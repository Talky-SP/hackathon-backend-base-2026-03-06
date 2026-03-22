"""
Task Executor — runs complex tasks with sub-agents and code execution.

Flow:
1. Receives task definition (type, description, location_id)
2. Plans execution steps (either hardcoded templates or LLM-planned)
3. Runs sub-agents in parallel where possible (reusing query_agent)
4. Executes analysis code with full data context
5. Generates output artifacts (Excel, charts)
6. Reports progress via callback for WebSocket streaming

Sub-agent guardrails:
- Max iterations per sub-agent: 10
- Cost budget enforced before each LLM call
- Timeout per sub-agent and per task
- Max sub-agents running in parallel: configured per task type
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any, Callable

from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import traced_completion, is_cancelled, CancelledError
from hackathon_backend.services.lambdas.agent.core.query_agent import (
    _execute_query, _execute_code, _sanitize, _extract_source,
)
from hackathon_backend.services.lambdas.agent.core.unified_agent import (
    _safe_exec, _build_dataset_card, _validate_generated_files,
    _audit_code_execution, UNIFIED_TOOLS, ARTIFACTS_DIR as _UA_ARTIFACTS_DIR,
)
from hackathon_backend.services.lambdas.agent.core.data_catalog import (
    get_schema_prompt, ALL_TABLE_NAMES,
)
from hackathon_backend.services.lambdas.agent.core.playbooks import (
    get_playbook_guidance,
)
from hackathon_backend.services.lambdas.agent.core.chat_store import record_llm_cost
from hackathon_backend.services.lambdas.agent.core.task_manager import (
    update_task_status, add_task_cost, add_task_artifact,
    add_task_step, update_task_step, check_budget,
    TASK_COST_LIMITS,
)
from hackathon_backend.services.lambdas.agent.core.tools.excel_gen import (
    generate_table_excel, generate_cash_flow_excel, generate_modelo_303_excel,
    list_artifacts,
)
from hackathon_backend.services.lambdas.agent.core.code_runner import (
    run_code_execution, build_excel_prompt, collect_sandbox_files,
    CODE_EXEC_SYSTEM,
)

EventCallback = Callable[[str, dict], None]


def _noop(event: str, data: dict) -> None:
    pass


# ---------------------------------------------------------------------------
# Sub-agent: runs a focused query + analysis task
# ---------------------------------------------------------------------------
@observe(name="sub_agent")
def run_sub_agent(
    task_id: str,
    location_id: str,
    step_id: int,
    objective: str,
    model_id: str = "claude-sonnet-4.5",
    max_iterations: int = 10,
    on_event: EventCallback | None = None,
) -> dict:
    """
    Run a sub-agent that can query DynamoDB and run code.
    Uses v2 tools: dynamo_query (dataset cards) + run_code (sandboxed exec).
    Returns: {"success": bool, "data": dict, "usage": list[dict], "sources": list[dict]}
    """
    emit = on_event or _noop
    usage_records: list[dict] = []
    sources: list[dict] = []

    # Build sub-agent system prompt with schema from data_catalog
    schema = get_schema_prompt()
    system_prompt = f"""{schema}

You are a sub-agent working as part of a larger financial analysis task.
Your tools: dynamo_query (returns dataset cards with stats, not raw items),
run_code (sandboxed Python with full data access).

In run_code:
- Access data via: items = data['query_1']['items']
- Helpers available: group_by(), monthly_totals(), top_n(), filter_items(), sum_field()
- Libraries: pandas (pd), openpyxl, matplotlib (plt), numpy (np), json, Decimal, datetime
- Assign results to `result` variable (dict with 'answer' key)
- ALWAYS generate .xlsx (Excel) files using openpyxl, NEVER .csv
- Save files to output_dir: f'{output_dir}/report.xlsx'

YOUR SPECIFIC OBJECTIVE:
{objective}

