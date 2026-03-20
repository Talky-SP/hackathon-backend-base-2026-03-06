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

# ---------------------------------------------------------------------------
# AWS Secrets Manager
# ---------------------------------------------------------------------------
_secrets_cache: dict[str, dict] = {}

AWS_PROFILE = os.getenv("AWS_PROFILE", "hackathon-equipo1")
AWS_REGION = os.getenv("AWS_REGION", "eu-west-3")

SECRET_NAMES = {
    "vertex_ai": "talky/vertex-ai",
    "gpt5_mini": "Azure/gpt-5-mini",
    "claude_sonnet": "Azure/claude-sonnet-4-5",
    "claude_opus": "Azure/claude-opus-4-6",
    "langfuse": "talky/langfuse/invoices-project",
}


def _get_sm_client():
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
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


def _register_azure_model(model_id: str, secret_key: str, litellm_provider: str = "azure"):
    """Register an Azure-hosted model into our registry."""
    sec = get_secret(secret_key)
    AVAILABLE_MODELS[model_id] = {
        "model": f"{litellm_provider}/{sec['AZURE_DEPLOYMENT_NAME']}",
        "api_key": sec["AZURE_API_KEY"],
        "api_base": sec["AZURE_API_BASE"],
        "api_version": sec.get("AZURE_API_VERSION", ""),
    }


def _register_vertex_model():
    """Register Gemini via Vertex AI using service-account JSON."""
    sec = get_secret("vertex_ai")
    # Write the service-account JSON to a temp file for google-auth
    sa_path = os.path.join(os.environ.get("TEMP", "/tmp"), "vertex_sa.json")
    with open(sa_path, "w") as f:
        json.dump(sec, f)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_path

    AVAILABLE_MODELS["gemini-2.0-flash"] = {
        "model": "vertex_ai/gemini-2.0-flash",
        "vertex_project": sec["project_id"],
        "vertex_location": "europe-west1",
    }


def init_models():
    """Load all secrets and register every model. Call once at startup."""
    _register_vertex_model()
    _register_azure_model("gpt-5-mini", "gpt5_mini")
    _register_azure_model("claude-sonnet-4.5", "claude_sonnet")
    _register_azure_model("claude-opus-4.6", "claude_opus")


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

    # Wire Langfuse callback into LiteLLM so every completion is traced
    litellm.success_callback = ["langfuse"]
    litellm.failure_callback = ["langfuse"]

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
    Accepts the same kwargs as litellm.completion (tools, temperature, etc.)
    """
    if model_id not in AVAILABLE_MODELS:
        raise ValueError(
            f"Unknown model '{model_id}'. Available: {list(AVAILABLE_MODELS.keys())}"
        )
    params = {**AVAILABLE_MODELS[model_id], **kwargs, "messages": messages}
    return litellm.completion(**params)


# ---------------------------------------------------------------------------
# Bootstrap helper
# ---------------------------------------------------------------------------
def init_all():
    """One-call init for local dev / Lambda cold start."""
    init_models()
    init_langfuse()
