"""
Chat store — manages conversation history with context windowing.

Architecture:
- Local: SQLite for dev/testing (this file)
- Production: DynamoDB (PK=locationId, SK=CHAT#{chatId}) + S3 for full history

Each chat has:
- chat_id: unique identifier
- location_id: tenant isolation
- messages: list of {role, content, timestamp, metadata}
- title: auto-generated from first question
- model: preferred model for this chat
- created_at, updated_at

Context window strategy:
- Keep full history in storage
- When building LLM context, use a sliding window:
  1. Always include the system prompt
  2. Include a summary of older messages (if any)
  3. Include the last N message pairs verbatim
  4. Stay under MAX_CONTEXT_MESSAGES
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_CONTEXT_MESSAGES = 20  # Max message pairs to send to LLM
MAX_CONTEXT_CHARS = 30000  # Max chars of conversation context
SUMMARY_THRESHOLD = 10  # After this many messages, summarize older ones

DB_PATH = os.path.join(
    os.environ.get("TEMP", "/tmp"),
    "cfo_agent_chats.db",
)


# ---------------------------------------------------------------------------
# SQLite setup
# ---------------------------------------------------------------------------
def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id TEXT PRIMARY KEY,
            location_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            model TEXT DEFAULT 'claude-sonnet-4.5',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            metadata TEXT DEFAULT '{}',
            FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chats_location ON chats(location_id, updated_at)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            message_id INTEGER,
            location_id TEXT NOT NULL,
            model TEXT NOT NULL,
            step TEXT NOT NULL DEFAULT 'unknown',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0.0,
            timestamp REAL NOT NULL,
            metadata TEXT DEFAULT '{}',
            FOREIGN KEY (chat_id) REFERENCES chats(chat_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_costs_chat ON llm_costs(chat_id, timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_costs_location ON llm_costs(location_id, timestamp)
    """)
    # --- AI Trace Store ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id TEXT NOT NULL,
            chat_id TEXT,
            task_id TEXT,
            location_id TEXT NOT NULL,
            parent_trace_id TEXT,
            step TEXT NOT NULL,
            model TEXT,
            provider TEXT,
            input TEXT NOT NULL DEFAULT '{}',
            output TEXT NOT NULL DEFAULT '{}',
            tool_calls TEXT DEFAULT '[]',
            tool_results TEXT DEFAULT '[]',
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            latency_ms INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error TEXT,
            metadata TEXT DEFAULT '{}',
            started_at REAL NOT NULL,
            completed_at REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_chat ON ai_traces(chat_id, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_task ON ai_traces(task_id, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_location ON ai_traces(location_id, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_trace_id ON ai_traces(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_parent ON ai_traces(parent_trace_id)")
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Chat CRUD
# ---------------------------------------------------------------------------
def create_chat(location_id: str, model: str = "claude-sonnet-4.5", title: str = "") -> dict:
    """Create a new chat session."""
    chat_id = str(uuid.uuid4())
    now = time.time()
    db = _get_db()
    db.execute(
        "INSERT INTO chats (chat_id, location_id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, location_id, title, model, now, now),
    )
    db.commit()
    db.close()
    return {"chat_id": chat_id, "location_id": location_id, "title": title, "model": model,
            "created_at": now, "updated_at": now, "message_count": 0}


def get_chat(chat_id: str) -> dict | None:
    """Get chat metadata."""
    db = _get_db()
    row = db.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    if not row:
        db.close()
        return None
    msg_count = db.execute("SELECT COUNT(*) as cnt FROM messages WHERE chat_id = ?", (chat_id,)).fetchone()["cnt"]
    db.close()
    return {**dict(row), "message_count": msg_count}


def list_chats(location_id: str, limit: int = 50) -> list[dict]:
    """List chats for a location, newest first."""
    db = _get_db()
    rows = db.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.chat_id) as message_count "
        "FROM chats c WHERE c.location_id = ? ORDER BY c.updated_at DESC LIMIT ?",
        (location_id, limit),
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def delete_chat(chat_id: str) -> bool:
    """Delete a chat and all its messages."""
    db = _get_db()
    db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    result = db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
    db.commit()
    deleted = result.rowcount > 0
    db.close()
    return deleted


def update_chat(chat_id: str, **kwargs) -> bool:
    """Update chat fields (title, model)."""
    allowed = {"title", "model"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [chat_id]
    db = _get_db()
    db.execute(f"UPDATE chats SET {set_clause} WHERE chat_id = ?", values)
    db.commit()
    db.close()
    return True


# ---------------------------------------------------------------------------
# Message CRUD
# ---------------------------------------------------------------------------
def add_message(chat_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
    """Add a message to a chat."""
    now = time.time()
    meta_str = json.dumps(metadata or {}, ensure_ascii=False, default=str)
    db = _get_db()
    cursor = db.execute(
        "INSERT INTO messages (chat_id, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
        (chat_id, role, content, now, meta_str),
    )
    db.execute("UPDATE chats SET updated_at = ? WHERE chat_id = ?", (now, chat_id))

    # Auto-title: set title from first user message
    if role == "user":
        chat = db.execute("SELECT title FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        if chat and not chat["title"]:
            title = content[:80] + ("..." if len(content) > 80 else "")
            db.execute("UPDATE chats SET title = ? WHERE chat_id = ?", (title, chat_id))

    db.commit()
    msg_id = cursor.lastrowid
    db.close()
    return {"id": msg_id, "chat_id": chat_id, "role": role, "content": content,
            "timestamp": now, "metadata": metadata or {}}


def get_messages(chat_id: str, limit: int = 200) -> list[dict]:
    """Get all messages for a chat, ordered by timestamp."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp ASC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    db.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# LLM Cost tracking
# ---------------------------------------------------------------------------
# Approximate cost per 1M tokens (USD) — updated March 2026
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gemini-3.0-flash":  {"input": 0.10, "output": 0.40},
    "gemini-3.1-pro":    {"input": 1.25, "output": 5.00},
    "gpt-5-mini":        {"input": 0.15, "output": 0.60},
    "claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
    "claude-opus-4.6":   {"input": 15.00, "output": 75.00},
}


def _estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate USD cost from token counts, accounting for Anthropic cache pricing.

    Cache read: 90% discount (0.1x input price)
    Cache write: 25% premium (1.25x input price)
    Non-cached input: standard price
    """
    pricing = MODEL_PRICING.get(model, {"input": 1.0, "output": 5.0})
    input_price = pricing["input"]

    # Separate cached vs non-cached input tokens
    non_cached_input = prompt_tokens - cache_read_tokens - cache_creation_tokens
    if non_cached_input < 0:
        non_cached_input = 0

    input_cost = (
        non_cached_input * input_price
        + cache_read_tokens * input_price * 0.1       # 90% discount
        + cache_creation_tokens * input_price * 1.25   # 25% premium
    ) / 1_000_000
    output_cost = completion_tokens * pricing["output"] / 1_000_000

    return round(input_cost + output_cost, 6)


def record_llm_cost(
    chat_id: str,
    location_id: str,
    model: str,
    step: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int | None = None,
    cost_usd: float | None = None,
    message_id: int | None = None,
    metadata: dict | None = None,
) -> dict:
    """Record a single LLM call's cost."""
    now = time.time()
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
    if cost_usd is None:
        cost_usd = _estimate_cost(model, prompt_tokens, completion_tokens)
    meta_str = json.dumps(metadata or {}, ensure_ascii=False, default=str)
    db = _get_db()
    cursor = db.execute(
        "INSERT INTO llm_costs (chat_id, message_id, location_id, model, step, "
        "prompt_tokens, completion_tokens, total_tokens, cost_usd, timestamp, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_id, message_id, location_id, model, step,
         prompt_tokens, completion_tokens, total_tokens, cost_usd, now, meta_str),
    )
    db.commit()
    cost_id = cursor.lastrowid
    db.close()
    return {"id": cost_id, "cost_usd": cost_usd, "total_tokens": total_tokens}


def get_chat_costs(chat_id: str) -> dict:
    """Get aggregated costs for a single chat."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM llm_costs WHERE chat_id = ? ORDER BY timestamp ASC", (chat_id,)
    ).fetchall()
    agg = db.execute(
        "SELECT COUNT(*) as calls, SUM(prompt_tokens) as prompt_tokens, "
        "SUM(completion_tokens) as completion_tokens, SUM(total_tokens) as total_tokens, "
        "SUM(cost_usd) as total_cost_usd FROM llm_costs WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    # Cost breakdown by model
    by_model = db.execute(
        "SELECT model, COUNT(*) as calls, SUM(prompt_tokens) as prompt_tokens, "
        "SUM(completion_tokens) as completion_tokens, SUM(total_tokens) as total_tokens, "
        "SUM(cost_usd) as cost_usd FROM llm_costs WHERE chat_id = ? GROUP BY model", (chat_id,)
    ).fetchall()
    # Cost breakdown by step
    by_step = db.execute(
        "SELECT step, COUNT(*) as calls, SUM(total_tokens) as total_tokens, "
        "SUM(cost_usd) as cost_usd FROM llm_costs WHERE chat_id = ? GROUP BY step", (chat_id,)
    ).fetchall()
    db.close()

    details = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        details.append(d)

    return {
        "chat_id": chat_id,
        "summary": {
            "total_calls": agg["calls"] or 0,
            "prompt_tokens": agg["prompt_tokens"] or 0,
            "completion_tokens": agg["completion_tokens"] or 0,
            "total_tokens": agg["total_tokens"] or 0,
            "total_cost_usd": round(agg["total_cost_usd"] or 0, 6),
        },
        "by_model": [dict(r) for r in by_model],
        "by_step": [dict(r) for r in by_step],
        "details": details,
    }


# ---------------------------------------------------------------------------
# AI Trace Store — records every LLM call with full inputs/outputs/tool calls
# ---------------------------------------------------------------------------
def record_trace(
    step: str,
    location_id: str,
    *,
    trace_id: str | None = None,
    chat_id: str | None = None,
    task_id: str | None = None,
    parent_trace_id: str | None = None,
    model: str = "",
    provider: str = "",
    input_data: dict | str | None = None,
    output_data: dict | str | None = None,
    tool_calls: list | None = None,
    tool_results: list | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    status: str = "ok",
    error: str | None = None,
    metadata: dict | None = None,
    started_at: float | None = None,
    completed_at: float | None = None,
) -> dict:
    """Record a single AI trace (LLM call, tool call, or processing step)."""
    now = time.time()
    tid = trace_id or str(uuid.uuid4())

    def _safe_json(obj):
        if obj is None:
            return "{}"
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, ensure_ascii=False, default=str)

    # Truncate large inputs/outputs to keep DB manageable
    input_str = _safe_json(input_data)
    output_str = _safe_json(output_data)
    if len(input_str) > 50000:
        input_str = input_str[:50000] + "...[truncated]"
    if len(output_str) > 50000:
        output_str = output_str[:50000] + "...[truncated]"

    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    if cost_usd == 0.0 and model and prompt_tokens > 0:
        cost_usd = _estimate_cost(model, prompt_tokens, completion_tokens)

    db = _get_db()
    db.execute(
        "INSERT INTO ai_traces (trace_id, chat_id, task_id, location_id, parent_trace_id, "
        "step, model, provider, input, output, tool_calls, tool_results, "
        "prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms, "
        "status, error, metadata, started_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, chat_id, task_id, location_id, parent_trace_id,
         step, model, provider, input_str, output_str,
         _safe_json(tool_calls), _safe_json(tool_results),
         prompt_tokens, completion_tokens, total_tokens, cost_usd, latency_ms,
         status, error, _safe_json(metadata),
         started_at or now, completed_at or now),
    )
    db.commit()
    db.close()
    return {"trace_id": tid, "step": step}


def get_chat_traces(chat_id: str, limit: int = 200) -> list[dict]:
    """Get all AI traces for a chat, ordered by time."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM ai_traces WHERE chat_id = ? ORDER BY started_at ASC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    db.close()
    return [_parse_trace_row(r) for r in rows]


def get_task_traces(task_id: str, limit: int = 200) -> list[dict]:
    """Get all AI traces for a task."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM ai_traces WHERE task_id = ? ORDER BY started_at ASC LIMIT ?",
        (task_id, limit),
    ).fetchall()
    db.close()
    return [_parse_trace_row(r) for r in rows]


def get_trace(trace_id: str) -> dict | None:
    """Get a single trace by ID."""
    db = _get_db()
    row = db.execute("SELECT * FROM ai_traces WHERE trace_id = ?", (trace_id,)).fetchone()
    db.close()
    return _parse_trace_row(row) if row else None


def get_trace_children(parent_trace_id: str) -> list[dict]:
    """Get child traces of a parent trace."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM ai_traces WHERE parent_trace_id = ? ORDER BY started_at ASC",
        (parent_trace_id,),
    ).fetchall()
    db.close()
    return [_parse_trace_row(r) for r in rows]


def get_location_traces(location_id: str, since: float | None = None, limit: int = 100) -> dict:
    """Get aggregated trace stats for a location."""
    db = _get_db()
    where = "WHERE location_id = ?"
    params: list = [location_id]
    if since:
        where += " AND started_at >= ?"
        params.append(since)

    agg = db.execute(
        f"SELECT COUNT(*) as total_calls, SUM(prompt_tokens) as prompt_tokens, "
        f"SUM(completion_tokens) as completion_tokens, SUM(total_tokens) as total_tokens, "
        f"SUM(cost_usd) as total_cost_usd, AVG(latency_ms) as avg_latency_ms "
        f"FROM ai_traces {where}", params
    ).fetchone()

    by_step = db.execute(
        f"SELECT step, COUNT(*) as calls, SUM(total_tokens) as total_tokens, "
        f"SUM(cost_usd) as cost_usd, AVG(latency_ms) as avg_latency_ms "
        f"FROM ai_traces {where} GROUP BY step ORDER BY cost_usd DESC", params
    ).fetchall()

    by_model = db.execute(
        f"SELECT model, COUNT(*) as calls, SUM(total_tokens) as total_tokens, "
        f"SUM(cost_usd) as cost_usd, AVG(latency_ms) as avg_latency_ms "
        f"FROM ai_traces {where} AND model != '' GROUP BY model ORDER BY cost_usd DESC", params
    ).fetchall()

    recent = db.execute(
        f"SELECT * FROM ai_traces {where} ORDER BY started_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()

    db.close()
    return {
        "location_id": location_id,
        "summary": {
            "total_calls": agg["total_calls"] or 0,
            "prompt_tokens": agg["prompt_tokens"] or 0,
            "completion_tokens": agg["completion_tokens"] or 0,
            "total_tokens": agg["total_tokens"] or 0,
            "total_cost_usd": round(agg["total_cost_usd"] or 0, 6),
            "avg_latency_ms": round(agg["avg_latency_ms"] or 0, 1),
        },
        "by_step": [dict(r) for r in by_step],
        "by_model": [dict(r) for r in by_model],
        "recent": [_parse_trace_row(r) for r in recent],
    }


def _parse_trace_row(row) -> dict:
    """Parse a trace row from SQLite into a dict with JSON fields decoded."""
    d = dict(row)
    for field in ("input", "output", "tool_calls", "tool_results", "metadata"):
        try:
            d[field] = json.loads(d.get(field, "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def get_location_costs(location_id: str, since: float | None = None) -> dict:
    """Get aggregated costs for a location (user), optionally filtered by time."""
    db = _get_db()
    where = "WHERE location_id = ?"
    params: list = [location_id]
    if since:
        where += " AND timestamp >= ?"
        params.append(since)

    agg = db.execute(
        f"SELECT COUNT(*) as calls, SUM(prompt_tokens) as prompt_tokens, "
        f"SUM(completion_tokens) as completion_tokens, SUM(total_tokens) as total_tokens, "
        f"SUM(cost_usd) as total_cost_usd FROM llm_costs {where}", params
    ).fetchone()

    by_model = db.execute(
        f"SELECT model, COUNT(*) as calls, SUM(prompt_tokens) as prompt_tokens, "
        f"SUM(completion_tokens) as completion_tokens, SUM(total_tokens) as total_tokens, "
        f"SUM(cost_usd) as cost_usd FROM llm_costs {where} GROUP BY model", params
    ).fetchall()

    by_chat = db.execute(
        f"SELECT c.chat_id, c.title, COUNT(*) as calls, SUM(l.total_tokens) as total_tokens, "
        f"SUM(l.cost_usd) as cost_usd FROM llm_costs l "
        f"JOIN chats c ON l.chat_id = c.chat_id {where.replace('location_id', 'l.location_id').replace('timestamp', 'l.timestamp')} "
        f"GROUP BY l.chat_id ORDER BY cost_usd DESC LIMIT 50", params
    ).fetchall()

    db.close()

    return {
        "location_id": location_id,
        "summary": {
            "total_calls": agg["calls"] or 0,
            "prompt_tokens": agg["prompt_tokens"] or 0,
            "completion_tokens": agg["completion_tokens"] or 0,
            "total_tokens": agg["total_tokens"] or 0,
            "total_cost_usd": round(agg["total_cost_usd"] or 0, 6),
        },
        "by_model": [dict(r) for r in by_model],
        "by_chat": [dict(r) for r in by_chat],
    }


# ---------------------------------------------------------------------------
# Context window — builds the conversation history for LLM
# ---------------------------------------------------------------------------
def build_context_window(chat_id: str) -> list[dict]:
    """
    Build a conversation history suitable for LLM context.

    Strategy:
    1. Get all messages
    2. If <= MAX_CONTEXT_MESSAGES, return all as-is
    3. If more, create a summary of older messages + keep recent ones verbatim
    4. Enforce MAX_CONTEXT_CHARS limit

    Returns list of {role: str, content: str} dicts ready for LLM.
    """
    all_messages = get_messages(chat_id)
    if not all_messages:
        return []

    # Filter to user/assistant messages only (skip system)
    conversation = [
        {"role": m["role"], "content": m["content"]}
        for m in all_messages
        if m["role"] in ("user", "assistant")
    ]

    if len(conversation) <= MAX_CONTEXT_MESSAGES:
        return _trim_to_char_limit(conversation)

    # Split into old (to summarize) and recent (keep verbatim)
    keep_recent = MAX_CONTEXT_MESSAGES
    old_messages = conversation[:-keep_recent]
    recent_messages = conversation[-keep_recent:]

    # Build summary of old messages
    summary = _summarize_conversation(old_messages)
    context = [{"role": "user", "content": f"[Resumen de la conversacion anterior: {summary}]"},
               {"role": "assistant", "content": "Entendido, tengo el contexto de nuestra conversacion anterior."}]
    context.extend(recent_messages)

    return _trim_to_char_limit(context)


def _summarize_conversation(messages: list[dict]) -> str:
    """Create a brief summary of older messages."""
    parts = []
    for i in range(0, len(messages), 2):
        user_msg = messages[i]["content"] if i < len(messages) else ""
        assistant_msg = messages[i + 1]["content"] if i + 1 < len(messages) else ""
        # Truncate long messages for summary
        user_short = user_msg[:100] + "..." if len(user_msg) > 100 else user_msg
        assistant_short = assistant_msg[:150] + "..." if len(assistant_msg) > 150 else assistant_msg
        parts.append(f"- Pregunta: {user_short} -> Respuesta: {assistant_short}")

    return "\n".join(parts)


def _trim_to_char_limit(messages: list[dict]) -> list[dict]:
    """Trim messages from the start to fit within MAX_CONTEXT_CHARS."""
    total = sum(len(m["content"]) for m in messages)
    while total > MAX_CONTEXT_CHARS and len(messages) > 2:
        removed = messages.pop(0)
        total -= len(removed["content"])
    return messages