Return your results by calling run_code with a structured result dict.
Focus ONLY on your objective — do not try to answer the full user question."""

    # Use UNIFIED_TOOLS (dynamo_query + run_code + edit_file)
    sub_agent_tools = [t for t in UNIFIED_TOOLS if t["function"]["name"] in ("dynamo_query", "run_code")]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": objective},
    ]

    query_results: dict[str, dict] = {}
    query_counter = 0
    code_retry_counts: dict[str, int] = {}

    for iteration in range(max_iterations):
        if is_cancelled(task_id):
            raise CancelledError(f"Task {task_id} cancelled")

        budget = check_budget(task_id)
        if not budget["ok"]:
            return {
                "success": False,
                "data": {"error": f"Budget exceeded: {budget.get('reason', '')}"},
                "usage": usage_records,
                "sources": sources,
            }

        update_task_step(step_id, "RUNNING",
                         result_summary=f"Iteration {iteration + 1}/{max_iterations}")

        response = traced_completion(
            model_id=model_id,
            messages=messages,
            step=f"sub_agent_iter_{iteration + 1}",
            task_id=task_id,
            location_id=location_id,
            tools=sub_agent_tools,
            temperature=0.1,
        )

        u = getattr(response, "usage", None)
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        completion_tokens = getattr(u, "completion_tokens", 0) or 0
        total_tokens = getattr(u, "total_tokens", 0) or 0

        usage_records.append({
            "model": model_id,
            "step": f"sub_agent_iter_{iteration + 1}",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        })

        from hackathon_backend.services.lambdas.agent.core.chat_store import _estimate_cost
        cost = _estimate_cost(model_id, prompt_tokens, completion_tokens)
        add_task_cost(task_id, total_tokens, cost)

        choice = response.choices[0]

        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            text = choice.message.content or ""
            update_task_step(step_id, "COMPLETED", result_summary=text[:200])
            return {
                "success": True,
                "data": _try_parse_json(text),
                "raw_text": text,
                "usage": usage_records,
                "sources": sources,
            }

        messages.append(choice.message)

        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            tc_id = tool_call.id
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                messages.append({
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps({"error": "Invalid JSON"}),
                })
                continue

            if fn_name == "dynamo_query":
                query_counter += 1
                query_key = f"query_{query_counter}"
                table_name = args["table_name"]

                emit("task_progress", {
                    "task_id": task_id,
                    "message": f"Consultando {table_name.replace('_', ' ')}...",
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

                if result.get("success") and table_name in ("User_Expenses", "User_Invoice_Incomes"):
                    for item in result["items"]:
                        src = _extract_source(item)
                        if src:
                            sources.append(src)

                # Return dataset card instead of raw items
                if result.get("success"):
                    response_for_llm = _build_dataset_card(query_key, result, table_name)
                else:
                    response_for_llm = {
                        "query_key": query_key, "success": False,
                        "table": table_name,
                        "error": result.get("error", "Unknown"),
                    }

                messages.append({
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps(response_for_llm, ensure_ascii=False, default=str),
                })

            elif fn_name == "run_code":
                code = args.get("code", "")
                file_task_id = task_id

                emit("task_progress", {
                    "task_id": task_id,
                    "message": "Ejecutando codigo de analisis...",
                })

                exec_result = _safe_exec(code, query_results, file_task_id)
                elapsed_ms = exec_result.get("elapsed_ms", 0)

                _audit_code_execution(
                    task_id=file_task_id, code=code, query_results=query_results,
                    result=exec_result.get("result"), error=exec_result.get("error"),
                    elapsed_ms=elapsed_ms, files=exec_result.get("files"),
                    location_id=location_id,
                )

                if not exec_result["success"]:
                    retry_key = "run_code_failures"
                    code_retry_counts[retry_key] = code_retry_counts.get(retry_key, 0) + 1
                    if code_retry_counts[retry_key] >= 3:
                        messages.append({
                            "role": "tool", "tool_call_id": tc_id,
                            "content": json.dumps({
                                "error": exec_result["error"], "fatal": True,
                                "message": "Code failed 3 times. Return error to user.",
                            }),
                        })
                    else:
                        messages.append({
                            "role": "tool", "tool_call_id": tc_id,
                            "content": json.dumps({
                                "error": exec_result["error"],
                                "attempt": code_retry_counts[retry_key],
                                "max_attempts": 3,
                                "hint": "Fix the code and try again.",
                            }),
                        })
                    continue

                result_val = exec_result.get("result")
                generated_files = exec_result.get("files", [])

                # Validate and register files
                if generated_files:
                    generated_files = _validate_generated_files(generated_files)
                    for f in generated_files:
                        add_task_artifact(task_id, {
                            "filename": f["filename"], "path": f["path"],
                            "type": f.get("type", "file"),
                            "size_bytes": f.get("size_bytes", 0),
                        })

                # If result has 'answer' or 'data', return immediately
                if isinstance(result_val, dict) and ("answer" in result_val or "data" in result_val):
                    update_task_step(step_id, "COMPLETED",
                                     result_summary=str(result_val.get("answer", ""))[:200])
                    return {
                        "success": True,
                        "data": result_val,
                        "usage": usage_records,
                        "sources": sources,
                    }

                tool_response = {"success": True}
                if result_val is not None:
                    tool_response["result"] = result_val
                if generated_files:
                    tool_response["files"] = [f["filename"] for f in generated_files]

                messages.append({
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps(tool_response, ensure_ascii=False, default=str),
                })

            else:
                messages.append({
                    "role": "tool", "tool_call_id": tc_id,
                    "content": json.dumps({"error": f"Unknown tool: {fn_name}"}),
                })

    update_task_step(step_id, "COMPLETED", result_summary="Max iterations reached")
    return {
        "success": True,
        "data": query_results,
        "usage": usage_records,
        "sources": sources,
    }


# No hardcoded plans — the AI generates plans dynamically via _create_custom_plan


# ---------------------------------------------------------------------------
# Main task executor
# ---------------------------------------------------------------------------
@observe(name="execute_task")
def execute_task(
    task_id: str,
    location_id: str,
    task_type: str,
    description: str = "",
    model_id: str = "claude-sonnet-4.5",
    on_event: EventCallback | None = None,
    uploaded_files: list[str] | None = None,
) -> dict:
    """
    Execute a complex task end-to-end.

    Returns:
    {
        "success": bool,
        "summary": str,
        "artifacts": list[dict],
        "sources": list[dict],
        "cost_usd": float,
        "total_tokens": int,
    }
    """
    emit = on_event or _noop
    all_sources: list[dict] = []
    all_usage: list[dict] = []

    try:
        # Step 0: Mark task as running
        update_task_status(task_id, "RUNNING", progress=0)
        emit("task_progress", {"task_id": task_id, "progress": 0, "step": "Iniciando tarea..."})

        # Step 1: Process uploaded documents if any
        doc_context = ""
        if uploaded_files:
            emit("task_progress", {"task_id": task_id, "progress": 5, "step": "Procesando documentos..."})
            doc_context = _process_uploads(task_id, uploaded_files, model_id, all_usage, emit)

        # Step 2: AI generates the execution plan dynamically
        plan = _create_custom_plan(task_id, task_type, description, doc_context, model_id, all_usage, emit)

        # Step 3: Create task steps
        for i, step_def in enumerate(plan):
            add_task_step(task_id, i + 1, step_def["description"], step_def["name"])

        # Step 4: Execute steps (parallel where possible)
        emit("task_progress", {"task_id": task_id, "progress": 10, "step": "Ejecutando consultas..."})
        step_results = _execute_plan(
            task_id, location_id, plan, model_id, emit, all_usage, all_sources,
        )

        # Step 5: Synthesize results with LLM
        emit("task_progress", {"task_id": task_id, "progress": 70, "step": "Sintetizando resultados..."})
        synthesis = _synthesize_results(
            task_id, task_type, description, step_results, doc_context,
            model_id, all_usage, emit,
        )

        # Step 6: Generate Excel artifact via native code execution
        emit("task_progress", {"task_id": task_id, "progress": 85, "step": "Generando informe Excel (code execution)..."})
        artifacts = _generate_artifacts_with_code_exec(
            task_id, task_type, synthesis, description, model_id, all_usage, emit,
        )

        # Step 7: Complete
        summary = synthesis.get("summary", "Tarea completada")
        cost_info = check_budget(task_id)

        update_task_status(task_id, "COMPLETED", progress=100, result_summary=summary)
        emit("task_completed", {
            "task_id": task_id,
            "summary": summary,
            "artifacts": artifacts,
            "cost_usd": cost_info.get("cost_usd", 0),
        })

        return {
            "success": True,
            "summary": summary,
            "artifacts": artifacts,
            "sources": all_sources,
            "cost_usd": cost_info.get("cost_usd", 0),
            "total_tokens": sum(u.get("total_tokens", 0) for u in all_usage),
        }

    except CancelledError:
        update_task_status(task_id, "CANCELLED")
        emit("task_cancelled", {"task_id": task_id, "message": "Tarea cancelada por el usuario"})
        return {
            "success": False,
            "summary": "Tarea cancelada por el usuario",
            "error": "cancelled",
            "artifacts": [],
            "sources": all_sources,
            "cost_usd": 0,
            "total_tokens": sum(u.get("total_tokens", 0) for u in all_usage),
        }
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        update_task_status(task_id, "FAILED", error=error_msg)
        emit("task_failed", {"task_id": task_id, "error": error_msg})
        return {
            "success": False,
            "summary": "",
            "error": error_msg,
            "artifacts": [],
            "sources": all_sources,
            "cost_usd": 0,
            "total_tokens": sum(u.get("total_tokens", 0) for u in all_usage),
        }


# ---------------------------------------------------------------------------
# Internal: execute plan steps
# ---------------------------------------------------------------------------
def _execute_plan(
    task_id: str,
    location_id: str,
    plan: list[dict],
    model_id: str,
    emit: EventCallback,
    all_usage: list,
    all_sources: list,
) -> dict[str, dict]:
    """Execute plan steps, running parallel groups concurrently."""
    results: dict[str, dict] = {}

    # Group steps by parallel_group
    groups: dict[int, list[dict]] = {}
    for i, step in enumerate(plan):
        g = step.get("parallel_group", i + 100)
        groups.setdefault(g, []).append((i, step))

    total_steps = len(plan)
    completed = 0

    for group_id in sorted(groups.keys()):
        group_steps = groups[group_id]

        if len(group_steps) == 1:
            # Single step — run directly
            idx, step_def = group_steps[0]
            step_ids = _get_step_ids(task_id)
            step_id = step_ids[idx] if idx < len(step_ids) else 0

            emit("task_progress", {
                "task_id": task_id,
                "progress": 10 + int(60 * completed / total_steps),
                "step": step_def["description"],
            })

            result = run_sub_agent(
                task_id=task_id,
                location_id=location_id,
                step_id=step_id,
                objective=step_def["objective"],
                model_id=model_id,
                on_event=emit,
            )
            results[step_def["name"]] = result
            all_usage.extend(result.get("usage", []))
            all_sources.extend(result.get("sources", []))
            completed += 1
        else:
            # Multiple steps — run in parallel with threads
            step_ids = _get_step_ids(task_id)

            def _run_step(idx_step):
                idx, step_def = idx_step
                sid = step_ids[idx] if idx < len(step_ids) else 0
                return step_def["name"], run_sub_agent(
                    task_id=task_id,
                    location_id=location_id,
                    step_id=sid,
                    objective=step_def["objective"],
                    model_id=model_id,
                    on_event=emit,
                )

            emit("task_progress", {
                "task_id": task_id,
                "progress": 10 + int(60 * completed / total_steps),
                "step": f"Ejecutando {len(group_steps)} consultas en paralelo...",
            })

            limits = TASK_COST_LIMITS.get("custom", {})
            max_workers = min(len(group_steps), limits.get("max_agents", 5))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = list(executor.map(_run_step, group_steps))

            for name, result in futures:
                results[name] = result
                all_usage.extend(result.get("usage", []))
                all_sources.extend(result.get("sources", []))
                completed += 1

    return results


def _get_step_ids(task_id: str) -> list[int]:
    """Get step IDs for a task in order."""
    from hackathon_backend.services.lambdas.agent.core.task_manager import get_task_steps
    steps = get_task_steps(task_id)
    return [s["id"] for s in steps]


# ---------------------------------------------------------------------------
# Internal: synthesize results into final output
# ---------------------------------------------------------------------------
@observe(name="synthesize_task_results")
def _synthesize_results(
    task_id: str,
    task_type: str,
    description: str,
    step_results: dict[str, dict],
    doc_context: str,
    model_id: str,
    all_usage: list,
    emit: EventCallback,
) -> dict:
    """Use LLM to synthesize sub-agent results into final output."""
    # Build context from step results
    context_parts = []
    for name, result in step_results.items():
        data = result.get("data", {})
        raw = result.get("raw_text", "")
        summary = json.dumps(data, ensure_ascii=False, default=str)[:3000] if data else raw[:3000]
        context_parts.append(f"### {name}\n{summary}")

    results_text = "\n\n".join(context_parts)

    synthesis_prompt = f"""You are a financial analyst synthesizing data for a report.

