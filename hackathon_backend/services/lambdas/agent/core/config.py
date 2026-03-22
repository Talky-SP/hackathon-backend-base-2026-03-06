"""
Configuration module: loads secrets from AWS Secrets Manager and sets up
LiteLLM model registry + Langfuse observability.
"""
import json
import os
from typing import Any

import boto3
import litellm
from langfuse import Langfuse

# Allow litellm to drop unsupported params (e.g. temperature for gpt-5)
litellm.drop_params = True

# ---------------------------------------------------------------------------
# AWS Secrets Manager
# ---------------------------------------------------------------------------
_secrets_cache: dict[str, dict] = {}

AWS_PROFILE = os.getenv("AWS_PROFILE", "")  # Empty in Lambda (uses IAM role), set locally
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_REGION_NAME", "eu-west-3"))

SECRET_NAMES = {
    "vertex_ai": "talky/vertex-ai",
    "gpt5_mini": "Azure/gpt-5-mini",
    "claude_sonnet": "Azure/claude-sonnet-4-5",
    "claude_opus": "Azure/claude-opus-4-6",
    "langfuse": "talky/langfuse/invoices-project",
}


def _get_sm_client():
    kwargs = {"region_name": AWS_REGION}
    if AWS_PROFILE:
        kwargs["profile_name"] = AWS_PROFILE
    session = boto3.Session(**kwargs)
    return session.client("secretsmanager")


def get_secret(key: str) -> dict[str, Any]:
    """Return parsed JSON secret, cached per key."""
    if key not in _secrets_cache:
        client = _get_sm_client()
        resp = client.get_secret_value(SecretId=SECRET_NAMES[key])
        _secrets_cache[key] = json.loads(resp["SecretString"])
    return _secrets_cache[key]


# ---------------------------------------------------------------------------
# Model registry — each entry maps a friendly model_id to the litellm params
# ---------------------------------------------------------------------------
AVAILABLE_MODELS: dict[str, dict] = {}


def _register_azure_openai_model(model_id: str, secret_key: str):
    """Register an Azure OpenAI model (GPT family) into our registry."""
    sec = get_secret(secret_key)
    AVAILABLE_MODELS[model_id] = {
        "model": f"azure/{sec['AZURE_DEPLOYMENT_NAME']}",
        "api_key": sec["AZURE_API_KEY"],
        "api_base": sec["AZURE_API_BASE"],
        "api_version": sec.get("AZURE_API_VERSION", ""),
    }


def _register_azure_anthropic_model(model_id: str, secret_key: str):
    """Register an Azure-hosted Anthropic model (Claude family) into our registry.
    These use Azure AI Services with Anthropic's native /messages API."""
    sec = get_secret(secret_key)
    # Strip the /anthropic/v1/messages suffix — litellm adds it
    api_base = sec["AZURE_API_BASE"]
    if "/anthropic/v1/messages" in api_base:
        api_base = api_base.replace("/anthropic/v1/messages", "")

    AVAILABLE_MODELS[model_id] = {
        "model": f"azure_ai/{sec['AZURE_DEPLOYMENT_NAME']}",
        "api_key": sec["AZURE_API_KEY"],
        "api_base": api_base,
    }


def _register_vertex_models():
    """Register Gemini models via Vertex AI using service-account JSON.

    Uses multi-location fallback: global → us-central1.
    Gemini 3.x preview models are typically available in global and us-central1.
    """
    sec = get_secret("vertex_ai")
    # Write the service-account JSON to a temp file for google-auth (ADC)
    sa_path = os.path.join(os.environ.get("TEMP", "/tmp"), "vertex_sa.json")
    with open(sa_path, "w") as f:
        json.dump(sec, f)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path

    project_id = sec["project_id"]

    # Gemini 3.0 Flash
    AVAILABLE_MODELS["gemini-3.0-flash"] = {
        "model": "vertex_ai/gemini-3-flash-preview",
        "vertex_project": project_id,
        "vertex_location": "global",
    }
    # Gemini 3.1 Pro
    AVAILABLE_MODELS["gemini-3.1-pro"] = {
        "model": "vertex_ai/gemini-3.1-pro-preview",
        "vertex_project": project_id,
        "vertex_location": "global",
    }

    # Fallback chains: try different locations, then fall back to older model
    _VERTEX_FALLBACK_CHAIN["gemini-3.0-flash"] = [
        {"model": "vertex_ai/gemini-3-flash-preview", "vertex_project": project_id, "vertex_location": "us-central1"},
        {"model": "vertex_ai/gemini-2.5-flash", "vertex_project": project_id, "vertex_location": "global"},
        {"model": "vertex_ai/gemini-2.5-flash", "vertex_project": project_id, "vertex_location": "us-central1"},
    ]
    _VERTEX_FALLBACK_CHAIN["gemini-3.1-pro"] = [
        {"model": "vertex_ai/gemini-3.1-pro-preview", "vertex_project": project_id, "vertex_location": "us-central1"},
        {"model": "vertex_ai/gemini-2.5-pro", "vertex_project": project_id, "vertex_location": "global"},
    ]


