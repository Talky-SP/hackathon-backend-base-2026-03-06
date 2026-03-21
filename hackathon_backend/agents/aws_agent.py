"""
AWSAgent — Two-phase data analyst agent for DynamoDB.

Phase 1 (function calling): Query DynamoDB via query_dynamodb tool
Phase 2 (codeExecution): Process/aggregate data with Gemini's native Python sandbox

Two phases because Gemini cannot combine codeExecution + function calling
in the same request.
"""

from __future__ import annotations

import json
import logging
import math
from decimal import Decimal
from typing import Any

import boto3

from hackathon_backend.agents.agent import AgentResult, ToolUseAgent
from hackathon_backend.agents.table_wiki import (
    TABLE_WIKI,
    get_wiki_text,
    resolve_table_name,
)
from hackathon_backend.agents.chart_tool import CHART_TOOL_INSTRUCTIONS, generate_chart

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source fields to extract from every item per table
# ---------------------------------------------------------------------------
_SOURCE_FIELDS: dict[str, list[str]] = {
    name: wiki.get("source_fields", [])
    for name, wiki in TABLE_WIKI.items()
}

# ---------------------------------------------------------------------------
# Tool schema for Phase 1
# ---------------------------------------------------------------------------
QUERY_DYNAMODB_SCHEMA = {
    "name": "query_dynamodb",
    "description": (
        "Execute a DynamoDB query or scan. Returns items as JSON. "
        "The userId is automatically scoped from the session — "
        "do NOT include userId in expression_attribute_values. "
        "ALWAYS use projection_expression to minimize returned data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "table_name": {
                "type": "string",
                "description": (
                    "Short table name without stage prefix, e.g. 'User_Expenses'. "
                    "Stage prefix is added automatically."
                ),
            },
            "method": {
                "type": "string",
                "enum": ["query", "scan"],
                "description": "Query method. Prefer 'query' over 'scan'.",
            },
            "index_name": {
                "type": "string",
                "description": "GSI name to use (null for base table).",
            },
            "key_condition_expression": {
                "type": "string",
                "description": "KeyConditionExpression string (required for query method).",
            },
            "filter_expression": {
                "type": "string",
                "description": "FilterExpression string (optional).",
            },
            "expression_attribute_values": {
                "type": "object",
                "description": "Expression attribute values dict, e.g. {':uid': 'deloitte-84'}.",
            },
            "expression_attribute_names": {
                "type": "object",
                "description": "Expression attribute names dict for reserved words, e.g. {'#s': 'status'}.",
            },
            "projection_expression": {
                "type": "string",
                "description": (
                    "Comma-separated list of attributes to return. "
                    "ALWAYS include source fields: categoryDate, supplier/client, "
                    "supplier_cif/client_cif, invoice_date, due_date, total, reconciled."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max items per page (DynamoDB Limit parameter).",
            },
            "scan_index_forward": {
                "type": "boolean",
                "description": "Sort order. false = descending (newest first).",
            },
        },
        "required": ["table_name", "method"],
    },
}


