"""
Chat store — manages conversation history with context windowing.

Architecture:
- Lambda (AGENT_TABLE_NAME set): DynamoDB single-table design
- Local dev (no AGENT_TABLE_NAME): SQLite for fast iteration

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
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_CHARS = 30000
SUMMARY_THRESHOLD = 10

# Backend selection: DynamoDB if AGENT_TABLE_NAME is set, else SQLite
_AGENT_TABLE = os.environ.get("AGENT_TABLE_NAME", "")
_USE_DYNAMO = bool(_AGENT_TABLE)

# ---------------------------------------------------------------------------
# LLM Cost pricing
# ---------------------------------------------------------------------------
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
    pricing = MODEL_PRICING.get(model, {"input": 1.0, "output": 5.0})
    input_price = pricing["input"]
    non_cached_input = prompt_tokens - cache_read_tokens - cache_creation_tokens
    if non_cached_input < 0:
        non_cached_input = 0
    input_cost = (
        non_cached_input * input_price
        + cache_read_tokens * input_price * 0.1
        + cache_creation_tokens * input_price * 1.25
    ) / 1_000_000
    output_cost = completion_tokens * pricing["output"] / 1_000_000
    return round(input_cost + output_cost, 6)


# ===================================================================
# DynamoDB Backend
# ===================================================================
if _USE_DYNAMO:
    import boto3
    from boto3.dynamodb.conditions import Key, Attr

    _REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_REGION_NAME", "eu-west-3"))
    _dynamo_resource = None

    def _get_table():
        global _dynamo_resource
        if _dynamo_resource is None:
            _dynamo_resource = boto3.resource("dynamodb", region_name=_REGION)
        return _dynamo_resource.Table(_AGENT_TABLE)

    # ---- Chat CRUD (DynamoDB) ----

    def create_chat(location_id: str, model: str = "claude-sonnet-4.5", title: str = "") -> dict:
        chat_id = str(uuid.uuid4())
        now = str(time.time())
        table = _get_table()
        table.put_item(Item={
            "pk": f"LOC#{location_id}",
            "sk": f"CHAT#{chat_id}",
            "chat_id": chat_id,
            "location_id": location_id,
            "title": title,
            "model": model,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        })
        return {"chat_id": chat_id, "location_id": location_id, "title": title,
                "model": model, "created_at": float(now), "updated_at": float(now), "message_count": 0}

    def get_chat(chat_id: str) -> dict | None:
        table = _get_table()
        # We need to find the chat — query GSI or scan by chat_id
        # Since we store pk=LOC#{locationId}, we need the locationId to query directly.
        # Alternative: do a query with a GSI. For now, use gsi2 with chat_id.
        # Actually, let's query all LOC# partitions — not ideal. Better approach:
        # Also store a reverse lookup: pk=CHAT#{chatId}, sk=META
        resp = table.get_item(Key={"pk": f"CHAT#{chat_id}", "sk": "META"})
        item = resp.get("Item")
        if not item:
            return None
        return _item_to_chat(item)

    def list_chats(location_id: str, limit: int = 50) -> list[dict]:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"LOC#{location_id}") & Key("sk").begins_with("CHAT#"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_item_to_chat(i) for i in resp.get("Items", [])]

    def delete_chat(chat_id: str) -> bool:
        table = _get_table()
        chat = get_chat(chat_id)
        if not chat:
            return False
        location_id = chat["location_id"]
        # Delete chat item from LOC# partition
        table.delete_item(Key={"pk": f"LOC#{location_id}", "sk": f"CHAT#{chat_id}"})
        # Delete META item
        table.delete_item(Key={"pk": f"CHAT#{chat_id}", "sk": "META"})
        # Delete all messages, costs, traces for this chat
        resp = table.query(KeyConditionExpression=Key("pk").eq(f"CHAT#{chat_id}"))
        with table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        return True

    def update_chat(chat_id: str, **kwargs) -> bool:
        allowed = {"title", "model"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = str(time.time())
        table = _get_table()
        # Update both items (LOC# and CHAT#META)
        chat = get_chat(chat_id)
        if not chat:
            return False
        expr_parts = []
        expr_values = {}
        expr_names = {}
        for i, (k, v) in enumerate(updates.items()):
            attr_name = f"#a{i}"
            attr_val = f":v{i}"
            expr_parts.append(f"{attr_name} = {attr_val}")
            expr_names[attr_name] = k
            expr_values[attr_val] = v
        update_expr = "SET " + ", ".join(expr_parts)
        # Update META item
        table.update_item(
            Key={"pk": f"CHAT#{chat_id}", "sk": "META"},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        # Update LOC# item
        table.update_item(
            Key={"pk": f"LOC#{chat['location_id']}", "sk": f"CHAT#{chat_id}"},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
        return True

    def _item_to_chat(item: dict) -> dict:
        return {
            "chat_id": item.get("chat_id", ""),
            "location_id": item.get("location_id", ""),
            "title": item.get("title", ""),
            "model": item.get("model", "claude-sonnet-4.5"),
            "created_at": float(item.get("created_at", 0)),
            "updated_at": float(item.get("updated_at", 0)),
            "message_count": int(item.get("message_count", 0)),
        }

    # ---- Message CRUD (DynamoDB) ----

    def add_message(chat_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        now = time.time()
        msg_id = str(uuid.uuid4())[:12]
        meta = metadata or {}
        table = _get_table()
        # Store message
        table.put_item(Item={
            "pk": f"CHAT#{chat_id}",
            "sk": f"MSG#{now:.6f}#{msg_id}",
            "chat_id": chat_id,
            "msg_id": msg_id,
            "role": role,
            "content": content,
            "timestamp": str(now),
            "metadata": json.dumps(meta, ensure_ascii=False, default=str),
        })
        # Update chat metadata (updated_at, message_count, auto-title)
        chat = get_chat(chat_id)
        if chat:
            update_expr = "SET #ua = :now ADD #mc :one"
            expr_names: dict[str, str] = {"#ua": "updated_at", "#mc": "message_count"}
            expr_values: dict[str, Any] = {":now": str(now), ":one": 1}
            if role == "user" and not chat.get("title"):
                title = content[:80] + ("..." if len(content) > 80 else "")
                update_expr += ", #tt = :title"
                expr_names["#tt"] = "title"
                expr_values[":title"] = title
            table.update_item(
                Key={"pk": f"CHAT#{chat_id}", "sk": "META"},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
            table.update_item(
                Key={"pk": f"LOC#{chat['location_id']}", "sk": f"CHAT#{chat_id}"},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
        return {"id": msg_id, "chat_id": chat_id, "role": role, "content": content,
                "timestamp": now, "metadata": meta}

    def get_messages(chat_id: str, limit: int = 200) -> list[dict]:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"CHAT#{chat_id}") & Key("sk").begins_with("MSG#"),
            ScanIndexForward=True,
            Limit=limit,
        )
        result = []
        for item in resp.get("Items", []):
            meta = {}
            try:
                meta = json.loads(item.get("metadata", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            result.append({
                "id": item.get("msg_id", ""),
                "chat_id": chat_id,
                "role": item.get("role", ""),
                "content": item.get("content", ""),
                "timestamp": float(item.get("timestamp", 0)),
                "metadata": meta,
            })
        return result

    # ---- LLM Cost tracking (DynamoDB) ----

    def record_llm_cost(
        chat_id: str, location_id: str, model: str, step: str,
        prompt_tokens: int, completion_tokens: int,
        total_tokens: int | None = None, cost_usd: float | None = None,
        message_id: int | None = None, metadata: dict | None = None,
    ) -> dict:
        now = time.time()
        cost_id = str(uuid.uuid4())[:12]
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        if cost_usd is None:
            cost_usd = _estimate_cost(model, prompt_tokens, completion_tokens)
        table = _get_table()
        item = {
            "pk": f"CHAT#{chat_id}",
            "sk": f"COST#{now:.6f}#{cost_id}",
            "gsi1pk": f"LOC#{location_id}#COST",
            "gsi1sk": str(now),
            "chat_id": chat_id,
            "cost_id": cost_id,
            "location_id": location_id,
            "model": model,
            "step": step,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": str(cost_usd),
            "timestamp": str(now),
            "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
        }
        if message_id is not None:
            item["message_id"] = str(message_id)
        table.put_item(Item=item)
        return {"id": cost_id, "cost_usd": cost_usd, "total_tokens": total_tokens}

    def get_chat_costs(chat_id: str) -> dict:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"CHAT#{chat_id}") & Key("sk").begins_with("COST#"),
            ScanIndexForward=True,
        )
        items = resp.get("Items", [])
        total_prompt = sum(int(i.get("prompt_tokens", 0)) for i in items)
        total_compl = sum(int(i.get("completion_tokens", 0)) for i in items)
        total_tok = sum(int(i.get("total_tokens", 0)) for i in items)
        total_cost = sum(float(i.get("cost_usd", 0)) for i in items)
        details = []
        for i in items:
            meta = {}
            try:
                meta = json.loads(i.get("metadata", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            details.append({
                "id": i.get("cost_id", ""),
                "model": i.get("model", ""),
                "step": i.get("step", ""),
                "prompt_tokens": int(i.get("prompt_tokens", 0)),
                "completion_tokens": int(i.get("completion_tokens", 0)),
                "total_tokens": int(i.get("total_tokens", 0)),
                "cost_usd": float(i.get("cost_usd", 0)),
                "timestamp": float(i.get("timestamp", 0)),
                "metadata": meta,
            })
        return {
            "chat_id": chat_id,
            "summary": {
                "total_calls": len(items),
                "prompt_tokens": total_prompt,
                "completion_tokens": total_compl,
                "total_tokens": total_tok,
                "total_cost_usd": round(total_cost, 6),
            },
            "details": details,
        }

    # ---- AI Trace Store (DynamoDB) ----

    def record_trace(
        step: str, location_id: str, *, trace_id: str | None = None,
        chat_id: str | None = None, task_id: str | None = None,
        parent_trace_id: str | None = None, model: str = "", provider: str = "",
        input_data: dict | str | None = None, output_data: dict | str | None = None,
        tool_calls: list | None = None, tool_results: list | None = None,
        prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0,
        cost_usd: float = 0.0, latency_ms: int = 0, status: str = "ok",
        error: str | None = None, metadata: dict | None = None,
        started_at: float | None = None, completed_at: float | None = None,
    ) -> dict:
        now = time.time()
        tid = trace_id or str(uuid.uuid4())

        def _safe_json(obj):
            if obj is None:
                return "{}"
            if isinstance(obj, str):
                return obj
            return json.dumps(obj, ensure_ascii=False, default=str)

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

        table = _get_table()
        started = started_at or now
        completed = completed_at or now

        item: dict[str, Any] = {
            "pk": f"CHAT#{chat_id}" if chat_id else f"LOC#{location_id}",
            "sk": f"TRACE#{started:.6f}#{tid[:12]}",
            "gsi2pk": tid,
            "trace_id": tid,
            "chat_id": chat_id or "",
            "task_id": task_id or "",
            "location_id": location_id,
            "parent_trace_id": parent_trace_id or "",
            "step": step,
            "model": model,
            "provider": provider,
            "input": input_str,
            "output": output_str,
            "tool_calls": _safe_json(tool_calls),
            "tool_results": _safe_json(tool_results),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": str(cost_usd),
            "latency_ms": latency_ms,
            "status": status,
            "error": error or "",
            "metadata": _safe_json(metadata),
            "started_at": str(started),
            "completed_at": str(completed),
        }
        if task_id:
            item["gsi1pk"] = f"TASK#{task_id}"
            item["gsi1sk"] = f"TRACE#{started:.6f}"

        table.put_item(Item=item)
        return {"trace_id": tid, "step": step}

    def get_chat_traces(chat_id: str, limit: int = 200) -> list[dict]:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"CHAT#{chat_id}") & Key("sk").begins_with("TRACE#"),
            ScanIndexForward=True,
            Limit=limit,
        )
        return [_parse_trace_item(i) for i in resp.get("Items", [])]

    def get_task_traces(task_id: str, limit: int = 200) -> list[dict]:
        table = _get_table()
        resp = table.query(
            IndexName="gsi1",
            KeyConditionExpression=Key("gsi1pk").eq(f"TASK#{task_id}") & Key("gsi1sk").begins_with("TRACE#"),
            ScanIndexForward=True,
            Limit=limit,
        )
        return [_parse_trace_item(i) for i in resp.get("Items", [])]

    def get_trace(trace_id: str) -> dict | None:
        table = _get_table()
        resp = table.query(
            IndexName="gsi2",
            KeyConditionExpression=Key("gsi2pk").eq(trace_id),
            Limit=1,
        )
        items = resp.get("Items", [])
        return _parse_trace_item(items[0]) if items else None

    def get_trace_children(parent_trace_id: str) -> list[dict]:
        # For DynamoDB, this requires a scan/filter — acceptable for small datasets
        table = _get_table()
        resp = table.scan(
            FilterExpression=Attr("parent_trace_id").eq(parent_trace_id),
            Limit=100,
        )
        return [_parse_trace_item(i) for i in resp.get("Items", [])]

    def get_location_traces(location_id: str, since: float | None = None, limit: int = 100) -> dict:
        table = _get_table()
        # Step 1: get all chats for this location
        chats_resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"LOC#{location_id}") & Key("sk").begins_with("CHAT#"),
        )
        chat_ids = [i.get("chat_id") for i in chats_resp.get("Items", []) if i.get("chat_id")]

        # Step 2: gather traces from each chat
        all_traces = []
        for cid in chat_ids:
            kce = Key("pk").eq(f"CHAT#{cid}") & Key("sk").begins_with("TRACE#")
            resp = table.query(KeyConditionExpression=kce, ScanIndexForward=False, Limit=limit)
            for item in resp.get("Items", []):
                started = float(item.get("started_at", 0))
                if since and started < since:
                    continue
                all_traces.append(item)

        # Step 3: aggregate
        total_prompt = sum(int(i.get("prompt_tokens", 0)) for i in all_traces)
        total_compl = sum(int(i.get("completion_tokens", 0)) for i in all_traces)
        total_tok = sum(int(i.get("total_tokens", 0)) for i in all_traces)
        total_cost = sum(float(i.get("cost_usd", 0)) for i in all_traces)
        latencies = [int(i.get("latency_ms", 0)) for i in all_traces if int(i.get("latency_ms", 0)) > 0]
        avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0

        # Step 4: by_model breakdown
        by_model_map: dict[str, dict] = {}
        for i in all_traces:
            m = i.get("model", "")
            if not m:
                continue
            if m not in by_model_map:
                by_model_map[m] = {"model": m, "calls": 0, "total_tokens": 0, "cost_usd": 0.0, "avg_latency_ms": 0, "_lat_sum": 0, "_lat_count": 0}
            by_model_map[m]["calls"] += 1
            by_model_map[m]["total_tokens"] += int(i.get("total_tokens", 0))
            by_model_map[m]["cost_usd"] += float(i.get("cost_usd", 0))
            lat = int(i.get("latency_ms", 0))
            if lat > 0:
                by_model_map[m]["_lat_sum"] += lat
                by_model_map[m]["_lat_count"] += 1
        by_model = []
        for v in sorted(by_model_map.values(), key=lambda x: x["cost_usd"], reverse=True):
            v["avg_latency_ms"] = round(v["_lat_sum"] / v["_lat_count"], 1) if v["_lat_count"] else 0
            v["cost_usd"] = round(v["cost_usd"], 6)
            del v["_lat_sum"]
            del v["_lat_count"]
            by_model.append(v)

        # Step 5: recent traces (parsed)
        all_traces.sort(key=lambda x: float(x.get("started_at", 0)), reverse=True)
        recent = [_parse_trace_item(i) for i in all_traces[:limit]]

        return {
            "location_id": location_id,
            "summary": {
                "total_calls": len(all_traces),
                "prompt_tokens": total_prompt,
                "completion_tokens": total_compl,
                "total_tokens": total_tok,
                "total_cost_usd": round(total_cost, 6),
                "avg_latency_ms": avg_latency,
            },
            "by_model": by_model,
            "recent": recent,
        }

    def get_location_costs(location_id: str, since: float | None = None) -> dict:
        table = _get_table()
        kce = Key("gsi1pk").eq(f"LOC#{location_id}#COST")
        if since:
            kce = kce & Key("gsi1sk").gte(str(since))
        resp = table.query(
            IndexName="gsi1",
            KeyConditionExpression=kce,
            ScanIndexForward=False,
        )
        items = resp.get("Items", [])
        total_prompt = sum(int(i.get("prompt_tokens", 0)) for i in items)
        total_compl = sum(int(i.get("completion_tokens", 0)) for i in items)
        total_tok = sum(int(i.get("total_tokens", 0)) for i in items)
        total_cost = sum(float(i.get("cost_usd", 0)) for i in items)

        # by_model breakdown
        by_model_map: dict[str, dict] = {}
        for i in items:
            m = i.get("model", "unknown")
            if m not in by_model_map:
                by_model_map[m] = {"model": m, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
            by_model_map[m]["calls"] += 1
            by_model_map[m]["prompt_tokens"] += int(i.get("prompt_tokens", 0))
            by_model_map[m]["completion_tokens"] += int(i.get("completion_tokens", 0))
            by_model_map[m]["total_tokens"] += int(i.get("total_tokens", 0))
            by_model_map[m]["cost_usd"] += float(i.get("cost_usd", 0))

        by_model = sorted(by_model_map.values(), key=lambda x: x["cost_usd"], reverse=True)

        return {
            "location_id": location_id,
            "summary": {
                "total_calls": len(items),
                "prompt_tokens": total_prompt,
                "completion_tokens": total_compl,
                "total_tokens": total_tok,
                "total_cost_usd": round(total_cost, 6),
            },
            "by_model": by_model,
        }

    def _parse_trace_item(item: dict) -> dict:
        d = dict(item)
        for field in ("input", "output", "tool_calls", "tool_results", "metadata"):
            try:
                val = d.get(field, "{}")
                if isinstance(val, str):
                    d[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
        for field in ("started_at", "completed_at", "timestamp", "cost_usd"):
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = float(d[field])
                except (ValueError, TypeError):
                    pass
        for field in ("prompt_tokens", "completion_tokens", "total_tokens", "latency_ms"):
            if field in d:
                try:
                    d[field] = int(d[field])
                except (ValueError, TypeError):
                    pass
        return d

    # We also need to create the CHAT#META item when creating a chat
    _original_create_chat = create_chat

    def create_chat(location_id: str, model: str = "claude-sonnet-4.5", title: str = "") -> dict:
        result = _original_create_chat(location_id, model, title)
        # Also store a META item for reverse lookup by chat_id
        table = _get_table()
        table.put_item(Item={
            "pk": f"CHAT#{result['chat_id']}",
            "sk": "META",
            "chat_id": result["chat_id"],
            "location_id": location_id,
            "title": title,
            "model": model,
            "created_at": str(result["created_at"]),
            "updated_at": str(result["updated_at"]),
            "message_count": 0,
        })
        return result

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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_location ON chats(location_id, updated_at)")
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_costs_chat ON llm_costs(chat_id, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_costs_location ON llm_costs(location_id, timestamp)")
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

    # ---- Chat CRUD (SQLite) ----

    def create_chat(location_id: str, model: str = "claude-sonnet-4.5", title: str = "") -> dict:
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
        db = _get_db()
        row = db.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        if not row:
            db.close()
            return None
        msg_count = db.execute("SELECT COUNT(*) as cnt FROM messages WHERE chat_id = ?", (chat_id,)).fetchone()["cnt"]
        db.close()
        return {**dict(row), "message_count": msg_count}

    def list_chats(location_id: str, limit: int = 50) -> list[dict]:
        db = _get_db()
        rows = db.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.chat_id = c.chat_id) as message_count "
            "FROM chats c WHERE c.location_id = ? ORDER BY c.updated_at DESC LIMIT ?",
            (location_id, limit),
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def delete_chat(chat_id: str) -> bool:
        db = _get_db()
        db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        result = db.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
        db.commit()
        deleted = result.rowcount > 0
        db.close()
        return deleted

    def update_chat(chat_id: str, **kwargs) -> bool:
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

    # ---- Message CRUD (SQLite) ----

    def add_message(chat_id: str, role: str, content: str, metadata: dict | None = None) -> dict:
        now = time.time()
        meta_str = json.dumps(metadata or {}, ensure_ascii=False, default=str)
        db = _get_db()
        cursor = db.execute(
            "INSERT INTO messages (chat_id, role, content, timestamp, metadata) VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, now, meta_str),
        )
        db.execute("UPDATE chats SET updated_at = ? WHERE chat_id = ?", (now, chat_id))
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

    # ---- LLM Cost tracking (SQLite) ----

    def record_llm_cost(
        chat_id: str, location_id: str, model: str, step: str,
        prompt_tokens: int, completion_tokens: int,
        total_tokens: int | None = None, cost_usd: float | None = None,
        message_id: int | None = None, metadata: dict | None = None,
    ) -> dict:
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
        db = _get_db()
        rows = db.execute(
            "SELECT * FROM llm_costs WHERE chat_id = ? ORDER BY timestamp ASC", (chat_id,)
        ).fetchall()
        agg = db.execute(
            "SELECT COUNT(*) as calls, SUM(prompt_tokens) as prompt_tokens, "
            "SUM(completion_tokens) as completion_tokens, SUM(total_tokens) as total_tokens, "
            "SUM(cost_usd) as total_cost_usd FROM llm_costs WHERE chat_id = ?", (chat_id,)
        ).fetchone()
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
            "details": details,
        }

    # ---- AI Trace Store (SQLite) ----

    def record_trace(
        step: str, location_id: str, *, trace_id: str | None = None,
        chat_id: str | None = None, task_id: str | None = None,
        parent_trace_id: str | None = None, model: str = "", provider: str = "",
        input_data: dict | str | None = None, output_data: dict | str | None = None,
        tool_calls: list | None = None, tool_results: list | None = None,
        prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0,
        cost_usd: float = 0.0, latency_ms: int = 0, status: str = "ok",
        error: str | None = None, metadata: dict | None = None,
        started_at: float | None = None, completed_at: float | None = None,
    ) -> dict:
        now = time.time()
        tid = trace_id or str(uuid.uuid4())

        def _safe_json(obj):
            if obj is None:
                return "{}"
            if isinstance(obj, str):
                return obj
            return json.dumps(obj, ensure_ascii=False, default=str)

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
        db = _get_db()
        rows = db.execute(
            "SELECT * FROM ai_traces WHERE chat_id = ? ORDER BY started_at ASC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        db.close()
        return [_parse_trace_row(r) for r in rows]

    def get_task_traces(task_id: str, limit: int = 200) -> list[dict]:
        db = _get_db()
        rows = db.execute(
            "SELECT * FROM ai_traces WHERE task_id = ? ORDER BY started_at ASC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        db.close()
        return [_parse_trace_row(r) for r in rows]

    def get_trace(trace_id: str) -> dict | None:
        db = _get_db()
        row = db.execute("SELECT * FROM ai_traces WHERE trace_id = ?", (trace_id,)).fetchone()
        db.close()
        return _parse_trace_row(row) if row else None

    def get_trace_children(parent_trace_id: str) -> list[dict]:
        db = _get_db()
        rows = db.execute(
            "SELECT * FROM ai_traces WHERE parent_trace_id = ? ORDER BY started_at ASC",
            (parent_trace_id,),
        ).fetchall()
        db.close()
        return [_parse_trace_row(r) for r in rows]

    def get_location_traces(location_id: str, since: float | None = None, limit: int = 100) -> dict:
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
            "by_model": [dict(r) for r in by_model],
            "recent": [_parse_trace_row(r) for r in recent],
        }

    def get_location_costs(location_id: str, since: float | None = None) -> dict:
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
        }

    def _parse_trace_row(row) -> dict:
        d = dict(row)
        for field in ("input", "output", "tool_calls", "tool_results", "metadata"):
            try:
                d[field] = json.loads(d.get(field, "{}") or "{}")
            except (json.JSONDecodeError, TypeError):
                pass
        return d


# ===================================================================
# Context window — shared between both backends
# ===================================================================
def build_context_window(chat_id: str) -> list[dict]:
    all_messages = get_messages(chat_id)
    if not all_messages:
        return []
    conversation = [
        {"role": m["role"], "content": m["content"]}
        for m in all_messages
        if m["role"] in ("user", "assistant")
    ]
    if len(conversation) <= MAX_CONTEXT_MESSAGES:
        return _trim_to_char_limit(conversation)
    keep_recent = MAX_CONTEXT_MESSAGES
    old_messages = conversation[:-keep_recent]
    recent_messages = conversation[-keep_recent:]
    summary = _summarize_conversation(old_messages)
    context = [
        {"role": "user", "content": f"[Resumen de la conversacion anterior: {summary}]"},
        {"role": "assistant", "content": "Entendido, tengo el contexto de nuestra conversacion anterior."},
    ]
    context.extend(recent_messages)
    return _trim_to_char_limit(context)


def _summarize_conversation(messages: list[dict]) -> str:
    parts = []
    for i in range(0, len(messages), 2):
        user_msg = messages[i]["content"] if i < len(messages) else ""
        assistant_msg = messages[i + 1]["content"] if i + 1 < len(messages) else ""
        user_short = user_msg[:100] + "..." if len(user_msg) > 100 else user_msg
        assistant_short = assistant_msg[:150] + "..." if len(assistant_msg) > 150 else assistant_msg
        parts.append(f"- Pregunta: {user_short} -> Respuesta: {assistant_short}")
    return "\n".join(parts)


def _trim_to_char_limit(messages: list[dict]) -> list[dict]:
    total = sum(len(m["content"]) for m in messages)
    while total > MAX_CONTEXT_CHARS and len(messages) > 2:
        removed = messages.pop(0)
        total -= len(removed["content"])
    return messages
