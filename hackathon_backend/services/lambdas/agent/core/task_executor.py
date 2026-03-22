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
    QUERY_AGENT_TOOLS, QUERY_AGENT_SYSTEM,
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
    Run a sub-agent that can query DynamoDB and run analysis code.
    Returns: {"success": bool, "data": dict, "usage": list[dict], "sources": list[dict]}
    """
    emit = on_event or _noop
    usage_records: list[dict] = []
    sources: list[dict] = []

    system_prompt = QUERY_AGENT_SYSTEM + f"""

ADDITIONAL CONTEXT:
You are a sub-agent working as part of a larger task. Your specific objective:
{objective}

Return your results by calling run_analysis with a structured result dict.
Focus ONLY on your objective — do not try to answer the full user question.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": objective},
    ]

    query_results: dict[str, dict] = {}
    query_counter = 0
    # location_id for traced_completion — extracted from the _execute_query calls

    for iteration in range(max_iterations):
        # Check cancellation
        if is_cancelled(task_id):
            raise CancelledError(f"Task {task_id} cancelled")

        # Check budget before each LLM call
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
            tools=QUERY_AGENT_TOOLS,
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

        # Track cost
        from hackathon_backend.services.lambdas.agent.core.chat_store import _estimate_cost
        cost = _estimate_cost(model_id, prompt_tokens, completion_tokens)
        add_task_cost(task_id, total_tokens, cost)

        choice = response.choices[0]

        # Agent finished
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
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps({"error": "Invalid JSON"}),
                })
                continue

            if fn_name == "dynamo_query":
                query_counter += 1
                query_key = f"query_{query_counter}"

                emit("task_progress", {
                    "task_id": task_id,
                    "message": f"Consultando {args['table_name']}...",
                })

                result = _execute_query(
                    table_name=args["table_name"],
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

                # Collect sources
                if result.get("success") and args["table_name"] in ("User_Expenses", "User_Invoice_Incomes"):
                    for item in result["items"]:
                        src = _extract_source(item)
                        if src:
                            sources.append(src)

                # Prepare LLM response (truncated)
                response_for_llm = {
                    "query_key": query_key,
                    "success": result["success"],
                    "table": result["table"],
                    "count": result["count"],
                }
                if result["success"]:
                    from hackathon_backend.services.lambdas.agent.core.query_agent import KEEP_FIELDS
                    items_for_llm = result["items"][:50]
                    slim_items = [{k: v for k, v in it.items() if k in KEEP_FIELDS} for it in items_for_llm]
                    response_for_llm["items"] = _sanitize(slim_items)
                    if result["count"] > 50:
                        response_for_llm["note"] = f"Showing 50 of {result['count']}. Use run_analysis for full dataset."
                else:
                    response_for_llm["error"] = result.get("error", "Unknown")

                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps(response_for_llm, ensure_ascii=False, default=str),
                })

            elif fn_name == "run_analysis":
                code = args["code"]
                result = _execute_code(code, query_results)

                if result["success"] and isinstance(result["result"], dict):
                    analysis = result["result"]
                    if "answer" in analysis or "data" in analysis:
                        update_task_step(step_id, "COMPLETED",
                                         result_summary=str(analysis.get("answer", ""))[:200])
                        return {
                            "success": True,
                            "data": analysis,
                            "usage": usage_records,
                            "sources": sources,
                        }

                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

            elif fn_name == "generate_file":
                emit("task_progress", {
                    "task_id": task_id,
                    "message": "Sub-agent generando archivo...",
                })
                file_prompt = args.get("prompt", "")
                data_json = args.get("data_json", "")

                code_result = run_code_execution(
                    prompt=file_prompt,
                    model_id=model_id,
                    data_context=data_json,
                    task_id=task_id,
                    system_prompt=CODE_EXEC_SYSTEM,
                )

                if code_result.get("usage"):
                    u = code_result["usage"]
                    if isinstance(u, dict):
                        usage_records.append(u)
                    elif isinstance(u, list):
                        usage_records.extend(u)

                file_response = {
                    "success": code_result.get("success", False),
                    "files": [f["filename"] for f in code_result.get("files", [])],
                    "text": code_result.get("text", "")[:500],
                }

                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps(file_response, ensure_ascii=False, default=str),
                })

            else:
                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps({"error": f"Unknown tool: {fn_name}"}),
                })

    # Max iterations
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
TASK-SPECIFIC GUIDANCE — CASH FLOW FORECAST:
The cash flow forecast MUST be built primarily from Bank_Reconciliations (real bank movements).
This table contains ALL actual money movements: amount<0 = outflow, amount>0 = inflow.

Steps:
1. Fetch ALL bank transactions from Bank_Reconciliations (PK=locationId, query by PK directly).
   Extract: bookingDate, amount, description, merchant, ai_enrichment.payment_type
2. In run_analysis, classify transactions into categories using ai_enrichment.payment_type
   (vendor_payment, payroll, social_security, bank_fee, tax_payment) and amount sign.
   Group by week and category to find historical patterns (weekly averages for inflows/outflows).
3. Project 13 weeks forward based on historical weekly averages per category.
   Calculate: opening_balance (last known bank position), weekly inflows, weekly outflows, closing_balance.
   Identify liquidity alerts (weeks where projected balance goes negative).