# ---------------------------------------------------------------------------
# Phase 1 system prompt
# ---------------------------------------------------------------------------
PHASE1_SYSTEM_PROMPT = """\
You are a DynamoDB data retrieval specialist for Talky, an AI CFO assistant.
You query financial data from DynamoDB tables for a specific user/tenant.

## MANDATORY QUERY PLANNING PROTOCOL

Before EVERY query_dynamodb call, you MUST think through these steps IN ORDER:

**Step 1 — IDENTIFY**: What entity does the user want?
  - Expenses? → User_Expenses table
  - Income invoices? → User_Invoice_Incomes table
  - Bank transactions? → Bank_Reconciliations table (PK=locationId, SK starts with MTXN#)
  - Payroll? → Payroll_Slips table
  - Provider/supplier list? → Providers table (small, ~50 items)
  - Customer list? → Customers table (small)
  What filter? (supplier name, CIF, date range, category, amount, status)
  What time period? (specific month, quarter, year, range, or all time)

**Step 2 — MAP FIELDS**: For each filter the user wants, determine:
  - Is it a PK, SK, or GSI key? → Use in KeyConditionExpression (fast, indexed)
  - Is it a regular attribute? → Use in FilterExpression (applied post-query, needs a broader query first)
  - Is it a NAME that needs CIF resolution? → Go to Step 3

**Step 3 — RESOLVE NAMES** (if user mentions a supplier/client by NAME, not CIF):
  a) FIRST query the **Providers** table: method=query, key_condition="locationId = :uid",
     filter_expression="contains(nombre, :fragment)",
     expression_attribute_values={{":fragment": "search term"}},
     projection_expression="cif, nombre"
     CRITICAL: The provider name field is 'nombre' (NOT 'name')!
  b) Extract the CIF(s) from results
  c) THEN query User_Expenses using **UserIdSupplierCifIndex** with supplier_cif = :cif
  d) If no provider found, report "no supplier matching that name was found" — do NOT guess CIFs
  IMPORTANT: Supplier names are mixed case (e.g. "Deloitte BPS, S.L.U."). Use contains() for partial match.

**Step 4 — CHOOSE STRATEGY**: Pick the optimal query approach:
  - Date range? → **UserIdInvoiceDateIndex** (sk=invoice_date, format YYYY-MM-DD)
  - Supplier CIF known? → **UserIdSupplierCifIndex** (sk=supplier_cif)
  - Client CIF known? → **UserIdClientCifIndex** (sk=client_cif, on User_Invoice_Incomes)
  - Category only? → Base table with begins_with(categoryDate, 'CATEGORY_NAME#')
  - Invoice ID? → **UserIdInvoiceIdIndex**
  - Reconciliation status? → **UserByReconStateDate** (begins_with 'R#' reconciled, 'U#' unreconciled)
  - P&L date range? → **UserIdPnlDateIndex**
  - Bank by month? → Base table with begins_with(SK, 'MTXN#YYYY-MM')
  - Bank by status? → **LocationByStatusDate** with begins_with(status_date, 'PENDING#' or 'MATCHED#')
  - Composite userId+supplier → **UserSupplierDateIndex** (pk={{userId}}#{{cif}}, sk=charge_date)
  - Need all expenses? → **UserIdInvoiceDateIndex** with wide date range

**Step 5 — EXECUTE**: Call query_dynamodb with the chosen table, index, and expressions.

## KEY RULES

1. **userId auto-injection**: The userId '{user_id}' is automatically injected as :uid. \
When the PK is userId or locationId, use ':uid' in your key_condition_expression. \
For composite PK GSIs (UserSupplierDateIndex), build the full value yourself: '{user_id}#{{cif}}'.
2. **Projection**: ALWAYS set projection_expression. Include source fields \
(userId/locationId, invoiceid, categoryDate, supplier, supplier_cif, invoice_date, due_date, total, reconciled) \
plus any fields needed for analysis.
3. **No scans on large tables**: NEVER scan User_Expenses or User_Invoice_Incomes. Always use a GSI or base table query. \
Small tables OK to scan: Providers (~50 items), Customers, Employees.
4. **categoryDate format**: {{CATEGORY}}#{{YYYY-MM-DD}}#{{HH:MM:SS.mmm}}#{{UUID}}. \
NEVER use '=' on categoryDate — always use begins_with(). \
Category filter: begins_with(categoryDate, 'COMPRAS#'). Category+year: begins_with(categoryDate, 'COMPRAS#2024').
5. **Empty results**: If 0 items, check GSI and date format. Try previous period once. \
If still empty, return the JSON handoff with data_for_processing=[], computation_plan=null, explain what was searched.
6. **Pagination**: Handled automatically. No action needed.

## COMMON PITFALLS

- **Providers vs Suppliers table**: Use **Providers** (has data). Suppliers is legacy (typically empty).
- **Case sensitivity**: CIFs are exact match (e.g. 'B83504761'). Names are mixed case — use contains() for partial match.
- **TEMP CIFs**: Some suppliers have temporary CIFs like 'TEMP-2E37B4AAAE7814BD'. These are valid.
- **Bank_Reconciliations**: PK is locationId (=userId). SK format: MTXN#YYYY-MM-DD#transactionId.
- **Categories**: Stored UPPERCASE in categoryDate: SERVICIOS PROFESIONALES, GASTOS GENERALES, I+D, COMPRAS, SUMINISTROS, etc.

## WHEN DONE QUERYING

Return your final answer as a **JSON code block**:

```json
{{{{
    "answer": "Human-readable answer in the SAME LANGUAGE as the user's question. Summarize findings clearly.",
    "data_for_processing": [... slim items with only fields needed for aggregation ...],
    "computation_plan": "describe computation, e.g. 'sum total grouped by supplier' or null if no aggregation needed",
    "sources": [... source references ...],
    "chart": {{{{"description": "what to chart", "type": "bar|line|pie|doughnut"}}}} or null
}}}}
```

ALWAYS include a helpful, specific "answer" — never "Data retrieved successfully". \
If the question can be answered directly from queried items, set computation_plan to null.

{chart_instructions}

## AVAILABLE TABLES AND SCHEMAS

{wiki_text}
"""


