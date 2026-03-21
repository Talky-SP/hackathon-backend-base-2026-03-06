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
    request_cancel,
    clear_cancel,
    is_cancelled,
    CancelledError,
)
from hackathon_backend.services.lambdas.agent.core.prompts import sync_prompts_to_langfuse
from hackathon_backend.services.lambdas.agent.core.unified_agent import run_agent, detect_heavy_task
from hackathon_backend.services.lambdas.agent.core.chat_store import (
    create_chat, get_chat, list_chats, delete_chat, update_chat,
    add_message, get_messages, build_context_window,
    record_llm_cost, get_chat_costs, get_location_costs,
    get_chat_traces, get_task_traces, get_trace, get_location_traces,
)
from hackathon_backend.services.lambdas.agent.core.task_manager import (
    create_task, get_task, list_tasks, update_task_status, cancel_task,
    get_task_steps, TASK_TYPE_NAMES, TASK_COST_LIMITS,
)
from hackathon_backend.services.lambdas.agent.core.tools.excel_gen import list_artifacts, get_artifact_path
from hackathon_backend.services.lambdas.agent.core.code_runner import (
    run_code_execution, CODE_EXEC_SYSTEM, collect_sandbox_files, ARTIFACTS_DIR,
)

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
        _setup_dev_logging()
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
    chat_id: str | None = None  # None = create new chat


class ChatResponse(BaseModel):
    type: str
    answer: str
    chart: dict | None = None
    sources: list[dict] = []
    intent: str = "fast_chat"
    model_used: str = ""
    request_id: str = ""
    chat_id: str = ""
    message_id: int | None = None


# ---------------------------------------------------------------------------
# Pipeline (sync, runs in thread pool for async endpoints)
# ---------------------------------------------------------------------------
@observe(name="agent_pipeline")
def _run_pipeline(
    question: str,
    location_id: str,
    model: str = "claude-sonnet-4.5",
    conversation_history: list[dict] | None = None,
    on_event=None,
    chat_id: str | None = None,
    task_id: str | None = None,
    extra_system: str = "",
    attachments: list[dict] | None = None,
    chat_artifacts: list[dict] | None = None,
) -> dict:
    """Unified pipeline: single agent handles everything."""
    def emit(event, data):
        _dev_log_event(event, data)
        if on_event:
            on_event(event, data)

    # Check if this should be a background task
    heavy_task_type = detect_heavy_task(question)
    if heavy_task_type and not task_id:
        # Signal the caller to create a background task.
        emit("step", {"step": "detect", "message": f"Tarea compleja detectada: {heavy_task_type}"})
        return {
            "type": "complex_task",
            "answer": (
                f"Esta tarea requiere procesamiento en segundo plano. "
                f"Tipo detectado: {TASK_TYPE_NAMES.get(heavy_task_type, heavy_task_type)}."
            ),
            "task_type": heavy_task_type,
            "chart": None,
            "sources": [],
            "artifacts": [],
            "model_used": model,
            "usage": [],
        }

    emit("step", {"step": "agent", "message": "Procesando..."})

    agent_result = run_agent(
        user_message=question,
        location_id=location_id,
        model_id=model,
        conversation_history=conversation_history,
        on_event=on_event,
        chat_id=chat_id,
        task_id=task_id,
        extra_system=extra_system,
        attachments=attachments,
        chat_artifacts=chat_artifacts,
    )

    return {
        "type": "full_answer",
        "answer": agent_result.get("answer", ""),
        "chart": agent_result.get("chart"),
        "sources": agent_result.get("sources") or [],
        "artifacts": agent_result.get("artifacts", []),
        "model_used": model,
        "usage": agent_result.get("usage", []),
    }


def _collect_chat_artifacts(chat_id: str) -> list[dict]:
    """Collect all artifacts generated in previous messages of this chat."""
    msgs = get_messages(chat_id)
    artifacts = []
    for m in msgs:
        meta = m.get("metadata") or {}
        # Artifacts stored by assistant messages
        for a in meta.get("artifacts_list", []):
            artifacts.append(a)
    return artifacts