TASK TYPE: {task_type}
DESCRIPTION: {description}
{"DOCUMENT CONTEXT: " + doc_context[:2000] if doc_context else ""}

SUB-AGENT RESULTS:
{results_text}

Based on these results, produce a JSON response with:
{{
    "summary": "Brief text summary of findings (in Spanish)",
    "excel_data": {{
        "sheets": [
            {{
                "name": "Sheet Name",
                "headers": ["Col1", "Col2", ...],
                "rows": [[val1, val2, ...], ...],
                "currency_cols": [2, 3],
                "total_row": [null, "TOTAL", 1234.56, ...],
                "chart": {{"type": "bar|line|pie", "title": "Title", "data_col": 2, "label_col": 1}}
            }}
        ]
    }},
    "chart_data": {{
        "type": "bar|line|pie",
        "title": "Chart Title",
        "labels": [...],
        "datasets": [{{"label": "...", "data": [...]}}]
    }},
    "key_metrics": {{
        "metric_name": value,
        ...
    }}
}}

For cash_flow_forecast, also include:
  "cash_flow": {{"weeks": [...], "inflows": [...], "outflows": [...], "opening_balance": 0}}

For modelo_303, also include:
  "modelo_303": {{"period": "...", "iva_repercutido": [...], "iva_soportado": [...], "operaciones_especiales": {{}}}}