# Fallback chains per vertex model — list of alternative {model, location} configs
_VERTEX_FALLBACK_CHAIN: dict[str, list[dict]] = {}


def init_models():
    """Load all secrets and register every model. Call once at startup."""
    _register_vertex_models()
    _register_azure_openai_model("gpt-5-mini", "gpt5_mini")
    _register_azure_anthropic_model("claude-sonnet-4.5", "claude_sonnet")
    _register_azure_anthropic_model("claude-opus-4.6", "claude_opus")


# ---------------------------------------------------------------------------
# Langfuse
# ---------------------------------------------------------------------------
_langfuse: Langfuse | None = None


def init_langfuse() -> Langfuse:
    """Initialise the Langfuse client and wire it into LiteLLM callbacks."""
    global _langfuse
    sec = get_secret("langfuse")
    os.environ["LANGFUSE_PUBLIC_KEY"] = sec["public_key"]
    os.environ["LANGFUSE_SECRET_KEY"] = sec["secret_key"]
    os.environ["LANGFUSE_HOST"] = sec["host"]

    _langfuse = Langfuse(
        public_key=sec["public_key"],
        secret_key=sec["secret_key"],
        host=sec["host"],
    )

    # Langfuse v4 uses OpenTelemetry — the @observe decorator on our functions
    # handles tracing. LiteLLM calls are traced via the observe context.
    # No need to set litellm.success_callback (incompatible with langfuse v4).

    return _langfuse


def get_langfuse() -> Langfuse:
    if _langfuse is None:
        return init_langfuse()
    return _langfuse


# ---------------------------------------------------------------------------
# Unified completion helper
# ---------------------------------------------------------------------------
def completion(model_id: str, messages: list[dict], **kwargs) -> Any:
    """
    Call any registered model via LiteLLM.
    For Vertex AI models, tries fallback locations if the primary fails.
    Accepts the same kwargs as litellm.completion (tools, temperature, etc.)

    Prompt caching:
    - For Claude (azure_ai): adds cache_control to system messages automatically
    - For Gemini: uses context caching via LiteLLM headers
    """
    if model_id not in AVAILABLE_MODELS:
        raise ValueError(
            f"Unknown model '{model_id}'. Available: {list(AVAILABLE_MODELS.keys())}"
        )

    model_cfg = AVAILABLE_MODELS[model_id]

    # Apply prompt caching for Anthropic models (Claude)
    cached_messages = _apply_cache_control(model_id, messages)

    params = {**model_cfg, **kwargs, "messages": cached_messages}

    # For Vertex AI models, try primary config then fallback chain
    fallbacks = _VERTEX_FALLBACK_CHAIN.get(model_id, [])
    if not fallbacks:
        return litellm.completion(**params)

    try:
        return litellm.completion(**params)
    except Exception as primary_err:
        last_err = primary_err
        for fb_cfg in fallbacks:
            try:
                fallback_params = {**params, **fb_cfg}
                return litellm.completion(**fallback_params)
            except Exception as fb_err:
                last_err = fb_err
                continue
        raise last_err


