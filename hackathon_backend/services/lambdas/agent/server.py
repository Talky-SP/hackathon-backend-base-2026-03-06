"""
WebSocket + REST API server for the AI CFO Agent.

Endpoints:
    WS  /ws/chat                     — WebSocket for real-time chat with streaming feedback
    POST /api/chat                   — REST endpoint for single question (no streaming)
    GET  /api/models                 — List available models
    GET  /api/health                 — Health check

Usage:
    python -m hackathon_backend.services.lambdas.agent.server
    python -m hackathon_backend.services.lambdas.agent.server --port 8080
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import os
import time
import uuid
from typing import Any

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from hackathon_backend.services.lambdas.agent.core.config import (
    init_all,
    AVAILABLE_MODELS,
)
from hackathon_backend.services.lambdas.agent.core.prompts import sync_prompts_to_langfuse
from hackathon_backend.services.lambdas.agent.core.classifier import classify_intent
from hackathon_backend.services.lambdas.agent.core.orchestrator import orchestrate
from hackathon_backend.services.lambdas.agent.core.query_agent import run_query_agent

from langfuse import observe, get_client as _get_langfuse_client

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="AI CFO Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_LOCATION_ID = "deloitte-84"
_initialized = False


def _ensure_init():
    global _initialized
    if not _initialized:
        print("Initializing models and Langfuse...")
        init_all()
        sync_prompts_to_langfuse()
        print(f"Models loaded: {list(AVAILABLE_MODELS.keys())}")
        _initialized = True


# ---------------------------------------------------------------------------
# REST models
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str
    location_id: str = DEFAULT_LOCATION_ID
    model: str = "claude-sonnet-4.5"
    classifier_model: str = "gpt-5-mini"


class ChatResponse(BaseModel):
    type: str
    answer: str
    chart: dict | None = None
    sources: list[dict] = []
    intent: str = "fast_chat"
    model_used: str = ""
    request_id: str = ""


# ---------------------------------------------------------------------------
# Pipeline (sync, runs in thread pool for async endpoints)
# ---------------------------------------------------------------------------
@observe(name="agent_pipeline")
def _run_pipeline(
    question: str,
    location_id: str,
    orchestrator_model: str,
    classifier_model: str,
    on_event=None,
) -> dict:
    """Full pipeline: classify -> orchestrate -> query agent."""
    emit = on_event or (lambda e, d: None)

    # Step 1: Classify
    emit("step", {"step": "classify", "message": "Clasificando intencion..."})
    intent = classify_intent(question, model_id=classifier_model)
    emit("intent", {"intent": intent})

    if intent == "complex_task":
        return {
            "type": "complex_task",
            "answer": (
                "Esta es una tarea compleja que requiere procesamiento en segundo plano. "
                "Por ahora, solo se soportan consultas rapidas (fast_chat)."
            ),
            "intent": intent,
            "chart": None,
            "sources": [],
            "model_used": classifier_model,
        }

    # Step 2: Orchestrate
    emit("step", {"step": "orchestrate", "message": "Analizando pregunta..."})
    orch_result = orchestrate(
        user_message=question,
        location_id=location_id,
        model_id=orchestrator_model,
    )

    if orch_result["type"] == "direct_answer":
        emit("step", {"step": "done", "message": "Respuesta directa"})
        return {
            "type": "direct_answer",
            "answer": orch_result["answer"],
            "chart": None,
            "sources": [],
            "intent": intent,
            "model_used": orchestrator_model,
        }

    # Step 3: Query agent
    data_requests = orch_result.get("data_requests", [])
    chart_suggestion = orch_result.get("chart_suggestion")
    emit("step", {"step": "query_agent", "message": f"Consultando datos ({len(data_requests)} solicitudes)...",
                   "data_requests": [{"table": r["table"], "description": r.get("description", "")} for r in data_requests]})

    agent_result = run_query_agent(
        user_question=question,
        data_requests=data_requests,
        location_id=location_id,
        model_id=orchestrator_model,
        chart_suggestion=chart_suggestion,
        on_event=on_event,
    )

    return {
        "type": "full_answer",
        "answer": agent_result["answer"],
        "chart": agent_result.get("chart"),
        "sources": agent_result.get("sources", []),
        "intent": intent,
        "model_used": orchestrator_model,
    }


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"status": "ok", "models_loaded": _initialized}


@app.get("/api/models")
async def list_models():
    _ensure_init()
    return {
        "models": [
            {"id": m, "provider": _get_provider(m)}
            for m in AVAILABLE_MODELS
        ],
        "default_orchestrator": "claude-sonnet-4.5",
        "default_classifier": "gpt-5-mini",
    }


def _get_provider(model_id: str) -> str:
    cfg = AVAILABLE_MODELS.get(model_id, {})
    model_str = cfg.get("model", "")
    if "vertex_ai" in model_str:
        return "Google Vertex AI"
    if "azure_ai" in model_str:
        return "Azure AI (Anthropic)"
    if "azure/" in model_str:
        return "Azure OpenAI"
    return "Unknown"


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    _ensure_init()
    request_id = str(uuid.uuid4())[:8]
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _run_pipeline(req.question, req.location_id, req.model, req.classifier_model),
    )
    result["request_id"] = request_id
    _get_langfuse_client().flush()
    return ChatResponse(**result)


# ---------------------------------------------------------------------------
# WebSocket endpoint — real-time streaming chat
# ---------------------------------------------------------------------------
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    _ensure_init()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            question = msg.get("question", "").strip()
            if not question:
                await ws.send_json({"type": "error", "message": "Missing 'question' field"})
                continue

            location_id = msg.get("location_id", DEFAULT_LOCATION_ID)
            model = msg.get("model", "claude-sonnet-4.5")
            classifier_model = msg.get("classifier_model", "gpt-5-mini")
            request_id = msg.get("request_id", str(uuid.uuid4())[:8])

            # Event queue for async->sync bridge
            event_queue: asyncio.Queue = asyncio.Queue()

            def on_event(event: str, data: dict):
                """Called from sync thread — puts events on the async queue."""
                try:
                    event_queue.put_nowait({"type": "event", "event": event, "request_id": request_id, **data})
                except Exception:
                    pass

            # Run pipeline in thread, forward events to WebSocket
            loop = asyncio.get_event_loop()

            async def run_and_send():
                # Start pipeline in background thread
                future = loop.run_in_executor(
                    None,
                    lambda: _run_pipeline(question, location_id, model, classifier_model, on_event=on_event),
                )

                # Forward events while pipeline runs
                while not future.done():
                    try:
                        event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                        await ws.send_json(event)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break

                # Drain remaining events
                while not event_queue.empty():
                    try:
                        event = event_queue.get_nowait()
                        await ws.send_json(event)
                    except Exception:
                        break

                # Get result and send final response
                result = await future
                result["request_id"] = request_id

                await ws.send_json({
                    "type": "result",
                    "request_id": request_id,
                    "data": {
                        "type": result.get("type", ""),
                        "answer": result.get("answer", ""),
                        "chart": result.get("chart"),
                        "sources": result.get("sources", []),
                        "intent": result.get("intent", ""),
                        "model_used": result.get("model_used", ""),
                    },
                })

                _get_langfuse_client().flush()

            await run_and_send()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="AI CFO Agent — WebSocket Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args()

    _ensure_init()

    print(f"\n{'='*60}")
    print(f"  AI CFO Agent Server")
    print(f"  WebSocket: ws://localhost:{args.port}/ws/chat")
    print(f"  REST API:  http://localhost:{args.port}/api/chat")
    print(f"  Models:    http://localhost:{args.port}/api/models")
    print(f"  Health:    http://localhost:{args.port}/api/health")
    print(f"  Docs:      http://localhost:{args.port}/docs")
    print(f"{'='*60}\n")

    uvicorn.run(
        "hackathon_backend.services.lambdas.agent.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