IMPORTANT: Use real numbers from the data. Format amounts as numbers (not strings). Spanish language for text."""

    budget = check_budget(task_id)
    if not budget.get("ok"):
        return {"summary": "Presupuesto agotado. Resultados parciales disponibles.", "excel_data": {"sheets": []}}

    response = traced_completion(
        model_id=model_id,
        messages=[{"role": "user", "content": synthesis_prompt}],
        step="synthesis",
        task_id=task_id,
        temperature=0.1,
    )

    u = getattr(response, "usage", None)
    prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
    completion_tokens = getattr(u, "completion_tokens", 0) or 0
    total_tokens = getattr(u, "total_tokens", 0) or 0
    all_usage.append({
        "model": model_id, "step": "synthesis",
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    })

    from hackathon_backend.services.lambdas.agent.core.chat_store import _estimate_cost
    cost = _estimate_cost(model_id, prompt_tokens, completion_tokens)
    add_task_cost(task_id, total_tokens, cost)

    text = response.choices[0].message.content or ""
    return _try_parse_json(text)


# ---------------------------------------------------------------------------
# Internal: generate artifacts via native code execution (primary)
# ---------------------------------------------------------------------------
def _generate_artifacts_with_code_exec(
    task_id: str,
    task_type: str,
    synthesis: dict,
    description: str,
    model_id: str,
    all_usage: list,
    emit: EventCallback,
) -> list[dict]:
    """
    Generate Excel/PDF artifacts using the LLM's native code execution sandbox.

    The LLM writes and runs openpyxl code to create professional Excel files
    with charts, formatting, and multiple sheets — dynamically, based on data.

    Falls back to template-based generation if code execution fails.
    """
    artifacts = []

    # Check budget before code execution
    budget = check_budget(task_id)
    if not budget.get("ok"):
        emit("task_progress", {"task_id": task_id, "step": "Budget exceeded, skipping artifact generation"})
        return _generate_artifacts_fallback(task_id, task_type, synthesis, emit)

    try:
        # Build the prompt for code execution
        prompt = build_excel_prompt(task_type, synthesis, description)

        emit("task_progress", {
            "task_id": task_id, "progress": 88,
            "step": f"Ejecutando código para generar Excel ({model_id})...",
        })

        result = run_code_execution(
            prompt=prompt,
            model_id=model_id,
            task_id=task_id,
            system_prompt=CODE_EXEC_SYSTEM,
        )

        if result.get("usage"):
            usage = result["usage"]
            if isinstance(usage, list):
                all_usage.extend(usage)
            else:
                all_usage.append(usage)
            # Track cost
            from hackathon_backend.services.lambdas.agent.core.chat_store import _estimate_cost
            u = usage if isinstance(usage, dict) else (usage[0] if usage else {})
            cost = _estimate_cost(
                u.get("model", model_id),
                u.get("prompt_tokens", 0),
                u.get("completion_tokens", 0),
            )
            total_tokens = u.get("total_tokens", 0)
            add_task_cost(task_id, total_tokens, cost)

        if result.get("success") and result.get("files"):
            # Files downloaded from sandbox
            for f in result["files"]:
                artifact = {
                    "filename": f["filename"],
                    "path": f["path"],
                    "type": f.get("type", "excel"),
                    "size_bytes": f.get("size_bytes", 0),
                }
                artifacts.append(artifact)
                add_task_artifact(task_id, artifact)

            emit("task_progress", {
                "task_id": task_id, "progress": 92,
                "step": f"Generados {len(artifacts)} archivos via code execution",
            })
        else:
            # Code execution didn't produce files — fall back to templates
            emit("task_progress", {
                "task_id": task_id, "progress": 90,
                "step": "Code execution sin archivos, usando plantillas...",
            })
            artifacts = _generate_artifacts_fallback(task_id, task_type, synthesis, emit)

    except Exception as e:
        emit("task_progress", {"task_id": task_id, "step": f"Code exec error: {e}, usando plantillas..."})
        artifacts = _generate_artifacts_fallback(task_id, task_type, synthesis, emit)

    return artifacts


def _generate_artifacts_fallback(task_id: str, task_type: str, synthesis: dict, emit: EventCallback) -> list[dict]:
    """Fallback: generate artifacts using hardcoded openpyxl templates."""
    artifacts = []

    try:
        if task_type == "cash_flow_forecast" and "cash_flow" in synthesis:
            cf = synthesis["cash_flow"]
            filepath = generate_cash_flow_excel(
                task_id=task_id,
                weeks=cf.get("weeks", []),
                inflows=cf.get("inflows", []),
                outflows=cf.get("outflows", []),
                opening_balance=cf.get("opening_balance", 0),
            )
            artifact = {"filename": os.path.basename(filepath), "path": filepath,
                        "type": "excel", "size_bytes": os.path.getsize(filepath)}
            artifacts.append(artifact)
            add_task_artifact(task_id, artifact)

        elif task_type == "modelo_303" and "modelo_303" in synthesis:
            filepath = generate_modelo_303_excel(task_id, synthesis["modelo_303"])
            artifact = {"filename": os.path.basename(filepath), "path": filepath,
                        "type": "excel", "size_bytes": os.path.getsize(filepath)}
            artifacts.append(artifact)
            add_task_artifact(task_id, artifact)

        excel_data = synthesis.get("excel_data", {})
        sheets = excel_data.get("sheets", [])
        if sheets:
            filename = f"{task_type}_report.xlsx"
            filepath = generate_table_excel(task_id, filename, sheets)
            artifact = {"filename": filename, "path": filepath,
                        "type": "excel", "size_bytes": os.path.getsize(filepath)}
            artifacts.append(artifact)
            add_task_artifact(task_id, artifact)

    except Exception as e:
        emit("task_progress", {"task_id": task_id, "step": f"Fallback Excel error: {e}"})

    return artifacts


# ---------------------------------------------------------------------------
# Internal: process uploaded files
# ---------------------------------------------------------------------------
def _process_uploads(
    task_id: str,
    file_paths: list[str],
    model_id: str,
    all_usage: list,
    emit: EventCallback,
) -> str:
    """Process uploaded documents and return context text."""
    from hackathon_backend.services.lambdas.agent.core.tools.pdf_reader import analyze_document

    parts = []
    for fp in file_paths:
        emit("task_progress", {"task_id": task_id, "step": f"Analizando {os.path.basename(fp)}..."})
        result = analyze_document(fp, model_id=model_id)
        all_usage.extend(result.get("usage", []))
        content = result.get("content", {})
        text = content.get("text", "")
        if text:
            parts.append(f"[Document: {os.path.basename(fp)}]\n{text[:5000]}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Task-specific guidance for the AI planner
# ---------------------------------------------------------------------------
def _get_task_guidance(task_type: str) -> str:
    """Return task-specific instructions for the AI planner."""
    guidance = {
        "cash_flow_forecast": """\