4. IMPORTANT: Call generate_file to create a professional Excel with:
   - Sheet 1: 13-week forecast table with weekly columns (inflows, outflows, net, cumulative balance)
   - Sheet 2: Historical analysis (weekly actuals by category)
   - Sheet 3: Executive summary with key metrics and alerts
   - Include a line chart showing projected balance over 13 weeks
   Pass the computed forecast data as data_json.

DO NOT use User_Expenses or User_Invoice_Incomes as primary data for cash flow.
Bank_Reconciliations IS the source of truth for actual cash movements.
You MUST generate an Excel file — this is a report task, not just a data query.""",

        "pack_reporting": """\
TASK-SPECIFIC GUIDANCE — PACK REPORTING:
Build a monthly financial reporting pack (P&L + KPIs).
1. Query User_Expenses via UserIdPnlDateIndex for the current month's expenses.
2. Query User_Invoice_Incomes via UserIdPnlDateIndex for income in the same period.
3. Query Payroll_Slips for salary costs in the period.
4. Build P&L in run_analysis: Revenue - COGS - Operating Expenses - Payroll = Operating Profit.
5. IMPORTANT: Call generate_file to create Excel with P&L sheet, KPIs sheet, and charts.
You MUST generate an Excel file — this is a report task.""",

        "modelo_303": """\
TASK-SPECIFIC GUIDANCE — MODELO 303 (IVA TRIMESTRAL):
Build a quarterly VAT return draft.
1. Query User_Expenses via UserIdPnlDateIndex for the quarter.
   Extract: ivas[] array, vatDeductibleAmount, vatNonDeductibleAmount, vatOperationType.
2. Query User_Invoice_Incomes via UserIdPnlDateIndex for the same quarter.
3. Calculate in run_analysis: Bases imponibles por tipo, cuotas soportadas deducibles, cuotas repercutidas.
4. IMPORTANT: Call generate_file to create Excel with the Modelo 303 draft.
You MUST generate an Excel file — this is a report task.""",

        "aging_analysis": """\
TASK-SPECIFIC GUIDANCE — AGING ANALYSIS:
Classify outstanding debts by age buckets (0-30d, 31-60d, 61-90d, >90d).
1. Query User_Invoice_Incomes by PK, filter unpaid in run_analysis.
2. Query User_Expenses by PK, filter unpaid.
3. Bucket by age, identify top debtors/creditors.
4. IMPORTANT: Call generate_file to create Excel with aging report.
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
TASK-SPECIFIC GUIDANCE — BANK RECONCILIATION (CONCILIACION BANCARIA):
Automatically match unreconciled bank transactions with unreconciled invoices/payrolls.

Steps:
1. Query Bank_Reconciliations by PK (locationId). Filter unreconciled:
   Use filter_expression: [{"field": "reconciled", "op": "ne", "value": true}]
   OR use LocationByStatusDate GSI with SK begins_with "PENDING".
2. Query User_Expenses by PK. In run_analysis filter items where reconciled is None/False.
3. Query User_Invoice_Incomes by PK. Same filter for unreconciled.
4. In run_analysis, implement matching algorithm:

   MATCHING RULES (by priority):
   a) EXACT MATCH (confidence=HIGH): abs(txn.amount) == invoice.total AND
      (ai_enrichment.vendor_cif == supplier_cif OR ai_enrichment.vendor_cif == client_cif)
   b) AMOUNT MATCH (confidence=MEDIUM): abs(txn.amount) == invoice.total (within 0.01 EUR tolerance)
      AND date proximity (bookingDate within 30 days of invoice_date or due_date)
   c) PARTIAL MATCH (confidence=LOW): amount close (within 5%) AND CIF matches
   d) AGGREGATE MATCH: Sum of multiple invoices = single bank transaction (N-1 matching)

   For EXPENSES: txn.amount < 0 matches invoice.total (outflow pays an expense)
   For INCOMES: txn.amount > 0 matches invoice.total (inflow receives a payment)

5. IMPORTANT: Call generate_file to create Excel with:
   - Sheet 1: "Propuesta Conciliacion" — matched pairs with confidence score, txn details, invoice details
   - Sheet 2: "Transacciones Sin Conciliar" — bank txns with no match found
   - Sheet 3: "Facturas Sin Conciliar" — invoices (expenses + incomes) with no match found
   - Sheet 4: "Resumen" — summary stats (total matched, unmatched, by confidence level)
   Color-code: green=high confidence, yellow=medium, red=low.
   You MUST generate an Excel file — this is a reconciliation report task.

IMPORTANT: Do NOT modify any records. Only PROPOSE matches. The user reviews and approves.""",
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
   GSIs: UserIdInvoiceDateIndex(sk=invoice_date), UserIdSupplierCifIndex(sk=supplier_cif), UserIdPnlDateIndex(sk=pnl_date)
   Fields: total, importe, ivas[], supplier, supplier_cif, invoice_date, due_date, category, concept, reconciled (None/False=unpaid), accountingEntries[], all_products[]
   NOTE: For unpaid invoices, query by PK and filter reconciled=None/False in run_analysis (the UserByReconStateDate GSI may not have data)

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
- Each sub-agent has dynamo_query (DynamoDB), run_analysis (Python code), and generate_file (AI code execution) tools.
- Always use PK queries (never scans). For date ranges, use GSIs with SK conditions.
- If a GSI returns 0 results, try querying by PK directly and filtering with run_analysis.
- The sub-agent should always finish with run_analysis to return structured data.
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
    "objective": "Detailed objective: what table to query, what PK/SK/GSI to use, what fields to extract, what to compute in run_analysis. Be specific."
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
