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

from hackathon_backend.services.lambdas.agent.core.config import completion
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

    for iteration in range(max_iterations):
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

        response = completion(
            model_id=model_id,
            messages=messages,
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


# ---------------------------------------------------------------------------
# Task execution plans — hardcoded templates for each TOP task type
# ---------------------------------------------------------------------------
TASK_PLANS: dict[str, list[dict]] = {
    "cash_flow_forecast": [
        {"name": "query_unpaid_expenses", "description": "Consultar facturas de gasto pendientes de pago (próximos 91 días)", "parallel_group": 1,
         "objective": "Query User_Expenses table using UserByReconStateDate index with sk begins_with 'U#' to find all unpaid expenses. For each, extract: supplier, total, due_date, amount_due, amount_paid. Return a structured result with the list of unpaid expenses and their due dates."},
        {"name": "query_unpaid_incomes", "description": "Consultar facturas de ingreso pendientes de cobro", "parallel_group": 1,
         "objective": "Query User_Invoice_Incomes table using UserByReconStateDate index with sk begins_with 'U#' to find all unpaid income invoices. For each, extract: client_name, total, due_date, amount_due, amount_paid. Return a structured result with the list of pending receivables."},
        {"name": "query_payroll", "description": "Consultar nóminas recientes para proyección", "parallel_group": 1,
         "objective": "Query Payroll_Slips to get the last 3 months of payroll data. Extract: payroll_info.gross_amount, payroll_info.net_amount, payroll_info.company_ss_contribution. Calculate the average monthly payroll cost for projection."},
        {"name": "query_bank_pending", "description": "Consultar transacciones bancarias pendientes", "parallel_group": 1,
         "objective": "Query Bank_Reconciliations using LocationByStatusDate index with sk begins_with 'PENDING#' to find unreconciled transactions. Sum the pending amounts (positive = income, negative = expense)."},
    ],
    "pack_reporting": [
        {"name": "query_expenses_month", "description": "Consultar gastos del mes por categoría", "parallel_group": 1,
         "objective": "Query User_Expenses using UserIdPnlDateIndex for the current month. Group by category and concept. Calculate total importe, total vatTotalAmount. Return breakdown by category with subtotals."},
        {"name": "query_incomes_month", "description": "Consultar ingresos del mes", "parallel_group": 1,
         "objective": "Query User_Invoice_Incomes using UserIdPnlDateIndex for the current month. Group by category. Calculate total importe. Return breakdown with client distribution."},
        {"name": "query_payroll_month", "description": "Consultar costes salariales del mes", "parallel_group": 1,
         "objective": "Query Payroll_Slips for the current month. Sum gross_amount, company_ss_contribution, irpf_amount. Return total personnel costs."},
    ],
    "modelo_303": [
        {"name": "query_iva_soportado", "description": "IVA Soportado (gastos) del trimestre", "parallel_group": 1,
         "objective": "Query User_Expenses using UserIdPnlDateIndex for the current quarter. For each invoice, extract: ivas array (rate, base_imponible, amount), vatTotalAmount, vatDeductibleAmount, vatNonDeductibleAmount, vatOperationType. Group by vatOperationType and IVA rate. Return the totals."},
        {"name": "query_iva_repercutido", "description": "IVA Repercutido (ingresos) del trimestre", "parallel_group": 1,
         "objective": "Query User_Invoice_Incomes using UserIdPnlDateIndex for the current quarter. Extract ivas array and group by rate. Calculate total bases imponibles and cuotas. Return the breakdown."},
    ],
    "aging_analysis": [
        {"name": "query_unpaid_receivables", "description": "Facturas de ingreso pendientes de cobro", "parallel_group": 1,
         "objective": "Query User_Invoice_Incomes using UserByReconStateDate with sk begins_with 'U#'. Get all unpaid invoices. For each: client_name, client_cif, total, due_date, invoice_date, amount_due. Classify into aging buckets: 0-30 days, 31-60, 61-90, >90 days overdue from today."},
        {"name": "query_unpaid_payables", "description": "Facturas de gasto pendientes de pago", "parallel_group": 1,
         "objective": "Query User_Expenses using UserByReconStateDate with sk begins_with 'U#'. Get all unpaid invoices. For each: supplier, supplier_cif, total, due_date, amount_due. Classify into aging buckets."},
    ],
    "client_profitability": [
        {"name": "query_incomes_by_client", "description": "Ingresos por cliente", "parallel_group": 1,
         "objective": "Query User_Invoice_Incomes for the last 12 months using UserIdPnlDateIndex. Group by client_cif and client_name. Sum importe per client. Return top clients ranked by revenue."},
        {"name": "query_expenses_by_supplier", "description": "Gastos por proveedor/categoría", "parallel_group": 1,
         "objective": "Query User_Expenses for the last 12 months using UserIdPnlDateIndex. Group by category and supplier. Sum importe per category. Return breakdown for margin calculation."},
        {"name": "query_payroll_total", "description": "Costes salariales totales", "parallel_group": 1,
         "objective": "Query Payroll_Slips for the last 12 months. Calculate total gross + SS contribution. This is needed to allocate personnel costs across clients."},
    ],
}


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

        # Step 2: Get execution plan
        plan = TASK_PLANS.get(task_type)
        if not plan:
            # Use LLM to create a custom plan
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

        # Step 6: Generate Excel artifact
        emit("task_progress", {"task_id": task_id, "progress": 85, "step": "Generando informe Excel..."})
        artifacts = _generate_artifacts(task_id, task_type, synthesis, emit)

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

    response = completion(
        model_id=model_id,
        messages=[{"role": "user", "content": synthesis_prompt}],
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
# Internal: generate artifacts from synthesis
# ---------------------------------------------------------------------------
def _generate_artifacts(task_id: str, task_type: str, synthesis: dict, emit: EventCallback) -> list[dict]:
    """Generate Excel/PDF artifacts from synthesized results."""
    artifacts = []

    try:
        # Task-specific Excel generation
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

        # Generic Excel from excel_data
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
        emit("task_progress", {"task_id": task_id, "step": f"Error generando Excel: {e}"})

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

    prompt = f"""You are planning a financial analysis task. Create an execution plan.

TASK TYPE: {task_type}
DESCRIPTION: {description}
{"DOCUMENT CONTEXT: " + doc_context[:2000] if doc_context else ""}

Available tables: User_Expenses, User_Invoice_Incomes, Bank_Reconciliations, Payroll_Slips, Delivery_Notes, Employees, Providers, Customers, Daily_Stats, Monthly_Stats.

Create 2-5 steps. Each step runs as a sub-agent that can query DynamoDB and run Python analysis.
Steps with the same parallel_group number run concurrently.

Return JSON:
[
  {{
    "name": "step_name",
    "description": "Human-readable description (Spanish)",
    "parallel_group": 1,
    "objective": "Detailed objective for the sub-agent including which table/index to query and what to extract"
  }}
]"""

    response = completion(
        model_id=model_id,
        messages=[{"role": "user", "content": prompt}],
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
