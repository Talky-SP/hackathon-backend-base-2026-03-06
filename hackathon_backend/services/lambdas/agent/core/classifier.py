"""
Intent classifier — first LLM call that determines if the user's message
is a fast_chat (quick answer) or a complex_task (background processing).
"""
from __future__ import annotations

import json
from typing import Literal

from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import traced_completion
from hackathon_backend.services.lambdas.agent.core.prompts import get_prompt


IntentType = Literal["fast_chat", "complex_task"]

# Maps complex_task sub-types to task_type for the task executor
COMPLEX_TASK_KEYWORDS: dict[str, list[str]] = {
    "cash_flow_forecast": ["prevision tesoreria", "prevision de tesoreria", "cash flow", "flujo de caja", "prevision de caja", "13 semanas", "forecast tesoreria", "previsión de tesorería", "previsión tesorería", "prevision caja", "tesoreria 13"],
    "pack_reporting": ["pack reporting", "reporting mensual", "p&l mensual", "cuenta resultados", "balance mensual"],
    "modelo_303": ["modelo 303", "iva trimestral", "liquidacion iva", "borrador 303"],
    "aging_analysis": ["aging", "antiguedad", "cobros pendientes", "deuda por antiguedad", "facturas vencidas"],
    "client_profitability": ["rentabilidad cliente", "rentabilidad por cliente", "margen por cliente"],
    "modelo_347": ["modelo 347", "terceros 3005", "declaracion terceros"],
    "three_way_matching": ["three way matching", "cruce tres vias", "albaranes facturas"],
}


# Patterns that indicate the user is asking a question, not requesting a task
_INFORMATIONAL_PREFIXES = [
    "que es", "qué es", "que son", "qué son", "como funciona", "cómo funciona",
    "como se calcula", "cómo se calcula", "explicame", "explícame", "dime que es",
    "que significa", "qué significa", "para que sirve", "para qué sirve",
    "what is", "how does", "explain",
]


def detect_task_type(user_message: str) -> str | None:
    """Detect specific complex task type from keywords.

    Returns None for informational questions (e.g. 'que es el modelo 303?')
    so they go through the LLM classifier as fast_chat.
    """
    msg_lower = user_message.lower()

    # If the message is an informational question, skip keyword detection
    for prefix in _INFORMATIONAL_PREFIXES:
        if msg_lower.startswith(prefix) or msg_lower.startswith("¿" + prefix):
            return None

    for task_type, keywords in COMPLEX_TASK_KEYWORDS.items():
        for kw in keywords:
            if kw in msg_lower:
                return task_type
    return None


@observe(name="classify_intent")
def classify_intent(
    user_message: str,
    model_id: str = "gpt-5-mini",
) -> tuple[IntentType, dict]:
    """
    Classify the user's message intent.

    Step 1: Keyword detection — if known complex task keywords match, skip LLM.
    Step 2: LLM classifier for ambiguous cases.

    Returns (intent, usage) where intent is "fast_chat" or "complex_task".
    """
    # Step 1: Fast keyword-based detection — no LLM needed
    task_type = detect_task_type(user_message)
    if task_type:
        return "complex_task", {"model": "keyword", "step": "classifier", "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    # Step 2: LLM classification for ambiguous cases
    system_prompt = get_prompt("classifier_system")

    response = traced_completion(
        model_id=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        step="classifier",
        temperature=0.0,
        max_tokens=50,
    )

    usage = _extract_usage(response, model_id, "classifier")
    raw = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(raw)
        intent = parsed.get("intent", "fast_chat")
    except (json.JSONDecodeError, AttributeError):
        if "complex_task" in raw.lower():
            intent = "complex_task"
        else:
            intent = "fast_chat"

    if intent not in ("fast_chat", "complex_task"):
        intent = "fast_chat"

    return intent, usage


def _extract_usage(response, model_id: str, step: str) -> dict:
    """Extract token usage from a LiteLLM response."""
    u = getattr(response, "usage", None)
    return {
        "model": model_id,
        "step": step,
        "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
        "total_tokens": getattr(u, "total_tokens", 0) or 0,
    }
