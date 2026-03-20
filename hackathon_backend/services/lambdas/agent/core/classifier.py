"""
Intent classifier — first LLM call that determines if the user's message
is a fast_chat (quick answer) or a complex_task (background processing).
"""
from __future__ import annotations

import json
from typing import Literal

from langfuse.decorators import observe

from hackathon_backend.services.lambdas.agent.core.config import completion
from hackathon_backend.services.lambdas.agent.core.prompts import get_prompt


IntentType = Literal["fast_chat", "complex_task"]


@observe(name="classify_intent")
def classify_intent(
    user_message: str,
    model_id: str = "gpt-5-mini",
) -> IntentType:
    """
    Classify the user's message intent.
    Uses a cheap/fast model by default (GPT-5-mini) since this is a simple classification.
    Returns "fast_chat" or "complex_task".
    """
    system_prompt = get_prompt("classifier_system")

    response = completion(
        model_id=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
        max_tokens=50,
    )

    raw = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(raw)
        intent = parsed.get("intent", "fast_chat")
    except (json.JSONDecodeError, AttributeError):
        # Fallback: look for keywords in the raw response
        if "complex_task" in raw.lower():
            intent = "complex_task"
        else:
            intent = "fast_chat"

    if intent not in ("fast_chat", "complex_task"):
        intent = "fast_chat"

    return intent
