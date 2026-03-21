"""
Chat server for testing AWSAgent (fast) + TaskAgent (deep) with SSE progress streaming.

Usage:
    python -m scripts.chat_server
    python -m scripts.chat_server --port 8080 --user deloitte-84
    python -m scripts.chat_server --mode deep   # always use TaskAgent
    python -m scripts.chat_server --mode auto   # classifier decides (default)
    python -m scripts.chat_server --mode fast   # always use AWSAgent

Then open http://localhost:8080 in your browser.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Globals set by main()
# ---------------------------------------------------------------------------
_fast_agent = None
_user_id = "deloitte-84"
_mode = "auto"
_stage = "dev"

STATIC_DIR = Path(__file__).parent / "chat_static"
EXPORT_DIR = Path(__file__).resolve().parent.parent / "test_output" / "task_exports"

# MIME types for static files
_MIME = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class ChatHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_file(STATIC_DIR / "index.html", "text/html")
        elif self.path == "/api/health":
            self._json_response({"status": "ok", "mode": _mode, "user": _user_id})
        elif self.path.startswith("/exports/"):
            # Serve generated export files
            fname = self.path[len("/exports/"):]
            fpath = EXPORT_DIR / fname
            ext = Path(fname).suffix
            self._serve_file(fpath, _MIME.get(ext, "application/octet-stream"))
        else:
            # Try to serve from static dir
            rel = self.path.lstrip("/")
            fpath = STATIC_DIR / rel
            ext = Path(rel).suffix
            if fpath.exists() and ext in _MIME:
                self._serve_file(fpath, _MIME[ext])
            else:
                self.send_error(404)

    def do_POST(self):
        if self.path == "/api/chat":
            self._handle_chat()
        elif self.path == "/api/chat/stream":
            self._handle_chat_stream()
        elif self.path == "/api/classify":
            self._handle_classify()
        else:
            self.send_error(404)

    @staticmethod
    def _build_context_messages(messages: list[dict]) -> list[dict] | None:
        """Build context messages from conversation history (last 10 messages)."""
        if not messages:
            return None
        # Limit to last 10 messages to prevent token overflow
        recent = messages[-10:]
        return [{"role": m["role"], "content": m["content"]} for m in recent if m.get("content")]

    def _handle_chat(self):
        """Synchronous chat — works with both AWSAgent and TaskAgent."""
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        message = body.get("message", "").strip()
        force_mode = body.get("mode", _mode)
        history = body.get("messages", [])

        if not message:
            self._json_response({"error": "Empty message"}, 400)
            return

        logger.info("Chat request [%s]: %s", force_mode, message[:120])
        context_messages = self._build_context_messages(history)

        start_time = time.time()
        try:
            agent = self._pick_agent(message, force_mode)
            result = agent.run(message, context_messages=context_messages)
            elapsed = time.time() - start_time
            response = {
                "success": result.success,
                "data": result.data,
                "error": result.error,
                "chart_html": result.chart_html,
                "iterations_used": result.iterations_used,
                "trace": result.trace,
                "mode": "deep" if hasattr(agent, '_worker_model_id') else "fast",
                "elapsed": round(elapsed, 1),
            }
        except Exception as exc:
            elapsed = time.time() - start_time
            logger.exception("Agent error")
            response = {"success": False, "error": str(exc), "data": None, "chart_html": None, "trace": []}

        logger.info("Chat response [%s] %.1fs", force_mode, elapsed)
        self._json_response(response)

    def _handle_chat_stream(self):
        """SSE-based streaming for TaskAgent progress updates."""
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        message = body.get("message", "").strip()
        history = body.get("messages", [])

        if not message:
            self._json_response({"error": "Empty message"}, 400)
            return

        logger.info("Stream request: %s", message[:120])
        context_messages = self._build_context_messages(history)

        # Set up SSE response
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        progress_q: queue.Queue = queue.Queue()

        def progress_cb(event: str, data: dict):
            progress_q.put({"type": "progress", "event": event, "data": data})

        from hackathon_backend.agents.task_agent import TaskAgent
        agent = TaskAgent(
            user_id=_user_id,
            stage=_stage,
            progress_callback=progress_cb,
            export_dir=str(EXPORT_DIR),
        )

        # Run agent in background thread
        result_holder = [None]
        error_holder = [None]

        def run_agent():
            try:
                result_holder[0] = agent.run(message, context_messages=context_messages)
            except Exception as exc:
                error_holder[0] = str(exc)

        t = threading.Thread(target=run_agent, daemon=True)
        t.start()

        # Stream progress events via SSE while agent runs
        try:
            while t.is_alive():
                try:
                    evt = progress_q.get(timeout=0.5)
                    self._send_sse(evt)
                except queue.Empty:
                    # Send keepalive comment
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()

            # Drain remaining events
            while not progress_q.empty():
                self._send_sse(progress_q.get_nowait())

            # Send final result
            if error_holder[0]:
                self._send_sse({
                    "type": "result",
                    "success": False,
                    "error": error_holder[0],
                    "trace": [],
                })
            else:
                result = result_holder[0]
                self._send_sse({
                    "type": "result",
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                    "chart_html": result.chart_html,
                    "iterations_used": result.iterations_used,
                    "trace": result.trace,
                })

            # Signal end
            self._send_sse({"type": "done"})

        except (BrokenPipeError, ConnectionResetError):
            logger.warning("Client disconnected during stream")

    def _send_sse(self, data: dict):
        payload = json.dumps(data, default=str)
        self.wfile.write(f"data: {payload}\n\n".encode())
        self.wfile.flush()

    def _pick_agent(self, message: str, mode: str):
        """Pick the right agent based on mode or classifier."""
        if mode == "fast":
            return _fast_agent

        if mode == "deep":
            from hackathon_backend.agents.task_agent import TaskAgent
            return TaskAgent(
                user_id=_user_id,
                stage=_stage,
                export_dir=str(EXPORT_DIR),
            )

        # auto mode: use classifier
        from hackathon_backend.services.lambdas.agent.core.classifier import classify_intent
        intent = classify_intent(message)
        logger.info("Classifier: %s", intent)

        if intent == "complex_task":
            from hackathon_backend.agents.task_agent import TaskAgent
            return TaskAgent(
                user_id=_user_id,
                stage=_stage,
                export_dir=str(EXPORT_DIR),
            )
        return _fast_agent

    def _handle_classify(self):
        """Classify intent: fast_chat or complex_task."""
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        message = body.get("message", "").strip()
        if not message:
            self._json_response({"intent": "fast_chat"})
            return
        try:
            from hackathon_backend.services.lambdas.agent.core.classifier import classify_intent
            intent = classify_intent(message)
            logger.info("Classify: %s -> %s", message[:60], intent)
            self._json_response({"intent": intent})
        except Exception as exc:
            logger.warning("Classify error: %s", exc)
            self._json_response({"intent": "fast_chat"})

    def _json_response(self, data: dict, status: int = 200):
        payload = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_file(self, path: Path, content_type: str):
        if not path.exists():
            self.send_error(404, f"{path.name} not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def main():
    global _fast_agent, _user_id, _mode, _stage

    parser = argparse.ArgumentParser(description="Talky chat test server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--user", default="deloitte-84", help="userId")
    parser.add_argument("--stage", default="dev")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Model for AWSAgent (fast mode)")
    parser.add_argument("--mode", choices=["auto", "fast", "deep"], default="auto",
                        help="Agent mode: auto (classifier), fast (AWSAgent only), deep (TaskAgent only)")
    args = parser.parse_args()

    _user_id = args.user
    _mode = args.mode
    _stage = args.stage

    # Init model registry
    from hackathon_backend.services.lambdas.agent.core.config import init_all
    init_all()

    from hackathon_backend.agents.aws_agent import AWSAgent
    _fast_agent = AWSAgent(user_id=args.user, stage=args.stage, model_id=args.model)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting chat server on http://localhost:%d  (user=%s, stage=%s, mode=%s, model=%s)",
        args.port, args.user, args.stage, args.mode, args.model,
    )

    class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    server = ThreadedHTTPServer(("0.0.0.0", args.port), ChatHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
