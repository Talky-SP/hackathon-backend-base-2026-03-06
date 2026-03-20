"""
Integration layer — connects the orchestrator's data_requests with the
DynamoDB executor and feeds results back to generate the final answer.

Security: EVERY query is forced to include the locationId.
The db_executor layer enforces this at the query level.
"""
from __future__ import annotations

import json
from typing import Any

from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import completion


# ---------------------------------------------------------------------------
# Generate final answer with the fetched data
# ---------------------------------------------------------------------------
@observe(name="generate_final_answer")
def generate_answer(
    user_question: str,
    data_results: list[dict],
    model_id: str = "claude-sonnet-4.5",
    chart_suggestion: dict | None = None,
) -> dict[str, Any]:
    """
    Feed the fetched data back to the LLM to generate the final user-facing answer.
    """
    # Build a summary of the data for the LLM
    data_summary = []
    for r in data_results:
        entry = f"## {r['table']} — {r['description']}\n"
        if r.get("error"):
            entry += f"Error: {r['error']}\n"
        elif r["count"] == 0:
            entry += "No data found.\n"
        else:
            entry += f"Found {r['count']} items.\n"
            # Include the actual data (truncated if too large)
            items_json = json.dumps(r["items"][:50], ensure_ascii=False, default=str)
            if len(items_json) > 8000:
                items_json = items_json[:8000] + "\n... (truncated)"
            entry += f"Data:\n{items_json}\n"
        data_summary.append(entry)

    system_prompt = (
        "You are an expert AI CFO assistant. You have just received financial data "
        "from the company's databases in response to the user's question.\n\n"
        "RULES:\n"
        "1. Respond in the same language the user writes in.\n"
        "2. Be precise with numbers. Use EUR formatting and Spanish number format (1.234,56 €).\n"
        "3. Summarize the data clearly and concisely.\n"
        "4. If a chart was suggested, include it in your response as a JSON block:\n"
        '   ```json\n{"chart": {"type": "...", "title": "...", "data": [...]}}\n```\n'
        "5. Never invent data — only use what's provided below.\n"
    )

    if chart_suggestion and chart_suggestion.get("type") != "none":
        system_prompt += (
            f"\nA chart of type '{chart_suggestion['type']}' was suggested "
            f"with title '{chart_suggestion.get('title', '')}'.\n"
            "Generate the chart data from the results below.\n"
        )

    user_content = (
        f"USER QUESTION: {user_question}\n\n"
        f"DATA FROM DATABASES:\n\n{''.join(data_summary)}"
    )

    response = completion(
        model_id=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )

    answer = response.choices[0].message.content or ""

    # Try to extract chart from the response
    chart = None
    if "```json" in answer:
        try:
            json_start = answer.index("```json") + 7
            json_end = answer.index("```", json_start)
            json_str = answer[json_start:json_end].strip()
            parsed = json.loads(json_str)
            if "chart" in parsed:
                chart = parsed["chart"]
                answer = (answer[:json_start - 7] + answer[json_end + 3:]).strip()
        except (ValueError, json.JSONDecodeError):
            pass

    return {
        "answer": answer,
        "chart": chart,
    }