def _store_pipeline_costs(result: dict, chat_id: str, location_id: str, message_id: int | None = None):
    """Store all LLM usage records from a pipeline run."""
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

    # Resolve or create chat
    chat_id = req.chat_id
    if not chat_id:
        chat_data = create_chat(req.location_id, model=req.model)
        chat_id = chat_data["chat_id"]
    else:
        chat_data = get_chat(chat_id)
        if not chat_data:
            chat_data = create_chat(req.location_id, model=req.model)
            chat_id = chat_data["chat_id"]

    # Store user message
    add_message(chat_id, "user", req.question)

    # Build conversation context
    history = build_context_window(chat_id)
    # Remove the last user message (we pass it as the question)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _run_pipeline(
            req.question, req.location_id, req.model,
            conversation_history=history if history else None,
            chat_id=chat_id,
        ),
    )

    # Store assistant response
    msg = add_message(chat_id, "assistant", result.get("answer", ""), metadata={
        "type": result.get("type"),
        "chart": result.get("chart") is not None,
        "sources_count": len(result.get("sources") or []),
        "model": result.get("model_used"),
    })

    # Store LLM costs
    _store_pipeline_costs(result, chat_id, req.location_id, msg["id"])

    result["request_id"] = request_id
    result["chat_id"] = chat_id
    result["message_id"] = msg["id"]
    result.pop("usage", None)  # Don't expose raw usage in REST response
    _get_langfuse_client().flush()
    return ChatResponse(**result)


# ---------------------------------------------------------------------------
# Chat management endpoints
# ---------------------------------------------------------------------------
@app.get("/api/chats")
async def api_list_chats(location_id: str = DEFAULT_LOCATION_ID, limit: int = 50):
    """List all chats for a location."""
    _ensure_init()
    return {"chats": list_chats(location_id, limit=limit)}


@app.get("/api/chats/{chat_id}")
async def api_get_chat(chat_id: str):
    """Get chat metadata."""
    chat_data = get_chat(chat_id)
    if not chat_data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat_data


@app.get("/api/chats/{chat_id}/messages")
async def api_get_messages(chat_id: str, limit: int = 200):
    """Get all messages for a chat."""
    msgs = get_messages(chat_id, limit=limit)
    return {"chat_id": chat_id, "messages": msgs}


@app.delete("/api/chats/{chat_id}")
async def api_delete_chat(chat_id: str):
    """Delete a chat and all its messages."""
    deleted = delete_chat(chat_id)
    return {"deleted": deleted}


@app.patch("/api/chats/{chat_id}")
async def api_update_chat(chat_id: str, title: str | None = None, model: str | None = None):
    """Update chat title or model."""
    kwargs = {}
    if title is not None:
        kwargs["title"] = title
    if model is not None:
        kwargs["model"] = model
    updated = update_chat(chat_id, **kwargs)
    return {"updated": updated}


@app.post("/api/chats")
async def api_create_chat(location_id: str = DEFAULT_LOCATION_ID, model: str = "claude-sonnet-4.5"):
    """Create a new empty chat."""
    return create_chat(location_id, model=model)


# ---------------------------------------------------------------------------
# Cost & context endpoints
# ---------------------------------------------------------------------------
@app.get("/api/chats/{chat_id}/costs")
async def api_chat_costs(chat_id: str):
    """Get AI cost breakdown for a specific chat."""
    return get_chat_costs(chat_id)


@app.get("/api/chats/{chat_id}/context")
async def api_chat_context(chat_id: str):
    """Get the current context window that would be sent to the LLM."""
    _ensure_init()
    context = build_context_window(chat_id)
    total_chars = sum(len(m["content"]) for m in context)
    return {
        "chat_id": chat_id,
        "context_messages": len(context),
        "total_chars": total_chars,
        "messages": context,
    }


@app.get("/api/costs")
async def api_location_costs(location_id: str = DEFAULT_LOCATION_ID, days: int | None = None):
    """Get AI cost summary for a location (user). Optionally filter by last N days."""
    since = None
    if days:
        since = time.time() - (days * 86400)
    return get_location_costs(location_id, since=since)


@app.get("/api/costs/models")
async def api_model_pricing():
    """Get the pricing table used for cost estimation."""
    from hackathon_backend.services.lambdas.agent.core.chat_store import MODEL_PRICING
    return {
        "pricing_per_1m_tokens_usd": MODEL_PRICING,
        "note": "Costs are estimates based on public pricing. Actual costs may vary.",
    }


