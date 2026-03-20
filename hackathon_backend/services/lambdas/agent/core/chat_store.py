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
