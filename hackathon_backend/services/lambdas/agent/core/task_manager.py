"""
Task Manager — manages async complex task lifecycle.

Architecture:
- Local: SQLite (same DB as chat_store)
- Production: DynamoDB (PK=locationId, SK=TASK#{taskId}) + S3 for artifacts

Task lifecycle:
  PENDING → RUNNING → COMPLETED | FAILED | CANCELLED

Each task has:
- task_id: unique identifier
- chat_id: parent chat (optional)
- location_id: tenant isolation
- task_type: cash_flow_forecast, pack_reporting, modelo_303, aging_analysis, etc.
- status: PENDING | RUNNING | COMPLETED | FAILED | CANCELLED
- progress: 0-100 percentage
- steps: list of execution steps with status
- artifacts: list of generated files (Excel, PDF)
- cost tracking: accumulated tokens and USD cost
- cost_budget: max allowed cost before auto-stop
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Cost budgets per task type
# ---------------------------------------------------------------------------
TASK_COST_LIMITS: dict[str, dict] = {
    "cash_flow_forecast":    {"max_usd": 1.00, "max_tokens": 500_000, "max_agents": 4, "timeout_s": 300},
    "pack_reporting":        {"max_usd": 1.50, "max_tokens": 750_000, "max_agents": 5, "timeout_s": 600},
    "modelo_303":            {"max_usd": 0.80, "max_tokens": 400_000, "max_agents": 3, "timeout_s": 300},
    "aging_analysis":        {"max_usd": 0.50, "max_tokens": 300_000, "max_agents": 2, "timeout_s": 180},
    "client_profitability":  {"max_usd": 1.00, "max_tokens": 500_000, "max_agents": 4, "timeout_s": 300},
    "modelo_347":            {"max_usd": 0.80, "max_tokens": 400_000, "max_agents": 3, "timeout_s": 300},
    "three_way_matching":    {"max_usd": 0.60, "max_tokens": 300_000, "max_agents": 2, "timeout_s": 180},
    "document_analysis":     {"max_usd": 0.50, "max_tokens": 200_000, "max_agents": 2, "timeout_s": 120},
    "custom":                {"max_usd": 2.00, "max_tokens": 1_000_000, "max_agents": 5, "timeout_s": 600},
}

# Friendly names for task types
TASK_TYPE_NAMES: dict[str, str] = {
    "cash_flow_forecast": "Previsión de Tesorería (13 semanas)",
    "pack_reporting": "Pack Reporting Mensual",
    "modelo_303": "Borrador Modelo 303 (IVA)",
    "aging_analysis": "Análisis de Antigüedad (Aging)",
    "client_profitability": "Rentabilidad por Cliente",
    "modelo_347": "Modelo 347 (Terceros >3.005€)",
    "three_way_matching": "Three-Way Matching",
    "document_analysis": "Análisis de Documentos",
    "custom": "Tarea Personalizada",
}


# ---------------------------------------------------------------------------
# SQLite setup (reuses same DB as chat_store)
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.environ.get("TEMP", "/tmp"), "cfo_agent_chats.db")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            chat_id TEXT,
            location_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDING',
            progress INTEGER DEFAULT 0,
            result_summary TEXT DEFAULT '',
            error TEXT DEFAULT '',
            total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            cost_budget_usd REAL DEFAULT 2.0,
            artifacts TEXT DEFAULT '[]',
            created_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL,
            metadata TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            step_number INTEGER NOT NULL,
            agent_name TEXT DEFAULT '',
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDING',
            result_summary TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            started_at REAL,
            completed_at REAL,
            metadata TEXT DEFAULT '{}',
            FOREIGN KEY (task_id) REFERENCES tasks(task_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_location ON tasks(location_id, created_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_task_steps ON task_steps(task_id, step_number)
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------
def create_task(
    location_id: str,
    task_type: str,
    description: str = "",
    chat_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create a new task."""
    task_id = str(uuid.uuid4())
    now = time.time()
    limits = TASK_COST_LIMITS.get(task_type, TASK_COST_LIMITS["custom"])
    meta_str = json.dumps(metadata or {}, ensure_ascii=False, default=str)

    db = _get_db()
    db.execute(
        "INSERT INTO tasks (task_id, chat_id, location_id, task_type, description, "
        "status, cost_budget_usd, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, chat_id, location_id, task_type, description,
         "PENDING", limits["max_usd"], now, meta_str),
    )
    db.commit()
    db.close()

    return {
        "task_id": task_id,
        "chat_id": chat_id,
        "location_id": location_id,
        "task_type": task_type,
        "task_type_name": TASK_TYPE_NAMES.get(task_type, task_type),
        "description": description,
        "status": "PENDING",
        "progress": 0,
        "cost_budget_usd": limits["max_usd"],
        "created_at": now,
    }


def get_task(task_id: str) -> dict | None:
    """Get task with its steps."""
    db = _get_db()
    row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        return None
    task = dict(row)
    try:
        task["artifacts"] = json.loads(task["artifacts"])
    except (json.JSONDecodeError, TypeError):
        task["artifacts"] = []
    try:
        task["metadata"] = json.loads(task["metadata"])
    except (json.JSONDecodeError, TypeError):
        task["metadata"] = {}
    task["task_type_name"] = TASK_TYPE_NAMES.get(task["task_type"], task["task_type"])

    # Get steps
    steps = db.execute(
        "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_number", (task_id,)
    ).fetchall()
    task["steps"] = [_parse_step(s) for s in steps]
    db.close()
    return task


