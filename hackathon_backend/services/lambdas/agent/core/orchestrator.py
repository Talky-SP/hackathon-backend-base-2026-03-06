"""
Orchestrator — the main fast-chat brain.

Flow:
1. Receives user message + locationId + selected model
2. Calls the LLM with the orchestrator system prompt + DB schemas + tools
3. If the LLM issues tool_calls (query_database), executes them
4. Feeds the results back and gets the final answer
5. Returns structured response with optional chart spec
"""
from __future__ import annotations

import json
from typing import Any

from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import completion
from hackathon_backend.services.lambdas.agent.core.prompts import get_prompt
from hackathon_backend.services.lambdas.agent.core.schemas import get_schemas_summary
from hackathon_backend.services.lambdas.agent.core.db_tools import TOOLS, execute_query


MAX_TOOL_ROUNDS = 5  # safety limit for tool-call loops


@observe(name="orchestrate_fast_chat")
def orchestrate(
    user_message: str,
    location_id: str,
    model_id: str = "claude-sonnet-4.5",
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Run the fast-chat orchestrator.

    Returns:
        {
            "answer": str,          # The final text response
            "chart": dict | None,   # Optional chart specification
            "tool_calls_made": int,  # How many DB queries were executed
            "model_used": str,
        }
    """
    system_prompt = get_prompt("orchestrator_system")
    db_context = get_schemas_summary()

    full_system = (
        f"{system_prompt}\n\n"
        f"CURRENT CONTEXT:\n"
        f"- locationId: {location_id}\n\n"
        f"DATABASE SCHEMAS REFERENCE:\n{db_context}"
    )

    # Build messages: system + optional history + current user message
    messages: list[dict] = [{"role": "system", "content": full_system}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    tool_calls_count = 0

    for _round in range(MAX_TOOL_ROUNDS):
        response = completion(
            model_id=model_id,
            messages=messages,
            tools=TOOLS,
            temperature=0.2,
        )

        choice = response.choices[0]

        # If no tool calls, we have the final answer
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            return _build_response(choice.message.content, tool_calls_count, model_id)

        # Process tool calls
        messages.append(choice.message)

        for tool_call in choice.message.tool_calls:
            if tool_call.function.name == "query_database":
                tool_calls_count += 1
                params = json.loads(tool_call.function.arguments)
                result = execute_query(params)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

    # If we exhausted rounds, get whatever the LLM has
    final_response = completion(
        model_id=model_id,
        messages=messages,
        temperature=0.2,
    )
    return _build_response(
        final_response.choices[0].message.content, tool_calls_count, model_id
    )


def _build_response(
    raw_content: str, tool_calls_count: int, model_id: str
) -> dict[str, Any]:
    """Parse the LLM's final response, extracting any chart spec."""
    chart = None
    answer = raw_content or ""

    # Try to extract a JSON chart block if the LLM included one
    if "```json" in answer:
        try:
            json_start = answer.index("```json") + 7
            json_end = answer.index("```", json_start)
            json_str = answer[json_start:json_end].strip()
            parsed = json.loads(json_str)
            if "chart" in parsed:
                chart = parsed["chart"]
                # Remove the JSON block from the text answer
                answer = (answer[:json_start - 7] + answer[json_end + 3:]).strip()
        except (ValueError, json.JSONDecodeError):
            pass

    # Also check if the whole response is JSON with answer + chart
    if not chart and answer.strip().startswith("{"):
        try:
            parsed = json.loads(answer)
            if isinstance(parsed, dict) and "answer" in parsed:
                answer = parsed["answer"]
                chart = parsed.get("chart")
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "answer": answer,
        "chart": chart,
        "tool_calls_made": tool_calls_count,
        "model_used": model_id,
    }