# ---------------------------------------------------------------------------
# AI Trace endpoints — full LLM call tracing (like Langfuse, self-hosted)
# ---------------------------------------------------------------------------
@app.get("/api/chats/{chat_id}/traces")
async def api_chat_traces(chat_id: str, limit: int = 200):
    """Get all AI traces for a chat — every LLM call with inputs, outputs, tokens, latency."""
    traces = get_chat_traces(chat_id, limit=limit)
    return {"chat_id": chat_id, "traces": traces, "count": len(traces)}


@app.get("/api/tasks/{task_id}/traces")
async def api_task_traces(task_id: str, limit: int = 200):
    """Get all AI traces for a task."""
    traces = get_task_traces(task_id, limit=limit)
    return {"task_id": task_id, "traces": traces, "count": len(traces)}


@app.get("/api/traces/{trace_id}")
async def api_get_trace(trace_id: str):
    """Get a single trace by ID with full details."""
    trace = get_trace(trace_id)
    if not trace:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@app.get("/api/traces")
async def api_location_traces(location_id: str = DEFAULT_LOCATION_ID, days: int | None = None, limit: int = 100):
    """Get aggregated trace stats for a location — total calls, tokens, costs, latency by model."""
    since = None
    if days:
        since = time.time() - (days * 86400)
    return get_location_traces(location_id, since=since, limit=limit)


# ---------------------------------------------------------------------------
# Cancel endpoints — abort in-progress chat or task operations
# ---------------------------------------------------------------------------
@app.post("/api/chats/{chat_id}/cancel")
async def api_cancel_chat(chat_id: str):
    """Cancel an in-progress chat operation. The current LLM call will stop at next iteration."""
    request_cancel(chat_id)
    return {"cancelled": True, "chat_id": chat_id}


@app.post("/api/tasks/{task_id}/cancel")
async def api_cancel_task_operation(task_id: str):
    """Cancel a running task. Sub-agents stop at the next iteration checkpoint."""
    request_cancel(task_id)
    cancel_task(task_id)
    return {"cancelled": True, "task_id": task_id}


# ---------------------------------------------------------------------------
# Task endpoints — async complex task management
# ---------------------------------------------------------------------------
# Track active WebSocket connections for task notifications
_task_ws_connections: dict[str, list[WebSocket]] = {}


class TaskRequest(BaseModel):
    task_type: str
    description: str = ""
    location_id: str = DEFAULT_LOCATION_ID
    chat_id: str | None = None
    model: str = "claude-sonnet-4.5"


@app.get("/api/tasks/types")
async def api_task_types():
    """List available task types with their cost budgets."""
    return {
        "types": [
            {
                "id": tid,
                "name": TASK_TYPE_NAMES.get(tid, tid),
                "cost_budget_usd": limits["max_usd"],
                "max_agents": limits["max_agents"],
                "timeout_s": limits["timeout_s"],
            }
            for tid, limits in TASK_COST_LIMITS.items()
        ]
    }


@app.post("/api/tasks")
async def api_create_task(req: TaskRequest):
    """Create and start a complex task. Runs in background."""
    _ensure_init()

    task = create_task(
        location_id=req.location_id,
        task_type=req.task_type,
        description=req.description,
        chat_id=req.chat_id,
    )
    task_id = task["task_id"]

    # Store user message in chat if chat_id provided
    if req.chat_id:
        add_message(req.chat_id, "user", req.description or f"[Tarea: {req.task_type}]")

    # Event callback that forwards to WebSocket connections
    def on_task_event(event: str, data: dict):
        pass  # For REST, events are polled via GET /api/tasks/{id}

    # Run task in background thread
    loop = asyncio.get_event_loop()
    from hackathon_backend.services.lambdas.agent.core.task_executor import _get_task_guidance
    task_guidance = _get_task_guidance(req.task_type)

    def _run_task():
        try:
            update_task_status(task_id, "RUNNING", progress=0)
            result = _run_pipeline(
                req.description or f"Ejecutar tarea: {req.task_type}",
                req.location_id, req.model,
                on_event=on_task_event,
                task_id=task_id,
                extra_system=task_guidance,
            )
            update_task_status(task_id, "COMPLETED", progress=100,
                               result_summary=result.get("answer", "")[:500])
        except Exception as e:
            update_task_status(task_id, "FAILED", error=str(e))

    loop.run_in_executor(None, _run_task)

    return task


