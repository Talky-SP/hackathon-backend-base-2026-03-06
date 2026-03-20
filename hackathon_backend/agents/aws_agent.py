from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key, Attr
from boto3.dynamodb.types import TypeDeserializer

from hackathon_backend.agents.agent import Agent, AgentResult
from hackathon_backend.agents.table_schema import get_all_schemas_description, TABLES

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a DynamoDB query specialist. You receive a natural-language request and
a set of table schemas, and you produce efficient DynamoDB queries.

Rules:
- Always prefer Query over Scan. Use Scan only when no key or GSI matches.
- Use GSIs when the request filters on a GSI partition key.
- Use KeyConditionExpression for partition/sort key filters.
- Use FilterExpression only for non-key attributes.
- Keep projected attributes minimal — only request what the user needs.
- Return VALID JSON that can be passed directly to boto3's Table resource methods.

Available DynamoDB tables and their schemas:
{schemas}
"""

QUERY_GEN_PROMPT = """\
User request: {request}

Generate exactly 3 different DynamoDB query strategies to fulfil this request.
For each strategy include:
- "method": "query" or "scan"
- "table_name": the resolved table name
- "kwargs": a dict of keyword arguments for boto3 Table.query() or Table.scan()
  (use string expressions, not boto3 Key/Attr objects)
- "rationale": why this approach is efficient or when it is appropriate
- "efficiency_score": 1-10 (10 = best)

Return a JSON object: {{ "strategies": [ {{...}}, {{...}}, {{...}} ] }}
Only return the JSON, no extra text.
"""

SELECTION_PROMPT = """\
Here are 3 query strategies:
{strategies}

Pick the strategy with the best balance of efficiency and correctness for the
original user request: "{request}"

Return a JSON object: {{ "selected_index": <0|1|2>, "reason": "..." }}
Only return the JSON, no extra text.
"""

REFINEMENT_PROMPT = """\
The previous query attempt did not return satisfactory results.
Previous result: {result}
Iteration: {iteration}/{max_iterations}

User request: {request}

Analyse what went wrong and produce a single improved query strategy.
Return a JSON object with keys: method, table_name, kwargs, rationale.
Only return the JSON, no extra text.
"""


class AWSAgent(Agent):
    """Agent specialized for querying AWS DynamoDB tables."""

    def __init__(
        self,
        stage: str = "dev",
        region: str = "eu-west-3",
        model: str = "anthropic/claude-sonnet-4-5-20250514",
        max_iterations: int = 8,
    ):
        super().__init__(model=model, max_iterations=max_iterations)
        self.stage = stage
        self.region = region
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._deserializer = TypeDeserializer()
        self._schemas_text = get_all_schemas_description(stage)
        self._current_request: str = ""
        self._selected_strategy: dict | None = None

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(schemas=self._schemas_text)

    def _llm_json(self, user_content: str) -> dict:
        """Call the LLM and parse the response as JSON."""
        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": user_content},
        ]
        raw = self._call_llm(messages)
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    def _plan(self, user_request: str) -> str:
        self._current_request = user_request
        logger.info("AWSAgent._plan | generating 3 query strategies")

        prompt = QUERY_GEN_PROMPT.format(request=user_request)
        result = self._llm_json(prompt)
        strategies = result.get("strategies", [])

        self._trace.append({"phase": "generate_strategies", "strategies": strategies})

        if not strategies:
            raise ValueError("LLM returned no strategies")

        # Ask LLM to pick the best one
        selection_prompt = SELECTION_PROMPT.format(
            strategies=json.dumps(strategies, indent=2),
            request=user_request,
        )
        selection = self._llm_json(selection_prompt)
        idx = selection.get("selected_index", 0)
        self._selected_strategy = strategies[idx]

        self._trace.append({
            "phase": "select_strategy",
            "selected_index": idx,
            "reason": selection.get("reason", ""),
            "strategy": self._selected_strategy,
        })

        logger.info(
            "AWSAgent._plan | selected strategy %d: %s",
            idx,
            self._selected_strategy.get("rationale", ""),
        )
        return json.dumps(self._selected_strategy)

    def _execute(self, plan: str) -> Any:
        strategy = json.loads(plan) if isinstance(plan, str) else plan
        method = strategy.get("method", "query")
        table_name = strategy["table_name"]
        kwargs = strategy.get("kwargs", {})

        logger.info("AWSAgent._execute | %s on %s", method, table_name)

        table = self._dynamodb.Table(table_name)

        # Resolve string expressions into boto3 condition objects
        kwargs = self._resolve_conditions(kwargs)

        if method == "query":
            response = table.query(**kwargs)
        else:
            response = table.scan(**kwargs)

        items = response.get("Items", [])

        # Handle pagination — collect all pages
        while "LastEvaluatedKey" in response:
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            if method == "query":
                response = table.query(**kwargs)
            else:
                response = table.scan(**kwargs)
            items.extend(response.get("Items", []))

        self._trace.append({
            "phase": "execute",
            "table": table_name,
            "method": method,
            "item_count": len(items),
        })

        logger.info("AWSAgent._execute | returned %d items", len(items))
        return items

    def _validate(self, result: Any) -> bool:
        if result is None:
            return False
        if isinstance(result, list) and len(result) == 0:
            logger.info("AWSAgent._validate | empty result set — will refine")
            return False
        return True

    def _refine(self, result: Any, iteration: int) -> str:
        logger.info("AWSAgent._refine | iteration %d", iteration)

        prompt = REFINEMENT_PROMPT.format(
            result=json.dumps(result, default=str)[:2000] if result else "null / error",
            iteration=iteration,
            max_iterations=self.max_iterations,
            request=self._current_request,
        )
        strategy = self._llm_json(prompt)
        self._selected_strategy = strategy
        self._trace.append({"phase": "refine", "iteration": iteration, "strategy": strategy})
        return json.dumps(strategy)

    # ------------------------------------------------------------------
    # Condition resolver
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_conditions(kwargs: dict) -> dict:
        """Convert string-based condition expressions from the LLM into
        the format boto3 Table resource expects.

        The LLM may return conditions in two styles:
        1. String expressions (KeyConditionExpression="userId = :uid") with
           ExpressionAttributeValues — these work directly with boto3.
        2. A structured dict — we convert to string form.

        We pass through whatever boto3 will accept.
        """
        resolved = {}
        for k, v in kwargs.items():
            resolved[k] = v
        return resolved

    # ------------------------------------------------------------------
    # Public convenience
    # ------------------------------------------------------------------

    def run(self, user_request: str) -> AgentResult:
        return super().run(user_request)

    def query(self, user_request: str) -> str:
        """Convenience method — returns JSON string directly."""
        result = self.run(user_request)
        return result.to_json()