TASK-SPECIFIC GUIDANCE — CASH FLOW FORECAST (EXPLORATORIO):
You are a treasury analyst. Build a realistic 13-week forecast by understanding cash patterns.

STEP 1 — FETCH DATA (parallel queries):
  - Bank_Reconciliations by PK — ALL transactions (source of truth)
  - User_Expenses by PK — pending invoices = known future outflows

STEP 2 — EXPLORE PATTERNS (first run_code):
  - Group bank txns by week. Separate inflows (amount>0) vs outflows (amount<0).
  - Classify by ai_enrichment.payment_type/category:
    * Recurring: rent, salaries, subscriptions (predictable)
    * Variable: supplier payments (irregular)
    * One-off: large unusual transactions (exclude from averages)
  - Detect trends and seasonality. Find current bank balance.
  - List pending invoices (reconciled != True) as future outflows.
  DO NOT set result here. Continue to STEP 3.

STEP 3 — BUILD FORECAST (second run_code):
  - Recurring items: project at their usual timing/amount
  - Variable items: weighted weekly averages (recent weeks > older)
  - Pending invoices: schedule by due_date or estimated payment delay
  - Safety: exclude one-offs, reduce inflows 10%, increase outflows 5%
  - Week-by-week: opening balance → inflows → outflows → closing balance
  - Flag weeks with negative or low balance
  DO NOT set result here. Continue to STEP 4.