def list_tasks(location_id: str, limit: int = 50) -> list[dict]:
    """List tasks for a location."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM tasks WHERE location_id = ? ORDER BY created_at DESC LIMIT ?",
        (location_id, limit),
    ).fetchall()
    db.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["artifacts"] = json.loads(d["artifacts"])
        except (json.JSONDecodeError, TypeError):
            d["artifacts"] = []
        d["task_type_name"] = TASK_TYPE_NAMES.get(d["task_type"], d["task_type"])
        result.append(d)
    return result


def update_task_status(
    task_id: str,
    status: str,
    progress: int | None = None,
    result_summary: str = "",
    error: str = "",
) -> bool:
    """Update task status and progress."""
    db = _get_db()
    updates = {"status": status}
    if progress is not None:
        updates["progress"] = progress
    if result_summary:
        updates["result_summary"] = result_summary
    if error:
        updates["error"] = error
    if status == "RUNNING" and progress == 0:
        updates["started_at"] = time.time()
    if status in ("COMPLETED", "FAILED", "CANCELLED"):
        updates["completed_at"] = time.time()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]
    db.execute(f"UPDATE tasks SET {set_clause} WHERE task_id = ?", values)
    db.commit()
    db.close()
    return True


def add_task_cost(task_id: str, tokens: int, cost_usd: float) -> dict:
    """Add cost to a task. Returns current totals and whether budget exceeded."""
    db = _get_db()
    db.execute(
        "UPDATE tasks SET total_tokens = total_tokens + ?, cost_usd = cost_usd + ? WHERE task_id = ?",
        (tokens, cost_usd, task_id),
    )
    db.commit()
    row = db.execute(
        "SELECT total_tokens, cost_usd, cost_budget_usd FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    db.close()
    if not row:
        return {"budget_exceeded": True}
    return {
        "total_tokens": row["total_tokens"],
        "cost_usd": row["cost_usd"],
        "budget_usd": row["cost_budget_usd"],
        "budget_exceeded": row["cost_usd"] > row["cost_budget_usd"],
    }


def add_task_artifact(task_id: str, artifact: dict) -> None:
    """Add an artifact (file reference) to a task."""
    db = _get_db()
    row = db.execute("SELECT artifacts FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    if row:
        try:
            artifacts = json.loads(row["artifacts"])
        except (json.JSONDecodeError, TypeError):
            artifacts = []
        artifacts.append(artifact)
        db.execute(
            "UPDATE tasks SET artifacts = ? WHERE task_id = ?",
            (json.dumps(artifacts, ensure_ascii=False, default=str), task_id),
        )
        db.commit()
    db.close()


def cancel_task(task_id: str) -> bool:
    """Cancel a running task."""
    return update_task_status(task_id, "CANCELLED")


# ---------------------------------------------------------------------------
# Task Steps
# ---------------------------------------------------------------------------
def add_task_step(
    task_id: str,
    step_number: int,
    description: str,
    agent_name: str = "",
) -> int:
    """Add a step to a task. Returns step ID."""
    db = _get_db()
    cursor = db.execute(
        "INSERT INTO task_steps (task_id, step_number, agent_name, description, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_id, step_number, agent_name, description, "PENDING"),
    )
    db.commit()
    step_id = cursor.lastrowid
    db.close()
    return step_id


def update_task_step(
    step_id: int,
    status: str,
    result_summary: str = "",
    tokens_used: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Update a task step."""
    db = _get_db()
    updates = {"status": status}
    if result_summary:
        updates["result_summary"] = result_summary
    if tokens_used:
        updates["tokens_used"] = tokens_used
    if cost_usd:
        updates["cost_usd"] = cost_usd
    if status == "RUNNING":
        updates["started_at"] = time.time()
    if status in ("COMPLETED", "FAILED"):
        updates["completed_at"] = time.time()

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [step_id]
    db.execute(f"UPDATE task_steps SET {set_clause} WHERE id = ?", values)
    db.commit()
    db.close()


def get_task_steps(task_id: str) -> list[dict]:
    """Get all steps for a task."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_number", (task_id,)
    ).fetchall()
    db.close()
    return [_parse_step(r) for r in rows]


def _parse_step(row) -> dict:
    d = dict(row)
    try:
        d["metadata"] = json.loads(d["metadata"])
    except (json.JSONDecodeError, TypeError):
        d["metadata"] = {}
    return d


# ---------------------------------------------------------------------------
# Budget checker — call before each LLM call
# ---------------------------------------------------------------------------
def check_budget(task_id: str) -> dict:
    """Check if a task is within its cost budget."""
    db = _get_db()
    row = db.execute(
        "SELECT status, total_tokens, cost_usd, cost_budget_usd, created_at FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    db.close()
    if not row:
        return {"ok": False, "reason": "Task not found"}
    if row["status"] == "CANCELLED":
        return {"ok": False, "reason": "Task cancelled"}
    if row["cost_usd"] > row["cost_budget_usd"]:
        return {"ok": False, "reason": f"Budget exceeded: ${row['cost_usd']:.4f} > ${row['cost_budget_usd']:.2f}"}
    limits = TASK_COST_LIMITS.get("custom", {})
    timeout = limits.get("timeout_s", 600)
    if time.time() - row["created_at"] > timeout + 60:
        return {"ok": False, "reason": f"Task timeout exceeded"}
    return {"ok": True, "cost_usd": row["cost_usd"], "budget_usd": row["cost_budget_usd"]}
