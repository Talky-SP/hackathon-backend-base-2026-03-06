"""
WebSocket handler for API Gateway WebSocket API.

Routes:
    $connect    — Store connectionId in DynamoDB
    $disconnect — Remove connectionId from DynamoDB
    $default    — Parse message, run agent pipeline, send responses via post_to_connection

Instead of ws.send_json(), we use ApiGatewayManagementApi.post_to_connection().
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

import boto3

logger = logging.getLogger("agent.ws_handler")

# DynamoDB table for WS connections
_WS_TABLE = os.environ.get("WS_CONNECTIONS_TABLE", "")
_REGION = os.environ.get("AWS_REGION", "eu-west-3")

# Lazy-init clients
_dynamodb = None
_apigw_mgmt = None


def _get_dynamo():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=_REGION)
    return _dynamodb


def _get_apigw_client(domain: str, stage: str):
    """Create ApiGatewayManagementApi client for sending messages back."""
    global _apigw_mgmt
    endpoint = f"https://{domain}/{stage}"
    _apigw_mgmt = boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=endpoint,
        region_name=_REGION,
    )
    return _apigw_mgmt


def _send_to_connection(connection_id: str, data: dict, apigw_client):
    """Send JSON data to a WebSocket connection."""
    try:
        apigw_client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
        )
    except apigw_client.exceptions.GoneException:
        # Connection closed — clean up
        _remove_connection(connection_id)
    except Exception as e:
        logger.warning(f"Failed to send to {connection_id}: {e}")


def _store_connection(connection_id: str, location_id: str = ""):
    """Store a new WebSocket connection in DynamoDB."""
    if not _WS_TABLE:
        return
    table = _get_dynamo().Table(_WS_TABLE)
    table.put_item(Item={
        "connectionId": connection_id,
        "locationId": location_id,
        "connectedAt": int(time.time()),
    })


def _remove_connection(connection_id: str):
    """Remove a WebSocket connection from DynamoDB."""
    if not _WS_TABLE:
        return
    table = _get_dynamo().Table(_WS_TABLE)
    try:
        table.delete_item(Key={"connectionId": connection_id})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------
def handle_ws_event(event: dict, context) -> dict:
    """Handle API Gateway WebSocket events."""
    rc = event["requestContext"]
    route = rc["routeKey"]
    connection_id = rc["connectionId"]
    domain = rc["domainName"]
    stage = rc["stage"]

    if route == "$connect":
        # Extract locationId from query string
        qs = event.get("queryStringParameters") or {}
        location_id = qs.get("location_id", qs.get("locationId", ""))
        _store_connection(connection_id, location_id)
        return {"statusCode": 200}

    if route == "$disconnect":
        _remove_connection(connection_id)
        return {"statusCode": 200}

    # $default — handle chat message
    apigw = _get_apigw_client(domain, stage)

    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        _send_to_connection(connection_id, {"type": "error", "message": "Invalid JSON"}, apigw)
        return {"statusCode": 200}

    # Handle cancel command
    if body.get("type") == "cancel":
        from hackathon_backend.services.lambdas.agent.core.config import request_cancel
        cancel_id = body.get("chat_id") or body.get("task_id")
        if cancel_id:
            request_cancel(cancel_id)
            _send_to_connection(connection_id, {"type": "cancelled", "id": cancel_id}, apigw)
        return {"statusCode": 200}

    question = body.get("question", "").strip()
    if not question:
        _send_to_connection(connection_id, {"type": "error", "message": "Missing 'question'"}, apigw)
        return {"statusCode": 200}

    return _handle_chat_message(body, question, connection_id, apigw)


def _handle_chat_message(body: dict, question: str, connection_id: str, apigw) -> dict:
    """Process a chat message and stream responses back via API GW Management API."""
    from hackathon_backend.services.lambdas.agent.core.config import (
        clear_cancel, CancelledError,
    )
    from hackathon_backend.services.lambdas.agent.core.chat_store import (
        create_chat, get_chat, add_message, build_context_window, record_llm_cost,
        get_messages,
    )
    from hackathon_backend.services.lambdas.agent.core.unified_agent import run_agent, detect_heavy_task
    from hackathon_backend.services.lambdas.agent.core.task_manager import (
        create_task, update_task_status, check_budget, TASK_TYPE_NAMES,
    )
    from hackathon_backend.services.lambdas.agent.core.task_executor import _get_task_guidance

    location_id = body.get("location_id", "deloitte-84")
    model = body.get("model", "claude-sonnet-4.5")
    request_id = body.get("request_id", str(uuid.uuid4())[:8])
    chat_id = body.get("chat_id")
    attachments = body.get("attachments") or []

    # Resolve or create chat
    if not chat_id:
        chat_data = create_chat(location_id, model=model)
        chat_id = chat_data["chat_id"]
    else:
        chat_data = get_chat(chat_id)
        if not chat_data:
            chat_data = create_chat(location_id, model=model)
            chat_id = chat_data["chat_id"]

    # Store user message
    user_meta = {}
    if attachments:
        user_meta["attachments"] = [
            {"filename": a.get("filename", ""), "mime_type": a.get("mime_type", "")}
            for a in attachments
        ]
    add_message(chat_id, "user", question, metadata=user_meta if user_meta else None)

    # Collect artifacts from previous messages
    chat_artifacts = _collect_chat_artifacts(chat_id)

    # Build conversation context
    history = build_context_window(chat_id)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    clear_cancel(chat_id)

    # Send chat_id back
    _send_to_connection(connection_id, {
        "type": "chat_id", "chat_id": chat_id, "request_id": request_id,
    }, apigw)

    # Event callback — sends events to WS connection
    def on_event(event: str, data: dict):
        _send_to_connection(connection_id, {
            "type": "event", "event": event, "request_id": request_id, **data,
        }, apigw)

    # Check for heavy task
    heavy_task_type = detect_heavy_task(question)
    if heavy_task_type:
        return _handle_heavy_task(
            heavy_task_type, question, location_id, model, chat_id,
            request_id, history, attachments, chat_artifacts, on_event,
            connection_id, apigw,
        )

    # Run agent pipeline
    try:
        on_event("step", {"step": "agent", "message": "Procesando..."})
        result = run_agent(
            user_message=question,
            location_id=location_id,
            model_id=model,
            conversation_history=history if history else None,
            on_event=on_event,
            chat_id=chat_id,
            attachments=attachments if attachments else None,
            chat_artifacts=chat_artifacts if chat_artifacts else None,
        )
    except CancelledError:
        clear_cancel(chat_id)
        _send_to_connection(connection_id, {
            "type": "cancelled", "request_id": request_id, "chat_id": chat_id,
        }, apigw)
        return {"statusCode": 200}

    clear_cancel(chat_id)

    # Store assistant response
    result_artifacts = result.get("artifacts") or []
    assistant_meta = {
        "type": result.get("type", "full_answer"),
        "model": model,
    }
    if result_artifacts:
        assistant_meta["artifacts_list"] = [
            {"filename": a.get("filename", ""), "task_id": a.get("task_id", ""), "url": a.get("url", "")}
            for a in result_artifacts
        ]
    assistant_msg = add_message(chat_id, "assistant", result.get("answer", ""), metadata=assistant_meta)

    # Store costs
    _store_pipeline_costs(result, chat_id, location_id, assistant_msg.get("id"))

    # Send final response
    _send_to_connection(connection_id, {
        "type": "response",
        "request_id": request_id,
        "chat_id": chat_id,
        "message_id": assistant_msg.get("id"),
        "answer": result.get("answer", ""),
        "chart": result.get("chart"),
        "sources": result.get("sources") or [],
        "artifacts": [
            {"filename": a.get("filename", ""), "url": a.get("url", "")}
            for a in result_artifacts
        ],
        "model_used": model,
    }, apigw)

    return {"statusCode": 200}


def _handle_heavy_task(
    task_type, question, location_id, model, chat_id,
    request_id, history, attachments, chat_artifacts, on_event,
    connection_id, apigw,
) -> dict:
    """Handle complex/heavy task that runs as a background task."""
    from hackathon_backend.services.lambdas.agent.core.config import CancelledError, clear_cancel
    from hackathon_backend.services.lambdas.agent.core.chat_store import add_message, record_llm_cost
    from hackathon_backend.services.lambdas.agent.core.unified_agent import run_agent
    from hackathon_backend.services.lambdas.agent.core.task_manager import (
        create_task, update_task_status, check_budget, TASK_TYPE_NAMES,
    )
    from hackathon_backend.services.lambdas.agent.core.task_executor import _get_task_guidance

    task = create_task(
        location_id=location_id,
        task_type=task_type,
        description=question,
        chat_id=chat_id,
    )
    task_id = task["task_id"]

    # Notify frontend
    _send_to_connection(connection_id, {
        "type": "task_created",
        "request_id": request_id,
        "chat_id": chat_id,
        "task_id": task_id,
        "task_type": task_type,
        "task_type_name": TASK_TYPE_NAMES.get(task_type, task_type),
    }, apigw)

    task_guidance = _get_task_guidance(task_type)
    update_task_status(task_id, "RUNNING", progress=0)

    def on_task_event(event: str, data: dict):
        _send_to_connection(connection_id, {
            "type": event, "request_id": request_id, **data,
        }, apigw)

    try:
        on_task_event("task_progress", {"task_id": task_id, "progress": 5, "step": "Iniciando agente..."})
        result = run_agent(
            user_message=question,
            location_id=location_id,
            model_id=model,
            conversation_history=history if history else None,
            on_event=on_task_event,
            chat_id=chat_id,
            task_id=task_id,
            extra_system=task_guidance,
            attachments=attachments if attachments else None,
            chat_artifacts=chat_artifacts if chat_artifacts else None,
        )
        answer = result.get("answer", "")
        update_task_status(task_id, "COMPLETED", progress=100, result_summary=answer[:500])
        on_task_event("task_completed", {
            "task_id": task_id,
            "summary": answer[:500],
            "artifacts": result.get("artifacts", []),
        })
    except CancelledError:
        update_task_status(task_id, "CANCELLED")
        on_task_event("task_cancelled", {"task_id": task_id})
        result = {"answer": "Tarea cancelada", "artifacts": [], "sources": []}
    except Exception as e:
        update_task_status(task_id, "FAILED", error=str(e))
        on_task_event("task_failed", {"task_id": task_id, "error": str(e)})
        result = {"answer": "", "error": str(e), "artifacts": [], "sources": []}

    clear_cancel(chat_id)

    # Store assistant message
    answer = result.get("answer", "")
    task_artifacts = result.get("artifacts") or []
    task_meta = {"type": "complex_task", "task_id": task_id, "task_type": task_type}
    if task_artifacts:
        task_meta["artifacts_list"] = [
            {"filename": a.get("filename", ""), "task_id": a.get("task_id", ""), "url": a.get("url", "")}
            for a in task_artifacts
        ]
    assistant_msg = add_message(chat_id, "assistant", answer, metadata=task_meta)

    # Store costs
    _store_pipeline_costs(result, chat_id, location_id, assistant_msg.get("id"))

    # Send final result
    _send_to_connection(connection_id, {
        "type": "result",
        "request_id": request_id,
        "chat_id": chat_id,
        "message_id": assistant_msg.get("id"),
        "data": {
            "type": "complex_task",
            "answer": answer,
            "chart": result.get("chart"),
            "sources": (result.get("sources") or [])[:20],
            "model_used": model,
            "task_id": task_id,
            "artifacts": [
                {"filename": a.get("filename", ""), "url": a.get("url", f"/api/tasks/{task_id}/artifacts/{a.get('filename', '')}")}
                for a in task_artifacts
            ],
        },
    }, apigw)

    return {"statusCode": 200}


def _collect_chat_artifacts(chat_id: str) -> list[dict]:
    """Collect all artifacts from previous messages in this chat."""
    from hackathon_backend.services.lambdas.agent.core.chat_store import get_messages
    msgs = get_messages(chat_id)
    artifacts = []
    for m in msgs:
        meta = m.get("metadata") or {}
        for a in meta.get("artifacts_list", []):
            artifacts.append(a)
    return artifacts


def _store_pipeline_costs(result: dict, chat_id: str, location_id: str, message_id=None):
    """Store all LLM usage records from a pipeline run."""
    from hackathon_backend.services.lambdas.agent.core.chat_store import record_llm_cost
    for usage in result.get("usage", []):
        cache_read = usage.get("cache_read_tokens", 0)
        cache_creation = usage.get("cache_creation_tokens", 0)
        cache_meta = {}
        if cache_read:
            cache_meta["cache_read_tokens"] = cache_read
        if cache_creation:
            cache_meta["cache_creation_tokens"] = cache_creation
        record_llm_cost(
            chat_id=chat_id,
            location_id=location_id,
            model=usage.get("model", "unknown"),
            step=usage.get("step", "unknown"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            message_id=message_id,
            metadata=cache_meta if cache_meta else None,
        )
