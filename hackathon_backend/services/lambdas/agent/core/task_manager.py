"""
Task Manager — manages async complex task lifecycle.

Architecture:
- Lambda (AGENT_TABLE_NAME set): DynamoDB single-table design
- Local dev (no AGENT_TABLE_NAME): SQLite for fast iteration

Task lifecycle:
  PENDING -> RUNNING -> COMPLETED | FAILED | CANCELLED
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Cost budgets per task type
# ---------------------------------------------------------------------------
TASK_COST_LIMITS: dict[str, dict] = {
    "cash_flow_forecast":    {"max_usd": 3.00, "max_tokens": 1_500_000, "max_agents": 5, "timeout_s": 600},
    "pack_reporting":        {"max_usd": 3.00, "max_tokens": 1_500_000, "max_agents": 5, "timeout_s": 600},
    "modelo_303":            {"max_usd": 2.00, "max_tokens": 1_000_000, "max_agents": 4, "timeout_s": 600},
    "aging_analysis":        {"max_usd": 1.50, "max_tokens": 750_000, "max_agents": 3, "timeout_s": 300},
    "client_profitability":  {"max_usd": 2.50, "max_tokens": 1_200_000, "max_agents": 5, "timeout_s": 600},
    "modelo_347":            {"max_usd": 2.00, "max_tokens": 1_000_000, "max_agents": 4, "timeout_s": 600},
    "three_way_matching":    {"max_usd": 1.50, "max_tokens": 750_000, "max_agents": 3, "timeout_s": 300},
    "document_analysis":     {"max_usd": 1.00, "max_tokens": 500_000, "max_agents": 2, "timeout_s": 180},
    "custom":                {"max_usd": 5.00, "max_tokens": 2_000_000, "max_agents": 5, "timeout_s": 600},
}

TASK_TYPE_NAMES: dict[str, str] = {
    "cash_flow_forecast": "Prevision de Tesoreria (13 semanas)",
    "pack_reporting": "Pack Reporting Mensual",
    "modelo_303": "Borrador Modelo 303 (IVA)",
    "aging_analysis": "Analisis de Antiguedad (Aging)",
    "client_profitability": "Rentabilidad por Cliente",
    "modelo_347": "Modelo 347 (Terceros >3.005 EUR)",
    "three_way_matching": "Three-Way Matching",
    "document_analysis": "Analisis de Documentos",
    "custom": "Tarea Personalizada",
}

# Backend selection
_AGENT_TABLE = os.environ.get("AGENT_TABLE_NAME", "")
_USE_DYNAMO = bool(_AGENT_TABLE)


# ===================================================================
# DynamoDB Backend
# ===================================================================
if _USE_DYNAMO:
    import boto3
    from boto3.dynamodb.conditions import Key

    _REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_REGION_NAME", "eu-west-3"))
    _dynamo_resource = None

    def _get_table():
        global _dynamo_resource
        if _dynamo_resource is None:
            _dynamo_resource = boto3.resource("dynamodb", region_name=_REGION)
        return _dynamo_resource.Table(_AGENT_TABLE)

    def create_task(
        location_id: str, task_type: str, description: str = "",
        chat_id: str | None = None, metadata: dict | None = None,
    ) -> dict:
        task_id = str(uuid.uuid4())
        now = time.time()
        limits = TASK_COST_LIMITS.get(task_type, TASK_COST_LIMITS["custom"])
        table = _get_table()
        item = {
            "pk": f"LOC#{location_id}",
            "sk": f"TASK#{task_id}",
            "task_id": task_id,
            "chat_id": chat_id or "",
            "location_id": location_id,
            "task_type": task_type,
            "description": description,
            "status": "PENDING",
            "progress": 0,
            "result_summary": "",
            "error": "",
            "total_tokens": 0,
            "cost_usd": "0.0",
            "cost_budget_usd": str(limits["max_usd"]),
            "artifacts": "[]",
            "created_at": str(now),
            "started_at": "",
            "completed_at": "",
            "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
        }
        # Also store reverse lookup by task_id
        table.put_item(Item=item)
        table.put_item(Item={**item, "pk": f"TASK#{task_id}", "sk": "META"})
        if chat_id:
            # GSI for looking up tasks by chat
            item_copy = dict(item)
            item_copy["gsi1pk"] = f"CHAT#{chat_id}"
            item_copy["gsi1sk"] = f"TASK#{now:.6f}"
            table.put_item(Item=item_copy)

        return {
            "task_id": task_id, "chat_id": chat_id, "location_id": location_id,
            "task_type": task_type,
            "task_type_name": TASK_TYPE_NAMES.get(task_type, task_type),
            "description": description, "status": "PENDING", "progress": 0,
            "cost_budget_usd": limits["max_usd"], "created_at": now,
        }

    def get_task(task_id: str) -> dict | None:
        table = _get_table()
        resp = table.get_item(Key={"pk": f"TASK#{task_id}", "sk": "META"})
        item = resp.get("Item")
        if not item:
            return None
        task = _item_to_task(item)
        # Get steps
        steps_resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"TASK#{task_id}") & Key("sk").begins_with("STEP#"),
            ScanIndexForward=True,
        )
        task["steps"] = [_item_to_step(s) for s in steps_resp.get("Items", [])]
        return task

    def list_tasks(location_id: str, limit: int = 50) -> list[dict]:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"LOC#{location_id}") & Key("sk").begins_with("TASK#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_item_to_task(i) for i in resp.get("Items", [])]

    def update_task_status(
        task_id: str, status: str, progress: int | None = None,
        result_summary: str = "", error: str = "",
    ) -> bool:
        table = _get_table()
        expr_parts = ["#st = :status"]
        expr_names = {"#st": "status"}
        expr_values: dict[str, Any] = {":status": status}
        if progress is not None:
            expr_parts.append("progress = :progress")
            expr_values[":progress"] = progress
        if result_summary:
            expr_parts.append("result_summary = :rs")
            expr_values[":rs"] = result_summary
        if error:
            expr_parts.append("error = :err")
            expr_values[":err"] = error
        if status == "RUNNING" and progress == 0:
            expr_parts.append("started_at = :sa")
            expr_values[":sa"] = str(time.time())
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            expr_parts.append("completed_at = :ca")
            expr_values[":ca"] = str(time.time())

        update_expr = "SET " + ", ".join(expr_parts)
        table.update_item(
            Key={"pk": f"TASK#{task_id}", "sk": "META"},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        # Also update the LOC# item — need to look up location_id first
        resp = table.get_item(Key={"pk": f"TASK#{task_id}", "sk": "META"})
        item = resp.get("Item")
        if item:
            table.update_item(
                Key={"pk": f"LOC#{item['location_id']}", "sk": f"TASK#{task_id}"},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
        return True

    def add_task_cost(task_id: str, tokens: int, cost_usd: float) -> dict:
        table = _get_table()
        resp = table.update_item(
            Key={"pk": f"TASK#{task_id}", "sk": "META"},
            UpdateExpression="SET total_tokens = total_tokens + :t, cost_usd = cost_usd + :c",
            ExpressionAttributeValues={":t": tokens, ":c": cost_usd},
            ReturnValues="ALL_NEW",
        )
        item = resp.get("Attributes", {})
        budget = float(item.get("cost_budget_usd", 2.0))
        current_cost = float(item.get("cost_usd", 0))
        return {
            "total_tokens": int(item.get("total_tokens", 0)),
            "cost_usd": current_cost,
            "budget_usd": budget,
            "budget_exceeded": current_cost > budget,
        }

    def add_task_artifact(task_id: str, artifact: dict) -> None:
        table = _get_table()
        resp = table.get_item(Key={"pk": f"TASK#{task_id}", "sk": "META"})
        item = resp.get("Item")
        if item:
            try:
                artifacts = json.loads(item.get("artifacts", "[]"))
            except (json.JSONDecodeError, TypeError):
                artifacts = []
            artifacts.append(artifact)
            table.update_item(
                Key={"pk": f"TASK#{task_id}", "sk": "META"},
                UpdateExpression="SET artifacts = :a",
                ExpressionAttributeValues={":a": json.dumps(artifacts, ensure_ascii=False, default=str)},
            )

    def cancel_task(task_id: str) -> bool:
        return update_task_status(task_id, "CANCELLED")

    def add_task_step(
        task_id: str, step_number: int, description: str, agent_name: str = "",
    ) -> int:
        step_id = int(time.time() * 1000) % 2147483647
        table = _get_table()
        table.put_item(Item={
            "pk": f"TASK#{task_id}",
            "sk": f"STEP#{step_number:04d}",
            "step_id": step_id,
            "task_id": task_id,
            "step_number": step_number,
            "agent_name": agent_name,
            "description": description,
            "status": "PENDING",
            "result_summary": "",
            "tokens_used": 0,
            "cost_usd": "0.0",
            "started_at": "",
            "completed_at": "",
            "metadata": "{}",
        })
        return step_id

    def update_task_step(
        step_id: int, status: str, result_summary: str = "",
        tokens_used: int = 0, cost_usd: float = 0.0,
    ) -> None:
        # In DynamoDB we need task_id and step_number — this is a limitation
        # For simplicity, scan for the step_id (rare operation)
        pass

    def get_task_steps(task_id: str) -> list[dict]:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"TASK#{task_id}") & Key("sk").begins_with("STEP#"),
            ScanIndexForward=True,
        )
        return [_item_to_step(i) for i in resp.get("Items", [])]

    def check_budget(task_id: str) -> dict:
        table = _get_table()
        resp = table.get_item(Key={"pk": f"TASK#{task_id}", "sk": "META"})
        item = resp.get("Item")
        if not item:
            return {"ok": False, "reason": "Task not found"}
        status = item.get("status", "")
        cost = float(item.get("cost_usd", 0))
        budget = float(item.get("cost_budget_usd", 2.0))
        if status == "CANCELLED":
            return {"ok": False, "reason": "Task cancelled"}
        if cost > budget:
            return {"ok": False, "reason": f"Budget exceeded: ${cost:.4f} > ${budget:.2f}"}
        return {"ok": True, "cost_usd": cost, "budget_usd": budget}

    def _item_to_task(item: dict) -> dict:
        d = dict(item)
        try:
            d["artifacts"] = json.loads(d.get("artifacts", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["artifacts"] = []
        try:
            d["metadata"] = json.loads(d.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        d["task_type_name"] = TASK_TYPE_NAMES.get(d.get("task_type", ""), d.get("task_type", ""))
        for field in ("created_at", "started_at", "completed_at", "cost_usd", "cost_budget_usd"):
            if field in d and isinstance(d[field], str) and d[field]:
                try:
                    d[field] = float(d[field])
                except (ValueError, TypeError):
                    pass
        for field in ("progress", "total_tokens"):
            if field in d:
                try:
                    d[field] = int(d[field])
                except (ValueError, TypeError):
                    pass
        return d

    def _item_to_step(item: dict) -> dict:
        d = dict(item)
        try:
            d["metadata"] = json.loads(d.get("metadata", "{}"))
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        for field in ("started_at", "completed_at", "cost_usd"):
            if field in d and isinstance(d[field], str) and d[field]:
                try:
                    d[field] = float(d[field])
                except (ValueError, TypeError):
                    pass
        return d


# ===================================================================
# SQLite Backend (local dev)
# ===================================================================
else:
    import sqlite3

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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_location ON tasks(location_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_steps ON task_steps(task_id, step_number)")
        conn.commit()
        return conn

    def create_task(
        location_id: str, task_type: str, description: str = "",
        chat_id: str | None = None, metadata: dict | None = None,
    ) -> dict:
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
            "task_id": task_id, "chat_id": chat_id, "location_id": location_id,
            "task_type": task_type,
            "task_type_name": TASK_TYPE_NAMES.get(task_type, task_type),
            "description": description, "status": "PENDING", "progress": 0,
            "cost_budget_usd": limits["max_usd"], "created_at": now,
        }

    def get_task(task_id: str) -> dict | None:
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
        steps = db.execute(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_number", (task_id,)
        ).fetchall()
        task["steps"] = [_parse_step(s) for s in steps]
        db.close()
        return task

    def list_tasks(location_id: str, limit: int = 50) -> list[dict]:
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
        task_id: str, status: str, progress: int | None = None,
        result_summary: str = "", error: str = "",
    ) -> bool:
        db = _get_db()
        updates: dict[str, Any] = {"status": status}
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
        return update_task_status(task_id, "CANCELLED")

    def add_task_step(
        task_id: str, step_number: int, description: str, agent_name: str = "",
    ) -> int:
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
        step_id: int, status: str, result_summary: str = "",
        tokens_used: int = 0, cost_usd: float = 0.0,
    ) -> None:
        db = _get_db()
        updates: dict[str, Any] = {"status": status}
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
        db = _get_db()
        rows = db.execute(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_number", (task_id,)
        ).fetchall()
        db.close()
        return [_parse_step(r) for r in rows]

    def check_budget(task_id: str) -> dict:
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
            return {"ok": False, "reason": "Task timeout exceeded"}
        return {"ok": True, "cost_usd": row["cost_usd"], "budget_usd": row["cost_budget_usd"]}

    def _parse_step(row) -> dict:
        d = dict(row)
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        return d