STEP 4 — GENERATE EXCEL (third run_code):
  - Sheet "Forecast 13 Semanas": weekly table with inflows, outflows, net, balance
  - Sheet "Detalle Categorias": flows by category
  - Sheet "Pagos Pendientes": pending invoices hitting the forecast period
  - Sheet "Historico": historical weekly data used as basis
  Include line chart of projected balance. Color-code risk weeks.
  You MUST generate an Excel file.

DO NOT use pandas. Use basic Python. Complete ALL steps. Set result only in STEP 4.""",

        "pack_reporting": """\
TASK-SPECIFIC GUIDANCE — PACK REPORTING (P&L):
Build a financial reporting pack using bank transactions as PRIMARY source + invoices for enrichment.

STEP 1 — FETCH DATA (parallel queries):
  - Bank_Reconciliations by PK — ALL bank transactions (source of truth for real cash flow)
  - User_Expenses via UserIdInvoiceDateIndex for the period (categorization + pending invoices)
  - User_Invoice_Incomes via UserIdInvoiceDateIndex (income categorization + pending)
  - Payroll_Slips for the period (payroll detail)

STEP 2 — BUILD P&L FROM BANK TRANSACTIONS (run_code):
  Bank transactions = SINGLE SOURCE OF TRUTH for actual cash:
  - amount > 0 = INCOME, amount < 0 = EXPENSE
  - Use ai_enrichment.category / ai_enrichment.payment_type for categorization
  - Filter by bookingDate for the requested period
  - Group by month → category → sum amounts

STEP 3 — ENRICH WITH INVOICES (run_code):
  - Match bank txns to invoices (reconciled=True invoices are already matched)
  - Use invoice category, concept, supplier for better labeling of matched txns
  - For unmatched bank txns: use ai_enrichment
  - AVOID DUPLICATION: NEVER sum bank txns AND their matched invoices separately
  - Identify PENDING invoices (reconciled != True) as future obligations

STEP 4 — EXCEL REPORT (run_code):
  - Sheet "P&L": Monthly columns, category rows, Income/Expenses/Net
  - Sheet "Detalle Gastos": Bank outflows enriched with invoice detail
  - Sheet "Detalle Ingresos": Bank inflows enriched with invoice detail
  - Sheet "Pendiente": Unpaid invoices (future obligations)
  - Sheet "KPIs": Margins, ratios, MoM comparison
  You MUST generate an Excel file — this is a report task.

IMPORTANT: P&L numbers come from BANK TRANSACTIONS (real cash). Invoices add context only.""",

        "modelo_303": """\
TASK-SPECIFIC GUIDANCE — MODELO 303 (IVA TRIMESTRAL):
Build a quarterly VAT return draft.
1. Query User_Expenses via UserIdInvoiceDateIndex for the quarter.
   Extract: ivas[] array, vatDeductibleAmount, vatNonDeductibleAmount, vatOperationType.
2. Query User_Invoice_Incomes via UserIdInvoiceDateIndex for the same quarter.
3. Calculate in run_code: Bases imponibles por tipo, cuotas soportadas deducibles, cuotas repercutidas.
4. IMPORTANT: Use run_code to create Excel (save to output_dir) with the Modelo 303 draft.
You MUST generate an Excel file — this is a report task.""",

        "aging_analysis": """\
TASK-SPECIFIC GUIDANCE — AGING ANALYSIS:
Classify outstanding debts by age buckets (0-30d, 31-60d, 61-90d, >90d).
1. Query User_Invoice_Incomes by PK, filter unpaid in run_code.
2. Query User_Expenses by PK, filter unpaid.
3. Bucket by age, identify top debtors/creditors.
4. IMPORTANT: Use run_code to create Excel (save to output_dir) with aging report.
You MUST generate an Excel file — this is a report task.""",

        "client_profitability": """\
