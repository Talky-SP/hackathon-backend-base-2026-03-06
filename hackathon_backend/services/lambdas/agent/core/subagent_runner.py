"""
Subagent Runner — parallel document processing with full tool access.

The main agent calls `dispatch_subagents` to fan out work to N parallel
subagents. Each subagent:
  - Receives a batch of documents (images/PDFs via multimodal)
  - Has full tool access: dynamo_query, run_code (same sandbox)
  - Has its own iteration limit and cost budget
  - Returns structured results back to the orchestrator

The main agent consolidates all subagent results and generates the final output.

Guardrails:
  - Max subagents per dispatch: 10
  - Max iterations per subagent: 8
  - Cost budget per subagent: $0.50 (configurable)
  - Total cost budget per dispatch: $3.00
  - Timeout per subagent: 120s
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any, Callable

from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import (
    traced_completion, is_cancelled, CancelledError,
)
from hackathon_backend.services.lambdas.agent.core.query_agent import (
    _execute_query, _sanitize, _extract_source,
)
from hackathon_backend.services.lambdas.agent.core.unified_agent import (
    _safe_exec, _build_dataset_card, _audit_code_execution,
    _noop, EventCallback, ARTIFACTS_DIR,
)
from hackathon_backend.services.lambdas.agent.core.data_catalog import (
    get_schema_prompt, ALL_TABLE_NAMES,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
MAX_SUBAGENTS = 10
MAX_ITERATIONS_PER_SUBAGENT = 8
DEFAULT_BUDGET_PER_SUBAGENT = 0.50  # USD
MAX_TOTAL_BUDGET = 3.00  # USD
SUBAGENT_TIMEOUT_S = 120

# Minimal tool definitions for subagents (same as main agent, no dispatch)
_SUBAGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "dynamo_query",
            "description": "Query DynamoDB. Returns dataset card. Full items accessible via run_code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "enum": ALL_TABLE_NAMES,
                    },
                    "index_name": {"type": "string"},
                    "pk_field": {"type": "string", "default": "userId"},
                    "pk_value": {"type": "string"},
                    "sk_field": {"type": "string"},
                    "sk_condition": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["eq", "between", "begins_with", "gt", "lt"]},
                            "value": {"type": "string"},
                            "value2": {"type": "string"},
                        },
                        "required": ["op", "value"],
                    },
                    "filter_expression": {},
                    "limit": {"type": "integer"},
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": (
                "Execute Python code. Access queried data via `data` dict. "
                "Save files to `output_dir`. Libraries: openpyxl, numpy, matplotlib. "
                "Helpers: group_by(), monthly_totals(), top_n(), filter_items(), sum_field(). "
                "Assign results to `result` variable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                },
                "required": ["code"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Subagent system prompt
# ---------------------------------------------------------------------------
def _subagent_system_prompt(objective: str, location_id: str, doc_count: int) -> str:
    schema_text = get_schema_prompt()
    return f"""{schema_text}

You are a specialized subagent processing {doc_count} document(s).

OBJECTIVE: {objective}

TOOLS:
- `dynamo_query`: Query DynamoDB (locationId auto-enforced). Returns dataset card.
- `run_code`: Execute Python with data dict, output_dir, openpyxl, numpy, matplotlib.
  Helpers: group_by(), monthly_totals(), top_n(), filter_items(), sum_field().

WORKFLOW:
1. Analyze the attached documents (images/PDFs) — extract all relevant financial data.
2. If needed, query DynamoDB to cross-reference or enrich the extracted data.
3. Use run_code to structure and process the results.
4. Set `result` with your findings as a structured dict.