# ---------------------------------------------------------------------------
# Phase 2 prompt
# ---------------------------------------------------------------------------
PHASE2_PROMPT = """\
Here is financial data to process:

```json
{data_json}
```

Computation plan: {computation_plan}

Compute the requested metrics from the data above and return ONLY a single JSON object \
(inside a ```json code block) with these keys:
- "answer": human-readable answer in the SAME LANGUAGE as the computation plan (Spanish if plan is in Spanish)
- "metrics": {{"total_amount": ..., "item_count": ..., ...}} — numeric metrics
- "data": any structured breakdown (monthly, by supplier, etc.) or null

IMPORTANT:
- Round monetary amounts to 2 decimal places
- item_count should match the number of source items
- Return ONLY the JSON code block, nothing else
"""


class AWSAgent(ToolUseAgent):
    """Two-phase data analyst agent for DynamoDB financial data."""

    def __init__(
        self,
        user_id: str,
        stage: str = "dev",
        region: str = "eu-west-3",
        model_id: str = "gemini-2.5-flash",
        max_tool_calls: int = 15,
        completion_fn: Any | None = None,
    ):
        # Lazy import to avoid circular deps and allow standalone usage
        if completion_fn is None:
            from hackathon_backend.services.lambdas.agent.core.config import completion
            completion_fn = completion

        super().__init__(
            completion_fn=completion_fn,
            model_id=model_id,
            max_tool_calls=max_tool_calls,
            temperature=0.0,
        )
        self.user_id = user_id
        self.stage = stage
        self.region = region
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._all_sources: list[dict] = []
        self._chart_request: dict | None = None

        # Register the query_dynamodb tool
        self._register_tool(QUERY_DYNAMODB_SCHEMA, self._handle_query_dynamodb)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        return PHASE1_SYSTEM_PROMPT.format(
            user_id=self.user_id,
            wiki_text=get_wiki_text(),
            chart_instructions=CHART_TOOL_INSTRUCTIONS,
        )

    # ------------------------------------------------------------------
    # Tool handler: query_dynamodb
    # ------------------------------------------------------------------

    def _handle_query_dynamodb(
        self,
        table_name: str,
        method: str = "query",
        index_name: str | None = None,
        key_condition_expression: str | None = None,
        filter_expression: str | None = None,
        expression_attribute_values: dict | None = None,
        expression_attribute_names: dict | None = None,
        projection_expression: str | None = None,
        limit: int | None = None,
        scan_index_forward: bool = True,
    ) -> dict:
        """Execute DynamoDB query/scan with userId auto-injection."""
        resolved_name = resolve_table_name(table_name, self.stage)
        table = self._dynamodb.Table(resolved_name)

        # Auto-escape reserved words in projection, key condition, and filter
        ean = dict(expression_attribute_names or {})
        if projection_expression:
            projection_expression, ean = _escape_reserved_words(
                projection_expression, ean
            )
        if key_condition_expression:
            key_condition_expression, ean = _escape_reserved_words(
                key_condition_expression, ean
            )
        if filter_expression:
            filter_expression, ean = _escape_reserved_words(
                filter_expression, ean
            )

        # Build kwargs
        kwargs: dict[str, Any] = {}
        if index_name:
            kwargs["IndexName"] = index_name
        if key_condition_expression:
            kwargs["KeyConditionExpression"] = key_condition_expression
        if filter_expression:
            kwargs["FilterExpression"] = filter_expression
        if projection_expression:
            kwargs["ProjectionExpression"] = projection_expression
        if limit:
            kwargs["Limit"] = limit
        if not scan_index_forward:
            kwargs["ScanIndexForward"] = False

        # Expression attribute values with userId auto-injection
        eav = dict(expression_attribute_values or {})
        if ":uid" in str(key_condition_expression) or ":uid" in str(filter_expression):
            eav[":uid"] = self.user_id
        if eav:
            kwargs["ExpressionAttributeValues"] = eav

        if ean:
            kwargs["ExpressionAttributeNames"] = ean

        # Validate security: ensure query is scoped to user
        self._validate_user_scope(table_name, kwargs)

        logger.info(
            "query_dynamodb | %s %s.%s | index=%s",
            method, resolved_name, index_name or "base", index_name,
        )

        # Execute with pagination
        items: list[dict] = []
        try:
            if method == "query":
                response = table.query(**kwargs)
            else:
                response = table.scan(**kwargs)

            items.extend(response.get("Items", []))

            # Paginate (unless limit was explicitly set for "top N" queries)
            while "LastEvaluatedKey" in response and not limit:
                kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                if method == "query":
                    response = table.query(**kwargs)
                else:
                    response = table.scan(**kwargs)
                items.extend(response.get("Items", []))

        except Exception as exc:
            logger.error("DynamoDB error: %s", exc)
            return {"error": str(exc), "items": [], "count": 0, "sources": []}

        # Extract sources
        source_fields = _SOURCE_FIELDS.get(table_name, [])
        sources = []
        for item in items:
            source = {"_table": table_name}
            for sf in source_fields:
                if sf in item:
                    val = item[sf]
                    source[sf] = float(val) if isinstance(val, Decimal) else val
            if len(source) > 1:  # more than just _table
                sources.append(source)

        self._all_sources.extend(sources)

        # Convert Decimals for JSON serialization
        clean_items = _decimal_to_float(items)

        logger.info("query_dynamodb | returned %d items", len(clean_items))

        return {
            "items": clean_items,
            "count": len(clean_items),
            "sources": sources,
        }

    def _validate_user_scope(self, table_name: str, kwargs: dict) -> None:
        """Ensure the query is scoped to the session user."""
        wiki = TABLE_WIKI.get(table_name, {})
        pk_name = wiki.get("pk", {}).get("name", "")

        # For tables where PK is userId/locationId, check it's in the key condition
        if pk_name in ("userId", "locationId"):
            kce = kwargs.get("KeyConditionExpression", "")
            eav = kwargs.get("ExpressionAttributeValues", {})
            # Check if userId is present in expression values
            has_user = any(
                v == self.user_id for v in eav.values()
            )
            # Also check composite keys that include userId
            has_composite = any(
                isinstance(v, str) and self.user_id in v for v in eav.values()
            )
            if not has_user and not has_composite and kce:
                raise ValueError(
                    f"Security: query on {table_name} must be scoped to userId '{self.user_id}'"
                )

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    def run(self, user_request: str, context_messages: list[dict] | None = None) -> AgentResult:
        """Run the two-phase agent pipeline."""
        logger.info("AWSAgent.run | user=%s | request=%s", self.user_id, user_request[:120])
        self._trace = []
        self._all_sources = []
        self._chart_request = None

        # ---- Phase 1: Query via tool-use loop ----
        try:
            phase1_response = self._run_tool_loop(user_request, extra_messages=context_messages)
        except Exception as exc:
            logger.error("Phase 1 failed: %s", exc)
            return AgentResult(success=False, error=f"Phase 1 error: {exc}", trace=self._trace)

        if not phase1_response:
            return AgentResult(
                success=False,
                error="Phase 1 returned no response",
                trace=self._trace,
            )

        # Parse Phase 1 structured output
        handoff = self._parse_phase1_output(phase1_response)
        if handoff is None:
            # No structured JSON found — treat the text as a direct answer
            return AgentResult(
                success=True,
                data={
                    "answer": phase1_response,
                    "data": None,
                    "sources": self._all_sources,
                    "metrics": {"item_count": len(self._all_sources)},
                },
                iterations_used=self._tool_call_count,
                trace=self._trace,
            )

        self._trace.append({"phase": "phase1_handoff", "handoff_keys": list(handoff.keys())})

        # Capture chart request from Phase 1
        self._chart_request = handoff.get("chart")

        # Use sources from Phase 1 output or from accumulated tool calls
        sources = handoff.get("sources", self._all_sources)

        # ---- Phase 2: Code execution (if computation_plan is set) ----
        computation_plan = handoff.get("computation_plan")
        data_for_processing = handoff.get("data_for_processing", [])

        if not computation_plan:
            # No aggregation needed — return directly
            result = AgentResult(
                success=True,
                data={
                    "answer": handoff.get("answer", "Data retrieved successfully."),
                    "data": data_for_processing,
                    "sources": sources,
                    "metrics": {"item_count": len(sources)},
                },
                iterations_used=self._tool_call_count,
                trace=self._trace,
            )
            self._maybe_generate_chart(result)
            return result

        # Phase 2: run code execution
        phase2_result = self._run_phase2(data_for_processing, computation_plan)

        if phase2_result is None:
            return AgentResult(
                success=False,
                error="Phase 2 code execution returned no result",
                trace=self._trace,
            )

        # Sanity checks
        passed, phase2_result = self._sanity_check(phase2_result, sources)
        if not passed:
            # Retry Phase 2 once
            logger.warning("Sanity check failed, retrying Phase 2")
            self._trace.append({"phase": "sanity_check_retry"})
            phase2_result_retry = self._run_phase2(data_for_processing, computation_plan)
            if phase2_result_retry:
                passed, phase2_result_retry = self._sanity_check(phase2_result_retry, sources)
                if passed:
                    phase2_result = phase2_result_retry

        # Build final response
        final = {
            "answer": phase2_result.get("answer", ""),
            "data": phase2_result.get("data"),
            "sources": sources,
            "metrics": phase2_result.get("metrics", {}),
        }

        result = AgentResult(
            success=True,
            data=final,
            iterations_used=self._tool_call_count,
            trace=self._trace,
        )
        self._maybe_generate_chart(result)
        return result

    # ------------------------------------------------------------------
    # Chart generation (post-processing)
    # ------------------------------------------------------------------

    def _maybe_generate_chart(self, result: AgentResult) -> None:
        """Generate a Chart.js chart if Phase 1 requested one."""
        if not self._chart_request or not result.success or not result.data:
            return
        try:
            chart_desc = self._chart_request.get("description", "")
            if self._chart_request.get("type"):
                chart_desc = f"{self._chart_request['type']} chart: {chart_desc}"

            logger.info("AWSAgent | generating chart: %s", chart_desc[:100])

            # Build an LLM caller compatible with chart_tool.generate_chart
            def _llm_caller(messages: list[dict]) -> str:
                resp = self._completion_fn(
                    model_id=self._model_id,
                    messages=messages,
                    temperature=0.0,
                )
                return resp.choices[0].message.content or ""

            result.chart_html = generate_chart(
                data=result.data if isinstance(result.data, list) else result.data.get("data", result.data),
                chart_request=chart_desc,
                model=self._model_id,
                llm_caller=_llm_caller,
            )
            self._trace.append({"phase": "chart_generation", "status": "success"})
        except Exception as exc:
            logger.warning("AWSAgent | chart generation failed: %s", exc)
            self._trace.append({"phase": "chart_generation", "status": "error", "error": str(exc)})

    # ------------------------------------------------------------------
    # Phase 2: Code execution
    # ------------------------------------------------------------------

    def _run_phase2(self, data: list[dict], computation_plan: str) -> dict | None:
        """Run Phase 2 with Gemini codeExecution."""
        prompt = PHASE2_PROMPT.format(
            data_json=json.dumps(data, default=str),
            computation_plan=computation_plan,
        )

        self._trace.append({"phase": "phase2_start", "plan": computation_plan})

        try:
            response = self._completion_fn(
                model_id=self._model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
        except Exception as exc:
            logger.error("Phase 2 completion failed: %s", exc)
            self._trace.append({"phase": "phase2_error", "error": str(exc)})
            return None

        content = response.choices[0].message.content
        if not content:
            return None

        self._trace.append({"phase": "phase2_response", "length": len(content)})

        # Extract JSON from the response
        return self._extract_json(content)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_phase1_output(text: str) -> dict | None:
        """Extract structured JSON from Phase 1 LLM response."""
        # Try to find JSON in code blocks
        result = AWSAgent._extract_json(text)
        if result and ("data_for_processing" in result or "computation_plan" in result or "sources" in result):
            return result
        return None

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extract a JSON object from text, handling code blocks."""
        # Try code-block extraction first
        if "```" in text:
            blocks = text.split("```")
            for i in range(1, len(blocks), 2):
                block = blocks[i]
                # Remove language tag
                if block.startswith("json"):
                    block = block[4:]
                elif block.startswith("python"):
                    continue
                block = block.strip()
                try:
                    return json.loads(block)
                except (json.JSONDecodeError, ValueError):
                    continue

        # Try parsing the whole text
        text = text.strip()
        # Find the outermost { }
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        return None
        return None

    # ------------------------------------------------------------------
    # Sanity checks
    # ------------------------------------------------------------------

    def _sanity_check(self, result: dict, sources: list[dict]) -> tuple[bool, dict]:
        """Basic sanity checks on Phase 2 output. Returns (passed, result)."""
        metrics = result.get("metrics", {})
        passed = True

        # Check item_count matches sources
        if "item_count" in metrics and metrics["item_count"] != len(sources):
            logger.warning(
                "Sanity: item_count mismatch: metrics=%s, sources=%s",
                metrics["item_count"], len(sources),
            )
            # Auto-fix
            metrics["item_count"] = len(sources)

        # Check no NaN/None in numeric metrics
        for k, v in metrics.items():
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                logger.warning("Sanity: bad metric %s=%s", k, v)
                passed = False

        # Note: total_amount <= 0 is valid for credit notes and zero-data periods.
        # The NaN/Inf check above already catches truly bad values.

        result["metrics"] = metrics
        return passed, result

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def query(self, user_request: str) -> str:
        """Run and return JSON string."""
        result = self.run(user_request)
        return result.to_json()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decimal_to_float(obj: Any) -> Any:
    """Recursively convert Decimal to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_float(i) for i in obj]
    return obj