TASK-SPECIFIC GUIDANCE — RENTABILIDAD POR CLIENTE:
1. Query User_Invoice_Incomes by PK, group by client_cif to get total revenue per client.
2. Query User_Expenses by PK, try to associate costs with clients (by project/concept).
3. Calculate margin per client: Revenue - Direct Costs. Rank by profitability.""",

        "modelo_347": """\
TASK-SPECIFIC GUIDANCE — MODELO 347:
List all third parties (suppliers + clients) with annual totals > 3,005€.
1. Query User_Expenses by PK, group by supplier_cif summing total for the year.
2. Query User_Invoice_Incomes by PK, group by client_cif summing total for the year.
3. Filter those with annual total > 3005€. Include: CIF, name, annual total.""",

        "three_way_matching": """\
TASK-SPECIFIC GUIDANCE — THREE-WAY MATCHING:
Cross-reference invoices, delivery notes, and purchase orders.
1. Query User_Expenses by PK to get invoices with supplier_cif and all_products.
2. Query Delivery_Notes by PK for delivery notes.
3. Match by supplier_cif + product descriptions. Flag discrepancies.""",

        "bank_reconciliation": """\
TASK-SPECIFIC GUIDANCE — BANK RECONCILIATION (CONCILIACION BANCARIA EXPLORATORIA):
You are an AI forensic accountant. The unreconciled items are the HARD cases that a standard
algorithm couldn't match. Your job is to EXPLORE the data creatively and find matches.

STEP 1 — FETCH DATA (parallel queries):
  - Bank_Reconciliations by PK — ALL transactions
  - User_Expenses by PK — ALL expense invoices
  - User_Invoice_Incomes by PK — ALL income invoices

STEP 2 — EXPLORE (first run_code): Understand the unreconciled landscape BEFORE matching.
  Filter:
  - Unreconciled bank txns: `[t for t in txns if t.get('status') != 'MATCHED']`
  - Unreconciled invoices: `[i for i in invoices if not i.get('reconciled')]`
    (reconciled=True → matched. Field MISSING → unreconciled. NEVER use reconciliationState.)
  Explore:
  - Print counts, amount ranges, date ranges for each set
  - Examine ai_enrichment field in bank txns (may contain vendor_cif, payment_type, category)
  - List top merchants in unreconciled txns and top suppliers in unreconciled invoices
  - Look for patterns: descriptions containing supplier names, CIFs, invoice numbers

STEP 3 — MATCH CREATIVELY (second run_code): Try MULTIPLE strategies, combine signals:
  a) Exact amount: abs(txn.amount) == invoice.total (±0.01€)
  b) CIF match: ai_enrichment.vendor_cif == supplier_cif
  c) Fuzzy name: supplier name partially in txn.description or txn.merchant (case-insensitive)
  d) Date proximity: bookingDate near invoice_date, due_date, or charge_date (±30 days)
  e) N-to-1: sum of N invoices from same supplier ≈ one bank txn amount
  f) 1-to-N: one large invoice split across multiple smaller bank txns
  g) Fees: txn.amount ≈ invoice.total ± bank fee (1-10€)
  h) Partial payment: txn.amount matches amount_paid or a fraction of total

  Score each match by combining signals. Explain WHY you think each match is correct.
  Bank amount < 0 = expense payment. Bank amount > 0 = income received.

STEP 4 — EXCEL REPORT (third run_code):
  - Sheet "Matches Propuestos": pairs with confidence %, reasoning, amounts, dates
  - Sheet "Txns Sin Match": remaining unmatched bank transactions
  - Sheet "Facturas Sin Match": remaining unmatched invoices
  - Sheet "Resumen": stats and insights from your exploration
  Color-code by confidence. You MUST generate an Excel file.