RESULT FORMAT — you MUST set `result` in run_code:
```python
result = {{
    "documents_processed": 3,
    "extracted_data": [
        {{
            "filename": "factura_001.pdf",
            "type": "invoice",  # invoice / receipt / payslip / bank_statement / other
            "supplier": "Proveedor ABC",
            "cif": "B12345678",
            "date": "2026-02-15",
            "base_amount": 1000.00,
            "vat_rate": 21.0,
            "vat_amount": 210.00,
            "total": 1210.00,
            "concept": "Servicios consulting",
            "line_items": [
                {{"description": "Horas consulting", "quantity": 10, "unit_price": 100.0, "total": 1000.0}}
            ],
            "matched_db_record": null,  # or dict with matching DB record if found
            "issues": [],  # any problems detected
        }},
    ],
    "summary": "Procesadas 3 facturas por un total de 5,230.00 EUR",
    "issues": [],  # cross-document issues (duplicates, missing, etc.)
}}
```

RULES:
- Extract ALL financial data from each document: amounts, dates, CIF/NIF, line items.
- If a document is unclear, note it in issues rather than guessing.
- Cross-reference with DB when possible (match by CIF, amount, date).
- NEVER invent data. Only report what you can see in the documents.
- Respond concisely. No need for long explanations.
- locationId={location_id}
- TODAY: 2026-03-22"""


# ---------------------------------------------------------------------------
# Build multimodal content with document attachments
# ---------------------------------------------------------------------------
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_PDF_EXTS = {".pdf"}

def _build_subagent_user_content(
    objective: str,
    doc_paths: list[str],
) -> list[dict]:
    """Build multimodal user message with document attachments."""
    blocks: list[dict] = [
        {"type": "text", "text": f"Process these {len(doc_paths)} document(s):\n{objective}"}
    ]

    for path in doc_paths:
        fname = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()

        if not os.path.isfile(path):
            blocks.append({"type": "text", "text": f"[ERROR: File not found: {fname}]"})
            continue

        try:
            with open(path, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode("utf-8")

            if ext in _IMAGE_EXTS:
                mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                        "gif": "image/gif", "webp": "image/webp"}.get(ext.lstrip("."), "image/jpeg")
                blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
                blocks.append({"type": "text", "text": f"[Document: {fname}]"})
            elif ext in _PDF_EXTS:
                blocks.append({
                    "type": "file",
                    "file": {"file_data": f"data:application/pdf;base64,{b64}"},
                })
                blocks.append({"type": "text", "text": f"[Document: {fname}]"})
            else:
                # Try as text
                try:
                    text = raw.decode("utf-8")[:5000]
                    blocks.append({"type": "text", "text": f"[Document: {fname}]\n{text}"})
                except UnicodeDecodeError:
                    blocks.append({"type": "text", "text": f"[Unsupported file: {fname}]"})
        except Exception as e:
            blocks.append({"type": "text", "text": f"[Error reading {fname}: {e}]"})

    return blocks


# ---------------------------------------------------------------------------
# Single subagent execution
# ---------------------------------------------------------------------------
@observe(name="subagent_run")
def run_subagent(
    subagent_id: str,
    objective: str,
    doc_paths: list[str],
    location_id: str,
    model_id: str,
    chat_id: str | None = None,
    task_id: str | None = None,
    budget_usd: float = DEFAULT_BUDGET_PER_SUBAGENT,
    max_iterations: int = MAX_ITERATIONS_PER_SUBAGENT,
    on_event: EventCallback | None = None,
) -> dict:
    """Run a single subagent with documents and full tool access.

    Returns: {
        "subagent_id": str,
        "success": bool,
        "result": dict | None,
        "artifacts": list[dict],
        "usage": list[dict],
        "error": str | None,
        "iterations": int,
        "cost_usd": float,
    }
    """
    emit = on_event or _noop
    t0 = time.time()
    cost_usd = 0.0
    usage_records: list[dict] = []
    artifacts: list[dict] = []
    query_results: dict[str, dict] = {}
    query_counter = 0
    code_retry_counts: dict[str, int] = {}

    log.info(f"[subagent:{subagent_id}] Starting: {objective[:100]}, docs={len(doc_paths)}, model={model_id}")
    emit("subagent_start", {
        "subagent_id": subagent_id,
        "objective": objective[:200],
        "doc_count": len(doc_paths),
    })

    # Build messages
    system_prompt = _subagent_system_prompt(objective, location_id, len(doc_paths))
    user_content = _build_subagent_user_content(objective, doc_paths)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    for iteration in range(max_iterations):
        # Check cancellation
        if chat_id and is_cancelled(chat_id):
            return _subagent_result(subagent_id, False, None, artifacts, usage_records,
                                    "Cancelled", iteration, cost_usd)
        if task_id and is_cancelled(task_id):
            return _subagent_result(subagent_id, False, None, artifacts, usage_records,
                                    "Cancelled", iteration, cost_usd)

        # Check budget
        if cost_usd >= budget_usd:
            log.warning(f"[subagent:{subagent_id}] Budget exceeded: ${cost_usd:.3f} >= ${budget_usd:.3f}")
            return _subagent_result(subagent_id, False, None, artifacts, usage_records,
                                    f"Budget exceeded (${cost_usd:.3f}/${budget_usd:.3f})",
                                    iteration, cost_usd)

        # Check timeout
        elapsed = time.time() - t0
        if elapsed > SUBAGENT_TIMEOUT_S:
            log.warning(f"[subagent:{subagent_id}] Timeout: {elapsed:.0f}s > {SUBAGENT_TIMEOUT_S}s")
            return _subagent_result(subagent_id, False, None, artifacts, usage_records,
                                    f"Timeout ({elapsed:.0f}s)", iteration, cost_usd)

        emit("subagent_thinking", {
            "subagent_id": subagent_id,
            "iteration": iteration + 1,
        })

        response = traced_completion(
            model_id=model_id,
            messages=messages,
            step=f"subagent_{subagent_id}_iter_{iteration + 1}",
            chat_id=chat_id,
            task_id=task_id,
            location_id=location_id,
            tools=_SUBAGENT_TOOLS,
            temperature=0.1,
        )

        # Track usage
        u = getattr(response, "usage", None)
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        completion_tokens = getattr(u, "completion_tokens", 0) or 0
        total_tokens = getattr(u, "total_tokens", 0) or 0
        usage_records.append({
            "model": model_id,
            "step": f"subagent_{subagent_id}_iter_{iteration + 1}",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        })

        # Estimate cost
        from hackathon_backend.services.lambdas.agent.core.chat_store import _estimate_cost
        iter_cost = _estimate_cost(model_id, prompt_tokens, completion_tokens, 0, 0)
        cost_usd += iter_cost

        choice = response.choices[0]

        # Done — no tool calls
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            log.info(f"[subagent:{subagent_id}] Done after {iteration + 1} iterations, cost=${cost_usd:.3f}")
            # Try to parse result from final text
            final_text = choice.message.content or ""
            return _subagent_result(subagent_id, True, {"text_response": final_text},
                                    artifacts, usage_records, None, iteration + 1, cost_usd)

        # Process tool calls
        messages.append(choice.message)

        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            tc_id = tool_call.id
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                messages.append({"role": "tool", "tool_call_id": tc_id,
                                 "content": json.dumps({"error": "Invalid JSON"})})
                continue

            # --- dynamo_query ---
            if fn_name == "dynamo_query":
                query_counter += 1
                query_key = f"sq_{subagent_id}_{query_counter}"
                table_name = args["table_name"]

                log.info(f"[subagent:{subagent_id}] dynamo_query: {table_name}")
                emit("subagent_query", {
                    "subagent_id": subagent_id,
                    "table": table_name,
                    "query_key": query_key,
                })

                result = _execute_query(
                    table_name=table_name,
                    location_id=location_id,
                    index_name=args.get("index_name"),
                    pk_field=args.get("pk_field", "userId"),
                    pk_value=args.get("pk_value"),
                    sk_field=args.get("sk_field"),
                    sk_condition=args.get("sk_condition"),
                    filter_expression=args.get("filter_expression"),
                    limit=args.get("limit"),
                )
                query_results[query_key] = result

                if result.get("success"):
                    card = _build_dataset_card(query_key, result, table_name)
                    messages.append({"role": "tool", "tool_call_id": tc_id,
                                     "content": json.dumps(card, ensure_ascii=False, default=str)})
                else:
                    messages.append({"role": "tool", "tool_call_id": tc_id,
                                     "content": json.dumps({"error": result.get("error", "Query failed")})})

            # --- run_code ---
            elif fn_name == "run_code":
                code = args.get("code", "")
                file_task_id = task_id or f"sub_{subagent_id}"

                log.info(f"[subagent:{subagent_id}] run_code: {code[:100]}...")
                emit("subagent_code", {
                    "subagent_id": subagent_id,
                    "code_preview": code[:200],
                })

                exec_result = _safe_exec(code, query_results, file_task_id)

                _audit_code_execution(
                    task_id=file_task_id, code=code, query_results=query_results,
                    result=exec_result.get("result"), error=exec_result.get("error"),
                    elapsed_ms=exec_result.get("elapsed_ms", 0),
                    files=exec_result.get("files"),
                    location_id=location_id, chat_id=chat_id,
                )

                if not exec_result["success"]:
                    retry_key = "run_code_failures"
                    code_retry_counts[retry_key] = code_retry_counts.get(retry_key, 0) + 1

                    if code_retry_counts[retry_key] >= 3:
                        messages.append({"role": "tool", "tool_call_id": tc_id,
                                         "content": json.dumps({
                                             "error": exec_result["error"],
                                             "fatal": True,
                                             "message": "Code failed 3 times. Return what you have so far.",
                                         })})
                    else:
                        messages.append({"role": "tool", "tool_call_id": tc_id,
                                         "content": json.dumps({
                                             "error": exec_result["error"],
                                             "attempt": code_retry_counts[retry_key],
                                             "max_attempts": 3,
                                             "hint": "Fix the code and try again.",
                                         })})
                    continue

                result_val = exec_result.get("result")
                generated_files = exec_result.get("files", [])

                # Collect artifacts
                for f in generated_files:
                    artifacts.append({
                        "filename": f["filename"],
                        "path": f["path"],
                        "task_id": file_task_id,
                        "type": f.get("type", "file"),
                        "size_bytes": f.get("size_bytes", 0),
                    })

                # If result has data, this subagent is done
                if isinstance(result_val, dict) and result_val.get("extracted_data") is not None:
                    log.info(f"[subagent:{subagent_id}] Got structured result with {len(result_val.get('extracted_data', []))} docs")
                    return _subagent_result(subagent_id, True, result_val,
                                            artifacts, usage_records, None,
                                            iteration + 1, cost_usd)

                # Build tool response
                tool_resp: dict[str, Any] = {"success": True}
                if result_val is not None:
                    tool_resp["result"] = result_val
                if generated_files:
                    tool_resp["files"] = [{"filename": f["filename"]} for f in generated_files]
                tool_resp["elapsed_ms"] = exec_result.get("elapsed_ms", 0)

                messages.append({"role": "tool", "tool_call_id": tc_id,
                                 "content": json.dumps(tool_resp, ensure_ascii=False, default=str)})
            else:
                messages.append({"role": "tool", "tool_call_id": tc_id,
                                 "content": json.dumps({"error": f"Unknown tool: {fn_name}"})})

    # Max iterations reached
    log.warning(f"[subagent:{subagent_id}] Max iterations ({max_iterations}) reached")
    return _subagent_result(subagent_id, False, None, artifacts, usage_records,
                            f"Max iterations ({max_iterations}) reached",
                            max_iterations, cost_usd)


def _subagent_result(
    subagent_id: str, success: bool, result: dict | None,
    artifacts: list[dict], usage: list[dict],
    error: str | None, iterations: int, cost_usd: float,
) -> dict:
    return {
        "subagent_id": subagent_id,
        "success": success,
        "result": result,
        "artifacts": artifacts,
        "usage": usage,
        "error": error,
        "iterations": iterations,
        "cost_usd": round(cost_usd, 4),
    }


# ---------------------------------------------------------------------------
# Dispatch — orchestrates parallel subagents
# ---------------------------------------------------------------------------
@observe(name="dispatch_subagents")
def dispatch_subagents(
    subtasks: list[dict],
    location_id: str,
    model_id: str,
    chat_id: str | None = None,
    task_id: str | None = None,
    max_parallel: int = 5,
    budget_per_subagent: float = DEFAULT_BUDGET_PER_SUBAGENT,
    on_event: EventCallback | None = None,
) -> dict:
    """Dispatch multiple subagents in parallel.

    Each subtask: {"objective": str, "documents": [path1, path2, ...]}

    Returns: {
        "success": bool,
        "subagent_results": [subagent_result, ...],
        "total_cost_usd": float,
        "total_tokens": int,
        "documents_processed": int,
        "summary": str,
    }
    """
    emit = on_event or _noop
    t0 = time.time()

    # Enforce limits
    if len(subtasks) > MAX_SUBAGENTS:
        log.warning(f"[dispatch] Capping {len(subtasks)} subtasks to {MAX_SUBAGENTS}")
        subtasks = subtasks[:MAX_SUBAGENTS]

    total_budget = budget_per_subagent * len(subtasks)
    if total_budget > MAX_TOTAL_BUDGET:
        budget_per_subagent = MAX_TOTAL_BUDGET / len(subtasks)
        log.info(f"[dispatch] Adjusted per-subagent budget to ${budget_per_subagent:.3f}")

    max_parallel = min(max_parallel, len(subtasks), MAX_SUBAGENTS)

    total_docs = sum(len(st.get("documents", [])) for st in subtasks)
    log.info(f"[dispatch] {len(subtasks)} subagents, {total_docs} docs, max_parallel={max_parallel}, "
             f"budget=${budget_per_subagent:.2f}/agent, model={model_id}")

    emit("dispatch_start", {
        "subagent_count": len(subtasks),
        "total_documents": total_docs,
        "max_parallel": max_parallel,
        "budget_per_subagent": budget_per_subagent,
    })

    results: list[dict] = []

    def _run_one(idx: int, subtask: dict) -> dict:
        subagent_id = f"sa_{idx + 1}_{uuid.uuid4().hex[:6]}"
        return run_subagent(
            subagent_id=subagent_id,
            objective=subtask["objective"],
            doc_paths=subtask.get("documents", []),
            location_id=location_id,
            model_id=model_id,
            chat_id=chat_id,
            task_id=task_id,
            budget_usd=budget_per_subagent,
            on_event=on_event,
        )

    # Run in parallel with thread pool
    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = {
            executor.submit(_run_one, i, st): i
            for i, st in enumerate(subtasks)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results.append(result)
                emit("subagent_complete", {
                    "subagent_id": result["subagent_id"],
                    "success": result["success"],
                    "cost_usd": result["cost_usd"],
                    "completed": len(results),
                    "total": len(subtasks),
                })
            except Exception as e:
                log.error(f"[dispatch] Subagent {idx} crashed: {e}")
                results.append({
                    "subagent_id": f"sa_{idx + 1}_error",
                    "success": False,
                    "result": None,
                    "artifacts": [],
                    "usage": [],
                    "error": str(e),
                    "iterations": 0,
                    "cost_usd": 0,
                })

    # Sort by original order
    elapsed_s = time.time() - t0
    total_cost = sum(r.get("cost_usd", 0) for r in results)
    total_tokens = sum(
        sum(u.get("total_tokens", 0) for u in r.get("usage", []))
        for r in results
    )
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    summary = (
        f"Dispatched {len(subtasks)} subagents ({total_docs} documents). "
        f"{len(successful)} succeeded, {len(failed)} failed. "
        f"Cost: ${total_cost:.3f}. Time: {elapsed_s:.1f}s."
    )

    log.info(f"[dispatch] {summary}")
    emit("dispatch_complete", {
        "success": len(successful) > 0,
        "total_subagents": len(subtasks),
        "successful": len(successful),
        "failed": len(failed),
        "total_cost_usd": total_cost,
        "elapsed_s": elapsed_s,
    })

    return {
        "success": len(successful) > 0,
        "subagent_results": results,
        "total_cost_usd": round(total_cost, 4),
        "total_tokens": total_tokens,
        "documents_processed": total_docs,
        "summary": summary,
    }
