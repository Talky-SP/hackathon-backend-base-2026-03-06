"""
Orchestrator — the main fast-chat brain.

Flow:
1. Receives user message + locationId + selected model
2. Calls the LLM with the orchestrator system prompt + DB schemas + tools
3. If the LLM can answer directly → returns the answer
4. If the LLM calls `fetch_financial_data` → returns a data request
   for an external database agent to handle
"""
from __future__ import annotations

import json
from typing import Any

from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import completion
from hackathon_backend.services.lambdas.agent.core.prompts import get_prompt
from hackathon_backend.services.lambdas.agent.core.schemas import get_schemas_summary
from hackathon_backend.services.lambdas.agent.core.db_tools import TOOLS


@observe(name="orchestrate_fast_chat")
def orchestrate(
    user_message: str,
    location_id: str,
    model_id: str = "claude-sonnet-4.5",
    conversation_history: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Run the fast-chat orchestrator.

    Returns one of two response types:

    1. Direct answer (no data needed):
        {
            "type": "direct_answer",
            "answer": str,
            "chart": dict | None,
            "model_used": str,
        }

    2. Needs data (delegate to DB agent):
        {
            "type": "needs_data",
            "user_question": str,
            "data_requests": [...],
            "chart_suggestion": dict | None,
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

    messages: list[dict] = [{"role": "system", "content": full_system}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    response = completion(
        model_id=model_id,
        messages=messages,
        tools=TOOLS,
        temperature=0.2,
    )

    choice = response.choices[0]

    # MODE 1: Direct answer — no tool calls
    if choice.finish_reason == "stop" or not choice.message.tool_calls:
        return {
            "type": "direct_answer",
            "answer": choice.message.content or "",
            "chart": None,
            "model_used": model_id,
        }

    # MODE 2: Needs data — extract the fetch_financial_data call(s)
    data_requests = []
    chart_suggestion = None

    for tool_call in choice.message.tool_calls:
        if tool_call.function.name == "fetch_financial_data":
            args = json.loads(tool_call.function.arguments)
            data_requests.extend(args.get("data_needed", []))
            if args.get("chart_suggestion"):
                chart_suggestion = args["chart_suggestion"]

    return {
        "type": "needs_data",
        "user_question": user_message,
        "data_requests": data_requests,
        "chart_suggestion": chart_suggestion,
        "model_used": model_id,
    }