@app.get("/api/tasks")
async def api_list_tasks(location_id: str = DEFAULT_LOCATION_ID, limit: int = 50):
    """List tasks for a location."""
    return {"tasks": list_tasks(location_id, limit=limit)}


@app.get("/api/tasks/{task_id}")
async def api_get_task(task_id: str):
    """Get task status, progress, steps, and artifacts."""
    task = get_task(task_id)
    if not task:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/api/tasks/{task_id}/steps")
async def api_get_task_steps(task_id: str):
    """Get detailed step-by-step trace for a task."""
    return {"task_id": task_id, "steps": get_task_steps(task_id)}


@app.get("/api/tasks/{task_id}/artifacts")
async def api_list_task_artifacts(task_id: str):
    """List downloadable artifacts for a task."""
    return {"task_id": task_id, "artifacts": list_artifacts(task_id)}


@app.get("/api/tasks/{task_id}/artifacts/{filename}")
async def api_download_artifact(task_id: str, filename: str):
    """Download a task artifact (Excel, PDF, etc.)."""
    from fastapi.responses import FileResponse
    path = get_artifact_path(task_id, filename)
    if not path:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(
        path=path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if filename.endswith(".xlsx") else "application/octet-stream",
    )


@app.delete("/api/tasks/{task_id}")
async def api_cancel_task(task_id: str):
    """Cancel a running task."""
    cancelled = cancel_task(task_id)
    return {"cancelled": cancelled}


@app.post("/api/tasks/{task_id}/upload")
async def api_upload_to_task(task_id: str):
    """Upload a file (PDF/image) for task processing."""
    from fastapi import UploadFile, File, HTTPException
    # This needs to be a separate function due to FastAPI file upload handling
    pass  # Implemented below


# Override with proper file upload signature
@app.post("/api/tasks/{task_id}/files")
async def api_upload_file(task_id: str, file: Any = None):
    """Upload a file for task processing. Use multipart/form-data."""
    from fastapi import HTTPException, Request
    from hackathon_backend.services.lambdas.agent.core.tools.pdf_reader import save_upload
    # For now, accept raw body
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Use /api/tasks with uploaded_files parameter", "task_id": task_id}


# ---------------------------------------------------------------------------
# Code execution endpoint — direct access to AI code execution sandbox
# ---------------------------------------------------------------------------
class CodeExecRequest(BaseModel):
    prompt: str
    model: str = "claude-sonnet-4.5"
    data: str = ""
    system_prompt: str | None = None
    task_id: str | None = None
    container_id: str | None = None


@app.post("/api/code-exec")
async def api_code_execution(req: CodeExecRequest):
    """
    Execute code in an AI sandbox (Claude or Gemini).

    The LLM writes and runs code (Python/Bash) in a secure sandboxed environment.
    Pre-installed: openpyxl, pandas, numpy, matplotlib, scipy, scikit-learn.

    Use this for:
    - Generating Excel reports dynamically
    - Running complex data analysis
    - Creating charts and visualizations
    - Any computation that benefits from code execution
    """
    _ensure_init()

    task_id = req.task_id or f"exec_{str(uuid.uuid4())[:8]}"

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: run_code_execution(
            prompt=req.prompt,
            model_id=req.model,
            data_context=req.data,
            task_id=task_id,
            container_id=req.container_id,
            system_prompt=req.system_prompt or CODE_EXEC_SYSTEM,
        ),
    )

    # Build artifact download URLs
    files = []
    for f in result.get("files", []):
        files.append({
            "filename": f["filename"],
            "size_bytes": f.get("size_bytes", 0),
            "type": f.get("type", "other"),
            "url": f"/api/tasks/{task_id}/artifacts/{f['filename']}",
        })

    return {
        "success": result.get("success", False),
        "text": result.get("text", ""),
        "code_blocks": result.get("code_blocks", []),
        "files": files,
        "container_id": result.get("container_id"),
        "task_id": task_id,
        "usage": result.get("usage", {}),
    }