# Common DynamoDB reserved words that appear in Talky schemas
_RESERVED_WORDS = {
    "total", "status", "name", "data", "type", "date", "year", "month",
    "day", "comment", "key", "value", "count", "number", "index",
    "source", "role", "user", "action", "condition", "level", "state",
    "time", "timestamp", "zone", "language", "description", "location",
    "group", "order", "limit", "offset", "format", "path", "domain",
    "connection", "table", "schema", "partition", "range", "global",
    "local", "transaction", "session", "operation", "system", "account",
    "amount", "balance", "credit", "debit", "rate", "percent",
}


def _escape_reserved_words(
    expression: str,
    existing_names: dict[str, str],
) -> tuple[str, dict[str, str]]:
    """Replace DynamoDB reserved words in an expression with #-prefixed aliases.

    Works on ProjectionExpression, KeyConditionExpression, and FilterExpression.
    Returns (updated_expression, updated_expression_attribute_names).
    """
    import re
    names = dict(existing_names)

    # DynamoDB operators and function names that must NOT be escaped
    _DYNAMO_KEYWORDS = {
        "and", "or", "not", "between", "in", "is", "null",
        "begins_with", "contains", "attribute_exists",
        "attribute_not_exists", "attribute_type", "size",
        "set", "remove", "add", "delete", "if_not_exists",
        "list_append",
    }

    def replacer(match: re.Match) -> str:
        word = match.group(0)
        # Skip DynamoDB operators/functions (case-insensitive)
        if word.lower() in _DYNAMO_KEYWORDS:
            return word
        if word.lower() in _RESERVED_WORDS:
            alias = f"#{word}"
            names[alias] = word
            return alias
        return word

    # Match bare attribute names: word chars not preceded by # or :
    updated = re.sub(r'(?<![#:])(?<!\w)([a-zA-Z_]\w*)(?!\w)', replacer, expression)

    return updated, names