def _apply_cache_control(model_id: str, messages: list[dict]) -> list[dict]:
    """
    Apply prompt caching to messages for supported models.

    Claude: Adds cache_control={"type": "ephemeral"} to system messages
    and the last user message that's large enough (>1024 tokens ~ 4096 chars).
    This tells Anthropic to cache that prefix, reducing costs by 90% on cache hits
    and 25% write premium on cache misses. Cache TTL is 5 minutes.

    Gemini: LiteLLM handles context caching automatically for Vertex AI.
    """
    if not model_id.startswith("claude"):
        return messages

    result = []
    for i, msg in enumerate(messages):
        msg_copy = dict(msg)

        # Cache system messages (they're repeated every call)
        if msg_copy.get("role") == "system":
            content = msg_copy.get("content", "")
            if isinstance(content, str) and len(content) > 500:
                # Convert to content blocks format for cache_control
                msg_copy["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            elif isinstance(content, list):
                # Already in blocks format — preserve existing cache_control,
                # ensure at least the last block has it
                blocks = [dict(b) for b in content]
                if blocks and "cache_control" not in blocks[-1]:
                    blocks[-1]["cache_control"] = {"type": "ephemeral"}
                msg_copy["content"] = blocks

        # Cache large conversation history prefixes (user messages before the last one)
        # Only cache if it's not the latest user message and it's big enough
        elif msg_copy.get("role") == "user" and i < len(messages) - 1:
            content = msg_copy.get("content", "")
            if isinstance(content, str) and len(content) > 4000:
                msg_copy["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]

        result.append(msg_copy)

    return result


# ---------------------------------------------------------------------------
# Traced completion — auto-records every LLM call to the trace store
# ---------------------------------------------------------------------------
def traced_completion(
    model_id: str,
    messages: list[dict],
    *,
    step: str = "llm_call",
    chat_id: str | None = None,
    task_id: str | None = None,
    location_id: str = "",
    parent_trace_id: str | None = None,
    trace_metadata: dict | None = None,
    **kwargs,
) -> Any:
    """
    Like completion(), but records a full trace (input/output/tool_calls/latency).
    Use this instead of completion() for traceable calls.
    """
    import logging as _logging
    import time as _time
    import uuid as _uuid

    _log = _logging.getLogger("hackathon_backend.traced")

    trace_id = str(_uuid.uuid4())
    t0 = _time.time()

    _log.info(f"[{step}] Starting LLM call: model={model_id}, msgs={len(messages)}")

    # Check if cancelled
    if chat_id and is_cancelled(chat_id):
        raise CancelledError(f"Chat {chat_id} was cancelled")
    if task_id and is_cancelled(task_id):
        raise CancelledError(f"Task {task_id} was cancelled")

    # Extract input for trace (compact: just last user message + tool count)
    input_summary = _extract_input_summary(messages, kwargs.get("tools"))

    try:
        response = completion(model_id, messages, **kwargs)

        elapsed_ms = int((_time.time() - t0) * 1000)
        _log.info(f"[{step}] Completed: {elapsed_ms}ms, model={model_id}")
        u = getattr(response, "usage", None)
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        completion_tokens = getattr(u, "completion_tokens", 0) or 0
        total_tokens = getattr(u, "total_tokens", 0) or 0

        choice = response.choices[0] if response.choices else None
        output_text = (choice.message.content or "")[:2000] if choice else ""

        # Extract tool calls
        tool_calls_data = []
        if choice and choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls_data.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments[:500],
                })

        # Record trace (lazy import to avoid circular)
        from hackathon_backend.services.lambdas.agent.core.chat_store import record_trace
        record_trace(
            step=step,
            location_id=location_id,
            trace_id=trace_id,
            chat_id=chat_id,
            task_id=task_id,
            parent_trace_id=parent_trace_id,
            model=model_id,
            provider=AVAILABLE_MODELS.get(model_id, {}).get("model", ""),
            input_data=input_summary,
            output_data={"text": output_text, "finish_reason": choice.finish_reason if choice else ""},
            tool_calls=tool_calls_data if tool_calls_data else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=elapsed_ms,
            status="ok",
            metadata=trace_metadata,
            started_at=t0,
            completed_at=_time.time(),
        )

        return response

    except CancelledError:
        raise
    except Exception as e:
        elapsed_ms = int((_time.time() - t0) * 1000)
        from hackathon_backend.services.lambdas.agent.core.chat_store import record_trace
        record_trace(
            step=step,
            location_id=location_id,
            trace_id=trace_id,
            chat_id=chat_id,
            task_id=task_id,
            parent_trace_id=parent_trace_id,
            model=model_id,
            input_data=input_summary,
            output_data={"error": str(e)},
            latency_ms=elapsed_ms,
            status="error",
            error=str(e),
            started_at=t0,
            completed_at=_time.time(),
        )
        raise


def _extract_input_summary(messages: list[dict], tools: list | None = None) -> dict:
    """Extract a compact summary of the input for trace recording."""
    summary: dict[str, Any] = {"message_count": len(messages)}
    if tools:
        summary["tool_count"] = len(tools)
    # Last user message (truncated)
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                summary["last_user_message"] = content[:500]
            break
    # System prompt (truncated)
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, str):
                summary["system_prompt_len"] = len(content)
            elif isinstance(content, list):
                summary["system_prompt_len"] = sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
            break
    return summary


# ---------------------------------------------------------------------------
# Cancellation registry — allows aborting in-progress operations
# ---------------------------------------------------------------------------
class CancelledError(Exception):
    """Raised when an operation is cancelled by the user."""
    pass


_cancelled: set[str] = set()


def request_cancel(operation_id: str):
    """Mark an operation (chat_id or task_id) as cancelled."""
    _cancelled.add(operation_id)


def is_cancelled(operation_id: str) -> bool:
    """Check if an operation has been cancelled."""
    return operation_id in _cancelled


def clear_cancel(operation_id: str):
    """Clear the cancel flag for an operation."""
    _cancelled.discard(operation_id)


# ---------------------------------------------------------------------------
# Bootstrap helper
# ---------------------------------------------------------------------------
def init_all():
    """One-call init for local dev / Lambda cold start."""
    init_models()
    init_langfuse()