# ---------------------------------------------------------------------------
# WebSocket endpoint — real-time streaming chat
# ---------------------------------------------------------------------------
@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket):
    await ws.accept()
    _ensure_init()

    # Track active operation IDs for this connection (for cancel support)
    _active_ops: set[str] = set()
    # Lock for concurrent WebSocket writes (multiple parallel queries)
    _ws_lock = asyncio.Lock()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            # Handle cancel command
            if msg.get("type") == "cancel":
                cancel_id = msg.get("chat_id") or msg.get("task_id")
                if cancel_id:
                    request_cancel(cancel_id)
                    await ws.send_json({"type": "cancelled", "id": cancel_id})
                continue

            question = msg.get("question", "").strip()
            if not question:
                await ws.send_json({"type": "error", "message": "Missing 'question' field"})
                continue

            location_id = msg.get("location_id", DEFAULT_LOCATION_ID)
            model = msg.get("model", "claude-sonnet-4.5")
            request_id = msg.get("request_id", str(uuid.uuid4())[:8])
            chat_id = msg.get("chat_id")

            # Parse attachments: [{filename, mime_type, data (base64)}]
            attachments = msg.get("attachments") or []

            # Resolve or create chat
            if not chat_id:
                chat_data = create_chat(location_id, model=model)
                chat_id = chat_data["chat_id"]
            else:
                chat_data = get_chat(chat_id)
                if not chat_data:
                    chat_data = create_chat(location_id, model=model)
                    chat_id = chat_data["chat_id"]

            # Store user message (with attachment filenames in metadata, not the data)
            user_meta = {}
            if attachments:
                user_meta["attachments"] = [
                    {"filename": a.get("filename", ""), "mime_type": a.get("mime_type", "")}
                    for a in attachments
                ]
            add_message(chat_id, "user", question, metadata=user_meta if user_meta else None)

            # Collect artifacts from previous messages in this chat
            chat_artifacts = _collect_chat_artifacts(chat_id)

            # Build conversation context (exclude the message we just added)
            history = build_context_window(chat_id)
            if history and history[-1]["role"] == "user":
                history = history[:-1]

            # Clear any previous cancel flag and track this operation
            clear_cancel(chat_id)
            _active_ops.add(chat_id)

            # Thread-safe send helper (prevents interleaved JSON on concurrent queries)
            async def _send(data: dict):
                async with _ws_lock:
                    await ws.send_json(data)

            # Send chat_id back immediately
            await _send({"type": "chat_id", "chat_id": chat_id, "request_id": request_id})

            # Capture all per-message state now (avoid closure-in-loop bug)
            _history = history if history else None
            _chat_id = chat_id
            _request_id = request_id
            _question = question
            _location_id = location_id
            _model = model
            _attachments = attachments if attachments else None
            _chat_artifacts = chat_artifacts if chat_artifacts else None

            # Event queue for async->sync bridge
            event_queue: asyncio.Queue = asyncio.Queue()

            def on_event(event: str, data: dict, _rid=_request_id):
                """Called from sync thread — puts events on the async queue."""
                try:
                    event_queue.put_nowait({"type": "event", "event": event, "request_id": _rid, **data})
                except Exception:
                    pass

            # Run pipeline in thread, forward events to WebSocket
            loop = asyncio.get_event_loop()

            async def run_and_send(
                _rid=_request_id, _cid=_chat_id, _q=_question,
                _loc=_location_id, _mdl=_model, _hist=_history,
                _eq=event_queue, _on_evt=on_event,
                _att=_attachments, _cart=_chat_artifacts,
            ):
                # Start pipeline in background thread
                future = loop.run_in_executor(
                    None,
                    lambda: _run_pipeline(
                        _q, _loc, _mdl,
                        conversation_history=_hist, on_event=_on_evt,
                        chat_id=_cid,
                        attachments=_att, chat_artifacts=_cart,
                    ),
                )

                # Forward events while pipeline runs
                while not future.done():
                    try:
                        event = await asyncio.wait_for(_eq.get(), timeout=0.1)
                        await _send(event)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break

                # Drain remaining events
                while not _eq.empty():
                    try:
                        event = _eq.get_nowait()
                        await _send(event)
                    except Exception:
                        break

                # Get result and send final response
                try:
                    result = await future
                except CancelledError:
                    clear_cancel(_cid)
                    _active_ops.discard(_cid)
                    await _send({
                        "type": "cancelled",
                        "request_id": _rid,
                        "chat_id": _cid,
                        "message": "Operacion cancelada",
                    })
                    return

                clear_cancel(_cid)
                _active_ops.discard(_cid)

                # Handle complex_task: create and run async task
                if result.get("type") == "complex_task":
                    task_type = result.get("task_type", "custom")
                    task = create_task(
                        location_id=_loc,
                        task_type=task_type,
                        description=_q,
                        chat_id=_cid,
                    )
                    task_id = task["task_id"]

                    # Store classifier costs
                    _store_pipeline_costs(result, _cid, _loc, None)

                    # Notify frontend about task creation
                    await _send({
                        "type": "task_created",
                        "request_id": _rid,
                        "chat_id": _cid,
                        "task_id": task_id,
                        "task_type": task_type,
                        "task_type_name": TASK_TYPE_NAMES.get(task_type, task_type),
                    })

                    # Run task in background, streaming events to WS
                    task_event_queue: asyncio.Queue = asyncio.Queue()

                    def on_task_event(event: str, data: dict, _r=_rid):
                        try:
                            task_event_queue.put_nowait({"type": event, "request_id": _r, **data})
                        except Exception:
                            pass

                    # Get task-specific guidance for the agent
                    from hackathon_backend.services.lambdas.agent.core.task_executor import _get_task_guidance
                    task_guidance = _get_task_guidance(task_type)

                    # Mark task as running
                    update_task_status(task_id, "RUNNING", progress=0)

                    def _run_task_agent():
                        try:
                            on_task_event("task_progress", {"task_id": task_id, "progress": 5, "step": "Iniciando agente..."})
                            result = _run_pipeline(
                                _q, _loc, _mdl,
                                conversation_history=_hist,
                                on_event=on_task_event,
                                chat_id=_cid,
                                task_id=task_id,
                                extra_system=task_guidance,
                                attachments=_att, chat_artifacts=_cart,
                            )
                            # Mark complete
                            answer = result.get("answer", "")
                            cost_info = check_budget(task_id) if True else {}
                            update_task_status(task_id, "COMPLETED", progress=100, result_summary=answer[:500])
                            on_task_event("task_completed", {
                                "task_id": task_id,
                                "summary": answer[:500],
                                "artifacts": result.get("artifacts", []),
                                "cost_usd": cost_info.get("cost_usd", 0),
                            })
                            return result
                        except CancelledError:
                            update_task_status(task_id, "CANCELLED")
                            on_task_event("task_cancelled", {"task_id": task_id})
                            return {"answer": "Tarea cancelada", "artifacts": [], "sources": []}
                        except Exception as e:
                            update_task_status(task_id, "FAILED", error=str(e))
                            on_task_event("task_failed", {"task_id": task_id, "error": str(e)})
                            return {"answer": "", "error": str(e), "artifacts": [], "sources": []}

                    from hackathon_backend.services.lambdas.agent.core.task_manager import check_budget

                    task_future = loop.run_in_executor(None, _run_task_agent)

                    # Forward task events to WebSocket
                    while not task_future.done():
                        try:
                            evt = await asyncio.wait_for(task_event_queue.get(), timeout=0.2)
                            await _send(evt)
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            break

                    # Drain remaining
                    while not task_event_queue.empty():
                        try:
                            evt = task_event_queue.get_nowait()
                            await _send(evt)
                        except Exception:
                            break

                    task_result = await task_future

                    # Store assistant message (include artifacts_list for edit-back)
                    answer = task_result.get("answer", result.get("answer", ""))
                    task_artifacts = task_result.get("artifacts") or []
                    task_meta = {
                        "type": "complex_task",
                        "task_id": task_id,
                        "task_type": task_type,
                        "artifacts": len(task_artifacts),
                    }
                    if task_artifacts:
                        task_meta["artifacts_list"] = [
                            {"filename": a.get("filename", ""), "task_id": a.get("task_id", ""), "url": a.get("url", "")}
                            for a in task_artifacts
                        ]
                    assistant_msg = add_message(_cid, "assistant", answer, metadata=task_meta)

                    # Send final result
                    await _send({
                        "type": "result",
                        "request_id": _rid,
                        "chat_id": _cid,
                        "message_id": assistant_msg["id"],
                        "data": {
                            "type": "complex_task",
                            "answer": answer,
                            "chart": task_result.get("chart"),
                            "sources": (task_result.get("sources") or [])[:20],
                            "model_used": _mdl,
                            "task_id": task_id,
                            "artifacts": [
                                {"filename": a.get("filename", ""), "url": a.get("url", f"/api/tasks/{task_id}/artifacts/{a.get('filename', '')}")}
                                for a in task_result.get("artifacts", [])
                            ],
                        },
                    })
                else:
                    # Normal fast_chat result
                    # Store assistant response (include artifacts_list for edit-back)
                    result_artifacts = result.get("artifacts") or []
                    assistant_meta = {
                        "type": result.get("type"),
                        "chart": result.get("chart") is not None,
                        "sources_count": len(result.get("sources") or []),
                        "model": result.get("model_used"),
                    }
                    if result_artifacts:
                        assistant_meta["artifacts_list"] = [
                            {"filename": a.get("filename", ""), "task_id": a.get("task_id", ""), "url": a.get("url", "")}
                            for a in result_artifacts
                        ]
                    assistant_msg = add_message(_cid, "assistant", result.get("answer", ""), metadata=assistant_meta)

                    # Store LLM costs
                    _store_pipeline_costs(result, _cid, _loc, assistant_msg["id"])

                    await _send({
                        "type": "response",
                        "request_id": _rid,
                        "chat_id": _cid,
                        "message_id": assistant_msg["id"],
                        "answer": result.get("answer", ""),
                        "chart": result.get("chart"),
                        "sources": result.get("sources") or [],
                        "artifacts": [
                            {"filename": a.get("filename", ""), "url": a.get("url", "")}
                            for a in (result.get("artifacts") or [])
                        ],
                        "model_used": result.get("model_used", ""),
                    })

                _get_langfuse_client().flush()

            # Run as background task so the WS loop can accept new messages
            # in parallel (multiple concurrent queries on the same connection)
            asyncio.create_task(run_and_send())

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Dev Logs — real-time log stream via WebSocket for debugging
# ---------------------------------------------------------------------------
import logging