MANDATORY KEYS FOR FRONTEND INTEGRATION:
  Every row in "Matches Propuestos" MUST include these DynamoDB keys (needed to execute real reconciliations):
  - invoice_categoryDate: the `categoryDate` field from User_Expenses (SK, format CATEGORY#YYYY-MM-DD#UUID)
  - txn_SK: the `SK` field from Bank_Reconciliations (sort key of the bank transaction)
  - invoice_userId: the `userId` (PK) of the invoice
  - txn_userId: the `userId` (PK) of the bank transaction
  These columns can be narrow/hidden but MUST be present. Without them the frontend cannot reconcile.

IMPORTANT: Do NOT modify records. Only PROPOSE matches. The user reviews and approves.""",
    }
    return guidance.get(task_type, "")


# ---------------------------------------------------------------------------
# Internal: custom plan via LLM
# ---------------------------------------------------------------------------
def _create_custom_plan(
    task_id: str,
    task_type: str,
    description: str,
    doc_context: str,
    model_id: str,
    all_usage: list,
    emit: EventCallback,
) -> list[dict]:
    """Use LLM to create a custom execution plan."""
    emit("task_progress", {"task_id": task_id, "progress": 5, "step": "Planificando tarea..."})

    task_guidance = _get_task_guidance(task_type)

    prompt = f"""You are planning a financial analysis task. Create an execution plan.

TASK TYPE: {task_type}
DESCRIPTION: {description}
{"DOCUMENT CONTEXT: " + doc_context[:2000] if doc_context else ""}
TODAY: 2026-03-21

AVAILABLE TABLES (DynamoDB):
1. User_Expenses — PK=userId, SK=categoryDate (CATEGORY#YYYY-MM-DD#UUID)
   GSIs: UserIdInvoiceDateIndex(sk=invoice_date), UserIdSupplierCifIndex(sk=supplier_cif), UserIdInvoiceDateIndex(sk=pnl_date)
   Fields: total, importe, ivas[], supplier, supplier_cif, invoice_date, due_date, category, concept, reconciled (None/False=unpaid), accountingEntries[], all_products[]
   NOTE: For unpaid invoices, query by PK and filter reconciled=None/False in run_code (the UserByReconStateDate GSI may not have data)

2. User_Invoice_Incomes — PK=userId, SK=categoryDate. Same pattern as expenses but with client_name/client_cif.
   GSIs: UserIdInvoiceDateIndex, UserIdClientCifIndex(sk=client_cif)

3. Bank_Reconciliations — PK=locationId, SK=MTXN#bookingDate#type#id
   Fields: amount (negative=outflow, positive=inflow), bookingDate, description, merchant, status (PENDING/MATCHED),
   ai_enrichment (Map with payment_type, vendor_name, vendor_cif, account_type)
   payment_type values: vendor_payment, payroll, social_security, bank_fee, tax_payment
   NOTE: Query by PK directly (no GSI needed). GSI LocationByStatusDate: PK=locationId, SK=status_date (status#bookingDate)

4. Payroll_Slips — PK=locationId, SK=categoryDate(date#nif). Fields: payroll_info.gross_amount, net_amount, company_ss_contribution

5. Providers — PK=locationId, SK=cif. Fields: nombre, cif, trade_name, facturas
6. Customers — PK=locationId, SK=cif
7. Employees — PK=locationId, SK=employeeNif
8. Daily_Stats — PK=locationId, SK=dayKey
9. Monthly_Stats — PK=locationId, SK=monthKey

{task_guidance}

IMPORTANT QUERY TIPS:
- Each sub-agent has dynamo_query (DynamoDB) and run_code (Python execution with data access, file generation) tools.
- Always use PK queries (never scans). For date ranges, use GSIs with SK conditions.
- If a GSI returns 0 results, try querying by PK directly and filtering with run_code.
- The sub-agent should always finish with run_code to return structured data and/or generate files.
- Bank_Reconciliations contains the REAL money movements. amount<0 = outflow, amount>0 = inflow.

Create 2-5 steps. Each step runs as a sub-agent that can query DynamoDB and run Python analysis.
Steps with the same parallel_group number run concurrently.
Put independent data-fetching steps in the same parallel_group so they run in parallel.

Return JSON array:
[
  {{
    "name": "step_name",
    "description": "Human-readable description (Spanish)",
    "parallel_group": 1,
    "objective": "Detailed objective: what table to query, what PK/SK/GSI to use, what fields to extract, what to compute in run_code. Be specific."
  }}
]"""

    response = traced_completion(
        model_id=model_id,
        messages=[{"role": "user", "content": prompt}],
        step="planning",
        task_id=task_id,
        temperature=0.2,
    )

    u = getattr(response, "usage", None)
    prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
    completion_tokens = getattr(u, "completion_tokens", 0) or 0
    all_usage.append({
        "model": model_id, "step": "planning",
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "total_tokens": (getattr(u, "total_tokens", 0) or 0),
    })

    from hackathon_backend.services.lambdas.agent.core.chat_store import _estimate_cost
    add_task_cost(task_id, getattr(u, "total_tokens", 0) or 0,
                  _estimate_cost(model_id, prompt_tokens, completion_tokens))

    text = response.choices[0].message.content or ""
    plan = _try_parse_json(text)

    if isinstance(plan, list):
        return plan
    elif isinstance(plan, dict) and "steps" in plan:
        return plan["steps"]
    else:
        # Fallback: single-step plan
        return [{
            "name": "analyze",
            "description": description or task_type,
            "parallel_group": 1,
            "objective": f"Analyze: {description}. Query the relevant tables and provide structured results.",
        }]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _try_parse_json(text: str) -> Any:
    """Try to parse JSON from text, handling markdown code blocks."""
    text = text.strip()
    if "```json" in text:
        try:
            start = text.index("```json") + 7
            end = text.index("```", start)
            return json.loads(text[start:end].strip())
        except (ValueError, json.JSONDecodeError):
            pass
    if "```" in text:
        try:
            start = text.index("```") + 3
            # Skip language identifier if present
            if text[start:start+1] == "\n":
                start += 1
            elif text[start:start+10].strip().isalpha():
                start = text.index("\n", start) + 1
            end = text.index("```", start)
            return json.loads(text[start:end].strip())
        except (ValueError, json.JSONDecodeError):
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"summary": text}
