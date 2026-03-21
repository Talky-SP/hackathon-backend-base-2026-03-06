"""
Lambda entry point — dispatches between REST (Mangum) and WebSocket (API GW WS) events.

Environment variables:
    AGENT_TABLE_NAME     — DynamoDB single-table for agent data
    WS_CONNECTIONS_TABLE — DynamoDB table for WebSocket connections
    ARTIFACTS_BUCKET     — S3 bucket for Excel/PDF artifacts
    AWS_REGION           — AWS region
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("agent.lambda")
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_initialized = False


def _ensure_init():
    global _initialized
    if not _initialized:
        from hackathon_backend.services.lambdas.agent.core.config import init_all
        init_all()
        _initialized = True


def handler(event: dict, context) -> dict:
    """
    Single Lambda handler for both REST and WebSocket API Gateway events.

    Routing logic:
    - WebSocket: event.requestContext.routeKey in ($connect, $disconnect, $default)
    - REST/HTTP: everything else → Mangum wraps FastAPI
    """
    request_context = event.get("requestContext", {})
    route_key = request_context.get("routeKey", "")

    # WebSocket API Gateway events
    if route_key in ("$connect", "$disconnect", "$default"):
        _ensure_init()
        from hackathon_backend.services.lambdas.agent.ws_handler import handle_ws_event
        return handle_ws_event(event, context)

    # REST / HTTP API Gateway events → Mangum wraps FastAPI
    _ensure_init()
    from hackathon_backend.services.lambdas.agent.rest_handler import mangum_handler
    return mangum_handler(event, context)