# In-memory log buffer for dev panel (circular buffer, last 500 entries)
_log_buffer: list[dict] = []
_log_subscribers: list[asyncio.Queue] = []
_MAX_LOG_BUFFER = 500


class _WSLogHandler(logging.Handler):
    """Logging handler that broadcasts to WebSocket subscribers and buffers."""
    def emit(self, record):
        try:
            entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            _log_buffer.append(entry)
            if len(_log_buffer) > _MAX_LOG_BUFFER:
                _log_buffer.pop(0)
            for q in _log_subscribers:
                try:
                    q.put_nowait(entry)
                except Exception:
                    pass
        except Exception:
            pass


def _setup_dev_logging():
    """Attach the WS log handler to the root logger and key modules."""
    handler = _WSLogHandler()
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    handler.setLevel(logging.DEBUG)
    # Attach to root and key loggers
    for name in ("", "hackathon_backend", "litellm", "uvicorn"):
        logging.getLogger(name).addHandler(handler)


# Emit pipeline events to the dev log system too
_original_noop = lambda e, d: None


def _dev_log_event(event: str, data: dict):
    """Write pipeline events to the dev log stream."""
    entry = {
        "ts": time.time(),
        "level": "INFO",
        "logger": "pipeline",
        "message": f"[{event}] {data.get('message', data.get('step', json.dumps(data, default=str)[:200]))}",
        "event": event,
        "data": {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, type(None)))},
    }
    _log_buffer.append(entry)
    if len(_log_buffer) > _MAX_LOG_BUFFER:
        _log_buffer.pop(0)
    for q in _log_subscribers:
        try:
            q.put_nowait(entry)
        except Exception:
            pass


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    """Dev-only WebSocket: streams real-time logs for the debug panel.

    On connect, sends the last 100 buffered log entries, then streams new ones.
    """
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue()
    _log_subscribers.append(queue)

    try:
        # Send buffered history
        for entry in _log_buffer[-100:]:
            await ws.send_json(entry)

        # Stream new logs
        while True:
            entry = await queue.get()
            await ws.send_json(entry)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _log_subscribers.remove(queue)


@app.get("/api/logs")
async def api_get_logs(limit: int = 100):
    """Get recent log entries (for polling instead of WebSocket)."""
    return {"logs": _log_buffer[-limit:], "count": len(_log_buffer)}


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
