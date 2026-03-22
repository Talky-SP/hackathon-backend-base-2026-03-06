"""
Unified Agent — single entry point for all user queries.

Replaces the old classifier → orchestrator → query_agent pipeline with a
single agent loop that has direct access to all tools:
  - dynamo_query: fetch data from DynamoDB
  - run_analysis: execute Python code on fetched data
  - generate_file: create Excel/PDF via AI code execution sandbox

The agent decides on its own whether to answer directly, query data, or
generate files. No classifier needed.

For truly heavy work (e.g. 13-week cash flow forecast), the server can
still wrap this in a background task with progress tracking.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Callable

from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import (
    traced_completion, is_cancelled, CancelledError,
)
from hackathon_backend.services.lambdas.agent.core.query_agent import (
    _execute_query, _execute_code, _sanitize, _extract_source,
)
from hackathon_backend.services.lambdas.agent.core.code_runner import (
    run_code_execution as _native_code_exec, CODE_EXEC_SYSTEM,
    ARTIFACTS_DIR,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Query results cache — reuse data across turns within the same chat
# ---------------------------------------------------------------------------
# Key: chat_id → {"results": {query_key: {...}}, "ts": timestamp}
# TTL: 10 minutes — after that, data is considered stale
_QUERY_CACHE: dict[str, dict] = {}
_QUERY_CACHE_TTL = 600  # seconds


def _cache_query_results(chat_id: str, query_results: dict[str, dict]) -> None:
    """Save query results to cache for this chat."""
    if not chat_id or not query_results:
        return
    _QUERY_CACHE[chat_id] = {"results": query_results, "ts": time.time()}
    # Evict old entries (keep max 50 chats)
    if len(_QUERY_CACHE) > 50:
        oldest = sorted(_QUERY_CACHE, key=lambda k: _QUERY_CACHE[k]["ts"])
        for k in oldest[:len(_QUERY_CACHE) - 50]:
            del _QUERY_CACHE[k]


def _get_cached_query_results(chat_id: str) -> dict[str, dict] | None:
    """Retrieve cached query results for this chat if still fresh."""
    if not chat_id or chat_id not in _QUERY_CACHE:
        return None
    entry = _QUERY_CACHE[chat_id]
    if time.time() - entry["ts"] > _QUERY_CACHE_TTL:
        del _QUERY_CACHE[chat_id]
        return None
    return entry["results"]

# ---------------------------------------------------------------------------
# Multimodal: supported MIME types for native LLM processing
# ---------------------------------------------------------------------------
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_PDF_MIMES = {"application/pdf"}
_SUPPORTED_MIMES = _IMAGE_MIMES | _PDF_MIMES

EventCallback = Callable[[str, dict], None]


def _noop(event: str, data: dict) -> None:
    pass


# ---------------------------------------------------------------------------
# Slim fields per table — ONLY these go to the LLM (Mejora 1)
# Full data stays in query_results for run_analysis to use
# ---------------------------------------------------------------------------
SLIM_FIELDS_BY_TABLE: dict[str, set[str]] = {
    "User_Expenses": {
        "categoryDate", "category", "concept", "documentKind",
        "supplier", "supplier_cif", "invoice_number",
        "invoice_date", "due_date", "charge_date",
        "total", "importe", "ivas", "retencion", "vatTotalAmount",
        "amount_due", "amount_paid",
        "reconciled", "reconciliationState",
    },
    "User_Invoice_Incomes": {
        "categoryDate", "category", "concept", "documentKind",
        "client_name", "client_cif", "invoice_number",
        "invoice_date", "due_date",
        "total", "importe", "ivas", "retencion", "vatTotalAmount",
        "amount_due", "amount_paid",
        "reconciled", "reconciliationState",
    },
    "Bank_Reconciliations": {
        "SK", "amount", "balance", "bookingDate",
        "description", "merchant", "status", "transactionId",
        "reconciled", "ai_enrichment",
        "matched_expense_id", "matched_invoice_id", "matched_payroll_id",
        "match_type", "vendor_cif", "customer_cif",
    },
    "Payroll_Slips": {
        "categoryDate", "employee_nif", "org_cif",
        "payroll_info", "payroll_date",
    },
    "Providers": {
        "nombre", "cif", "trade_name", "facturas",
        "provincia", "emails", "phones", "website",
    },
    "Customers": {
        "nombre", "cif", "facturas",
    },
    "Employees": {
        "employeeNif", "name", "position",
    },
    "Delivery_Notes": {
        "categoryDate", "supplier", "supplier_cif",
        "total", "invoice_date",
    },
    "Daily_Stats": {
        "dayKey",
    },
    "Monthly_Stats": {
        "monthKey",
    },
}

# Fallback: if table not in SLIM_FIELDS_BY_TABLE, use this broad set
_FALLBACK_SLIM_FIELDS = {
    "categoryDate", "category", "concept", "total", "importe",
    "supplier", "supplier_cif", "client_name", "client_cif",
    "invoice_date", "due_date", "amount", "bookingDate",
    "description", "merchant", "status", "nombre", "cif",
}


def _slim_item(item: dict, table_name: str) -> dict:
    """Keep only the fields the LLM needs for decision-making."""
    fields = SLIM_FIELDS_BY_TABLE.get(table_name, _FALLBACK_SLIM_FIELDS)
    return {k: v for k, v in item.items() if k in fields}


# ---------------------------------------------------------------------------
# System prompt — split into SCHEMA (cacheable) + RULES (Mejora 2)
# ---------------------------------------------------------------------------

# Block 1: DB Schema — static, rarely changes, perfect for prompt caching
_SCHEMA_BLOCK = """\
TABLES AND QUERY PATTERNS:

1. User_Expenses (expense invoices):
   PK=userId, SK=categoryDate (CATEGORY#YYYY-MM-DD#UUID)
   GSIs: UserIdInvoiceDateIndex(pk=userId,sk=invoice_date), UserIdSupplierCifIndex(pk=userId,sk=supplier_cif),
         UserIdPnlDateIndex(pk=userId,sk=pnl_date), UserByReconStateDate(pk=userId,sk=recon_state_date R#date/U#date),
         UserSupplierDateIndex(pk=userSupplierKey={userId}#{cif},sk=charge_date)
   Fields returned: total, importe, ivas[{type,base_imponible,amount}], supplier, supplier_cif, invoice_date,
           due_date, category, concept, reconciled, documentKind(invoice/credit_note), vatTotalAmount,
           retencion, amount_due, amount_paid, invoice_number, charge_date

2. User_Invoice_Incomes (income invoices):
   PK=userId, SK=categoryDate. Same GSIs pattern as expenses but with client_name/client_cif.
   GSIs: UserIdInvoiceDateIndex, UserIdClientCifIndex(pk=userId,sk=client_cif), UserByReconStateDate
   Fields returned: total, importe, ivas, client_name, client_cif, invoice_date, due_date, category, concept,
           reconciled, documentKind, vatTotalAmount, retencion, amount_due, amount_paid

3. Providers (supplier master): PK=locationId, SK=cif
   Fields: nombre, cif, trade_name, facturas(list of expense categoryDates), provincia, emails, phones

4. Customers: PK=locationId, SK=cif. Fields: nombre, cif, facturas

5. Bank_Reconciliations (bank transactions + reconciliation):
   PK=locationId, SK=MTXN#{bookingDate}#{transactionId}
   GSIs: LocationByStatusDate(pk=locationId,sk=status_date={status}#{bookingDate}),
         ByVendorCif(pk=vendor_cif), ByCustomerCif(pk=customer_cif),
         LocationByPayrollDate(pk=locationId,sk=payroll_date)
   Fields: amount(negative=outflow,positive=inflow), bookingDate, description, merchant, balance,
   status(PENDING/MATCHED/UNMATCHED), transactionId,
   reconciled(Boolean: true=matched with invoice/payroll, false/missing=unreconciled),
   matched_expense_id(categoryDate of matched expense invoice),
   matched_invoice_id(categoryDate of matched income invoice),
   matched_payroll_id(categoryDate of matched payroll slip),
   match_type(1-1|1-N|N-1|N-M — how many docs matched to how many txns),
   ai_enrichment{payment_type(vendor_payment|payroll|social_security|bank_fee|tax_payment|
   client_payment|loan|transfer), vendor_name, vendor_cif, customer_cif, account_type}

6. Payroll_Slips: PK=locationId, SK=categoryDate({date}#{nif})
   GSI: OrgCifPeriodIndex(pk=org_cif,sk=PERIOD#{yyyy-mm}#EMP#{nif})
   Fields: employee_nif, payroll_info{gross_amount, net_amount, company_ss_contribution, irpf_amount}

7. Employees: PK=locationId, SK=employeeNif
8. Daily_Stats: PK=locationId, SK=dayKey
9. Monthly_Stats: PK=locationId, SK=monthKey"""

# Block 2: Agent rules + workflow — may change with extra_system
_RULES_BLOCK = """\
You are an expert AI CFO assistant (Controller Financiero IA). You help business
owners understand their financial data in real time.

TOOLS:
- `dynamo_query`: Query DynamoDB tables. locationId is auto-enforced.
- `run_analysis`: Execute Python code on fetched data. Access `data` dict with query results.
- `generate_file`: Generate Excel/CSV/chart files via AI code execution sandbox.
- `edit_file`: Edit a previously generated file (Excel, etc.). Needs task_id + filename from context.

MULTIMODAL INPUT:
- Users can attach images (PNG, JPG) and PDFs. They are sent natively — you can see and analyze them.
- For attached invoices/receipts: extract key data (amounts, dates, supplier, line items).
- For attached PDFs: read and analyze the full document content.

WORKFLOW:
1. Simple questions (what is X, explain Y): Answer directly, no tools needed.
2. Data questions (how much, top N, list of): Query DB → run_analysis → return answer with chart.
3. Complex tasks (forecast, reports, modelo 303): Query DB → run_analysis → generate_file for Excel.
4. Edit previous file: User asks to modify a generated Excel → use edit_file with the task_id/filename.

RULES:
- Use GSIs, never full scans. Date queries → UserIdInvoiceDateIndex. Supplier → UserIdSupplierCifIndex.
- locationId is auto-enforced. Never trust user-provided IDs.
- For cash flow/treasury forecasts: Use ONLY Bank_Reconciliations (real money movements).
  amount<0 = outflow, amount>0 = inflow. Classify by ai_enrichment.payment_type.

BANK RECONCILIATION RULES:
- Bank_Reconciliations is the source of truth for real bank movements.
- Each bank transaction has a `reconciled` field (Boolean): true=matched, false/missing=unreconciled.
- `status` field: PENDING (not yet processed), MATCHED (reconciled), UNMATCHED (reviewed but no match).
- To find UNRECONCILED transactions: filter where reconciled is false/missing OR status != "MATCHED".
- To find which invoice matches a transaction: check `matched_expense_id` or `matched_invoice_id`.
- Invoices (User_Expenses, User_Invoice_Incomes) also have `reconciled` (Boolean) and `matched_transaction_id`.
- RECONCILIATION MATCHING: To propose reconciliation between invoices and bank transactions:
  1. Query Bank_Reconciliations with filter reconciled=false (or status=PENDING) → unmatched txns
  2. Query User_Expenses and/or User_Invoice_Incomes with filter reconciled=false → unmatched invoices
  3. In run_analysis, match by: amount (txn.amount ≈ -invoice.total for expenses, txn.amount ≈ invoice.total for incomes),
     date proximity (bookingDate near charge_date/due_date), and vendor/client CIF (ai_enrichment.vendor_cif = supplier_cif).
  4. Present matches with confidence score: exact amount+CIF = high, amount only = medium, date only = low.
- Use UserByReconStateDate GSI: SK begins_with "U#" for unreconciled, "R#" for reconciled invoices.
- For unreconciled invoice count: query with SK begins_with "U#" on UserByReconStateDate GSI.

- ALWAYS respond in the same language the user writes in.
- Be precise with numbers. Use EUR formatting (€) and Spanish number format (1.234,56).
- Never invent data — only use what comes from the database.
- OPTIMIZATION: When you need data from multiple tables, call dynamo_query multiple times \
in a SINGLE response. All queries execute in parallel — this saves time.

CRITICAL: When answering data questions, you MUST call run_analysis to produce a structured \
result. ALWAYS assign to `result` a dict with:

```python
result = {
    "answer": "Text answer in user's language",
    "chart": {  # or None
        "type": "bar|line|pie|table",
        "title": "Chart title",
        "labels": ["L1", "L2", ...],
        "datasets": [{"label": "Series", "data": [1, 2, 3]}]
    },
    "sources": [  # invoice/document references used
        {"categoryDate": "...", "supplier": "...", "total": 123.45, ...}
    ]
}
```

IMPORTANT: When user asks for a downloadable file (Excel, CSV, PDF), you MUST call generate_file \
directly. Do NOT use run_analysis to produce an "answer" — that will end the conversation without \
creating a file. Instead: dynamo_query → generate_file.
FILE DATA: generate_file automatically injects ALL query data (full dataset, not just the 50-item \
preview you see). You do NOT need to pass data in data_json — just describe what file to create in \
the prompt field. The sandbox receives data["query_1"]["items"] etc. with ALL records. \
NEVER reference data with JS templates like ${...} — just describe the columns/format you want.
DATA CACHE: Query results from recent turns are cached automatically. If the user asks to \
generate a file with data you already queried recently (e.g., "ponlo en Excel"), you can call \
generate_file directly — the cached data will be used. You do NOT need to re-query. \
However, if more than 10 minutes have passed or the user asks for different data, query again.

NUMBER FORMATTING: Use Spanish format in answer text (1.234,56 EUR). Keep raw numbers in chart data.
TODAY'S DATE: 2026-03-21."""


def _build_system_prompt(extra_system: str = "", location_id: str = "") -> list[dict]:
    """
    Build the system prompt as content blocks for optimal prompt caching.

    Returns a list of content blocks (Anthropic format) with cache_control markers.
    Block 1 (schema) is cached — it's identical across all calls.
    Block 2 (rules + context) varies per task but is still cached within a conversation.
    """
    schema_text = _SCHEMA_BLOCK
    rules_text = _RULES_BLOCK

    if extra_system:
        rules_text += f"\n\nADDITIONAL CONTEXT:\n{extra_system}"
    rules_text += f"\nCURRENT CONTEXT: locationId={location_id}"

    return [
        {
            "type": "text",
            "text": schema_text,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": rules_text,
            "cache_control": {"type": "ephemeral"},
        },
    ]


# ---------------------------------------------------------------------------
# Multimodal user message builder
# ---------------------------------------------------------------------------
def _build_user_content(
    text: str,
    attachments: list[dict] | None = None,
) -> str | list[dict]:
    """
    Build user message content, optionally with multimodal attachments.

    Each attachment: {"filename": str, "mime_type": str, "data": str (base64)}

    Uses LiteLLM native format:
    - Images → {"type": "image_url", "image_url": {"url": "data:mime;base64,..."}}
    - PDFs   → {"type": "file", "file": {"file_data": "data:application/pdf;base64,..."}}

    Unsupported types are described as text.
    """
    if not attachments:
        return text

    blocks: list[dict] = [{"type": "text", "text": text}]

    for att in attachments:
        mime = att.get("mime_type", "application/octet-stream")
        b64 = att.get("data", "")
        fname = att.get("filename", "file")

        if mime in _IMAGE_MIMES:
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        elif mime in _PDF_MIMES:
            blocks.append({
                "type": "file",
                "file": {"file_data": f"data:{mime};base64,{b64}"},
            })
        else:
            blocks.append({
                "type": "text",
                "text": f"[Attached file: {fname} ({mime}) — unsupported for direct viewing]",
            })

    return blocks


def _build_artifact_context(chat_artifacts: list[dict] | None) -> str:
    """
    Build a context string describing files generated in previous turns.
    This tells the agent what files exist and can be edited.
    """
    if not chat_artifacts:
        return ""

    lines = ["PREVIOUSLY GENERATED FILES (available for editing via edit_file tool):"]
    for a in chat_artifacts:
        fname = a.get("filename", "?")
        task_id = a.get("task_id", "?")
        url = a.get("url", "")
        lines.append(f"  - {fname} (task_id={task_id}, url={url})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------
UNIFIED_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "dynamo_query",
            "description": "Execute a DynamoDB query. locationId is auto-enforced.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Table name without stage prefix",
                        "enum": [
                            "User_Expenses", "User_Invoice_Incomes", "Bank_Reconciliations",
                            "Payroll_Slips", "Delivery_Notes", "Employees", "Providers",
                            "Customers", "Daily_Stats", "Monthly_Stats",
                        ],
                    },
                    "index_name": {
                        "type": "string",
                        "description": "GSI name (null for primary key query)",
                    },
                    "pk_field": {
                        "type": "string",
                        "description": "Partition key field name (default: userId)",
                        "default": "userId",
                    },
                    "pk_value": {
                        "type": "string",
                        "description": "PK value. For userId/locationId this is auto-set. Only provide for composite keys.",
                    },
                    "sk_field": {
                        "type": "string",
                        "description": "Sort key field name",
                    },
                    "sk_condition": {
                        "type": "object",
                        "description": "Sort key condition",
                        "properties": {
                            "op": {"type": "string", "enum": ["eq", "between", "begins_with", "gt", "lt"]},
                            "value": {"type": "string"},
                            "value2": {"type": "string", "description": "Second value for 'between'"},
                        },
                        "required": ["op", "value"],
                    },
                    "filter_expression": {
                        "description": "Post-query filter(s). Single object or array.",
                        "oneOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string"},
                                    "op": {"type": "string", "enum": ["eq", "ne", "contains", "begins_with", "exists", "gt", "lt"]},
                                    "value": {},
                                },
                                "required": ["field", "op", "value"],
                            },
                            {"type": "array", "items": {"type": "object"}},
                        ],
                    },
                    "limit": {"type": "integer", "description": "Max items to return"},
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_analysis",
            "description": (
                "Execute Python code to analyze data from previous queries. "
                "Access `data` dict with query results keyed by 'query_N'. "
                "Each entry: {items, count, table}. "
                "IMPORTANT: ALWAYS assign to `result` a dict with at minimum "
                "{\"answer\": \"text\"}, optionally \"chart\" and \"sources\". "
                "This allows immediate return without extra LLM calls.\n"
                "Available: len, sum, min, max, round, sorted, enumerate, zip, map, filter, "
                "list, dict, set, tuple, str, int, float, bool, range, any, all, reversed, json, Decimal."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code. Use `data` dict. MUST assign to `result` a dict with 'answer' key.",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_file",
            "description": (
                "Generate Excel/CSV/chart files using AI code execution sandbox. "
                "The AI writes and runs Python code (openpyxl, pandas, matplotlib). "
                "Use for downloadable reports, exports, detailed Excel files. "
                "NOTE: All query data is AUTOMATICALLY injected as base64-encoded JSON — "
                "the sandbox receives the FULL dataset (all items, not the 50 you see). "
                "You do NOT need to pass the data yourself in data_json. "
                "Just describe what file to create in the prompt. "
                "The sandbox decodes data['query_N']['items'] with ALL records."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed instructions: file type, sheets, columns, data, formatting.",
                    },
                    "data_json": {
                        "type": "string",
                        "description": "JSON string with data to include in the file.",
                    },
                },
                "required": ["prompt", "data_json"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Edit a previously generated file (Excel, CSV, etc.). "
                "Provide the task_id and filename of the existing file, plus "
                "a prompt describing the edits. The AI code execution sandbox "
                "receives the existing file and modifies it in place."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "task_id of the previously generated file.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Filename to edit (e.g. 'Informe_Gastos.xlsx').",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Detailed instructions for what to change in the file.",
                    },
                    "data_json": {
                        "type": "string",
                        "description": "Optional: additional data for the edit (JSON string).",
                        "default": "",
                    },
                },
                "required": ["task_id", "filename", "prompt"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------
@observe(name="unified_agent")
def run_agent(
    user_message: str,
    location_id: str,
    model_id: str = "claude-sonnet-4.5",
    conversation_history: list[dict] | None = None,
    on_event: EventCallback | None = None,
    chat_id: str | None = None,
    task_id: str | None = None,
    max_iterations: int = 15,
    extra_system: str = "",
    attachments: list[dict] | None = None,
    chat_artifacts: list[dict] | None = None,
) -> dict[str, Any]:
    """
    Run the unified agent. Handles everything from simple answers to complex tasks.

    Args:
        attachments: User-uploaded files [{filename, mime_type, data (base64)}]
        chat_artifacts: Files generated in previous turns [{filename, task_id, url}]

    Returns:
        {
            "answer": str,
            "chart": dict | None,
            "sources": list[dict],
            "artifacts": list[dict],
            "usage": list[dict],
        }
    """
    emit = on_event or _noop

    # Build system prompt as content blocks (Mejora 2: separate cached blocks)
    # Inject artifact context so the agent knows about previously generated files
    artifact_ctx = _build_artifact_context(chat_artifacts)
    full_extra = extra_system
    if artifact_ctx:
        full_extra = f"{extra_system}\n\n{artifact_ctx}" if extra_system else artifact_ctx
    system_blocks = _build_system_prompt(full_extra, location_id)

    messages: list[dict] = [{"role": "system", "content": system_blocks}]
    if conversation_history:
        messages.extend(conversation_history)

    # Build user message with multimodal attachments (images/PDFs via LiteLLM native)
    user_content = _build_user_content(user_message, attachments)
    messages.append({"role": "user", "content": user_content})

    query_results: dict[str, dict] = {}
    query_counter = 0
    sources_collected: list[dict] = []
    artifacts: list[dict] = []
    usage_records: list[dict] = []

    emit("agent_start", {"question": user_message, "model": model_id})

    for iteration in range(max_iterations):
        # Check cancellation
        if chat_id and is_cancelled(chat_id):
            raise CancelledError(f"Chat {chat_id} cancelled")
        if task_id and is_cancelled(task_id):
            raise CancelledError(f"Task {task_id} cancelled")

        if iteration == 0:
            emit("thinking", {"step": 1, "message": "Analizando tu pregunta..."})
        else:
            emit("thinking", {"step": iteration + 1, "message": "Procesando resultados..."})

        response = traced_completion(
            model_id=model_id,
            messages=messages,
            step=f"agent_iter_{iteration + 1}",
            chat_id=chat_id,
            task_id=task_id,
            location_id=location_id,
            tools=UNIFIED_TOOLS,
            temperature=0.1,
        )

        u = getattr(response, "usage", None)
        prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
        completion_tokens = getattr(u, "completion_tokens", 0) or 0
        total_tokens = getattr(u, "total_tokens", 0) or 0
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(u, "cache_creation_input_tokens", 0) or 0
        usage_records.append({
            "model": model_id,
            "step": f"agent_iter_{iteration + 1}",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
        })

        if cache_read > 0:
            log.info(f"[iter_{iteration+1}] Cache hit: {cache_read} tokens read from cache")

        # Track cost if running as task
        if task_id:
            from hackathon_backend.services.lambdas.agent.core.chat_store import _estimate_cost
            from hackathon_backend.services.lambdas.agent.core.task_manager import add_task_cost, check_budget
            cost = _estimate_cost(model_id, prompt_tokens, completion_tokens, cache_read, cache_creation)
            add_task_cost(task_id, total_tokens, cost)
            budget = check_budget(task_id)
            if not budget.get("ok"):
                return {
                    "answer": f"Presupuesto agotado: {budget.get('reason', '')}",
                    "chart": None,
                    "sources": sources_collected,
                    "artifacts": artifacts,
                    "usage": usage_records,
                }

        choice = response.choices[0]

        # Agent finished — no more tool calls
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            final_text = choice.message.content or ""
            emit("agent_done", {"message": "Respuesta generada"})

            # Try to extract structured result from the text
            result = _parse_final_response(final_text, sources_collected)
            result["artifacts"] = artifacts
            result["usage"] = usage_records
            return result

        # Process tool calls
        messages.append(choice.message)

        # Summarize what tools the agent decided to call
        tool_names = [tc.function.name for tc in choice.message.tool_calls]
        tool_summary = ", ".join(tool_names)
        emit("tool_calls", {
            "message": f"Ejecutando: {tool_summary}",
            "tools": tool_names,
            "iteration": iteration + 1,
        })

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

                table_name = args["table_name"]
                table_label = table_name.replace("_", " ")
                emit("querying", {
                    "message": f"Consultando {table_label}...",
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
                # Cache for reuse in subsequent turns (e.g., "put that in Excel")
                _cache_query_results(chat_id, query_results)

                if result.get("success"):
                    emit("query_result", {
                        "query_key": query_key,
                        "table": table_name,
                        "count": result["count"],
                        "message": f"Encontrados {result['count']} registros en {table_label}",
                    })
                    # Collect sources
                    if table_name in ("User_Expenses", "User_Invoice_Incomes"):
                        for item in result["items"]:
                            src = _extract_source(item)
                            if src:
                                sources_collected.append(src)
                    elif table_name == "Bank_Reconciliations":
                        for item in result["items"]:
                            sources_collected.append({
                                "categoryDate": item.get("SK", ""),
                                "supplier": item.get("merchant") or item.get("description", ""),
                                "invoice_date": item.get("bookingDate"),
                                "total": item.get("amount"),
                                "reconciled": bool(item.get("reconciled")) or item.get("status") == "MATCHED",
                                "category": "BANK",
                                "matched_expense_id": item.get("matched_expense_id"),
                                "matched_invoice_id": item.get("matched_invoice_id"),
                                "match_type": item.get("match_type"),
                                "transactionId": item.get("transactionId"),
                            })

                # Build slim response for LLM (Mejora 1)
                response_for_llm = {
                    "query_key": query_key,
                    "success": result["success"],
                    "table": result["table"],
                    "count": result["count"],
                }
                if not result["success"]:
                    response_for_llm["error"] = result.get("error", "Unknown")
                else:
                    items_for_llm = result["items"][:50]
                    # Slim: only fields the LLM needs for decision-making
                    slim = [_slim_item(it, table_name) for it in items_for_llm]
                    response_for_llm["items"] = slim
                    if result["count"] > 50:
                        response_for_llm["note"] = (
                            f"Showing 50 of {result['count']}. "
                            "Use run_analysis to access the full dataset."
                        )

                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps(response_for_llm, ensure_ascii=False, default=str),
                })

            elif fn_name == "run_analysis":
                # Load cached query results if none this turn
                if not query_results:
                    cached = _get_cached_query_results(chat_id)
                    if cached:
                        query_results = cached
                        log.info(f"[run_analysis] Loaded {len(cached)} cached queries for chat {chat_id}")

                code = args["code"]
                code_preview = code.strip().split("\n")[0][:80]
                emit("analyzing", {
                    "message": f"Ejecutando analisis de datos...",
                    "detail": code_preview,
                })
                result = _execute_code(code, query_results)

                if result["success"] and isinstance(result["result"], dict):
                    analysis = result["result"]
                    if "answer" in analysis:
                        emit("agent_done", {"message": "Análisis completado"})
                        return {
                            "answer": analysis.get("answer", ""),
                            "chart": analysis.get("chart"),
                            "sources": analysis.get("sources") or sources_collected,
                            "artifacts": artifacts,
                            "usage": usage_records,
                        }

                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

            elif fn_name == "generate_file":
                prompt_preview = (args.get("prompt", ""))[:100]
                emit("generating", {
                    "message": f"Generando archivo (code execution)...",
                    "detail": prompt_preview,
                    "model": model_id,
                })

                file_task_id = task_id or f"chat_{str(uuid.uuid4())[:8]}"
                emit("code_exec_start", {
                    "message": f"Escribiendo y ejecutando codigo Python ({model_id})...",
                    "task_id": file_task_id,
                })

                # If no queries this turn, try to load from cache (previous turns)
                if not query_results:
                    cached = _get_cached_query_results(chat_id)
                    if cached:
                        query_results = cached
                        log.info(f"[generate_file] Loaded {len(cached)} cached queries for chat {chat_id}")
                    else:
                        tool_result = json.dumps({
                            "error": True,
                            "message": (
                                "ERROR: No query data available and no cached data found. "
                                "You must call dynamo_query first to fetch the data, "
                                "then call generate_file again."
                            ),
                        })
                        messages.append({"role": "tool", "tool_call_id": tc_id, "content": tool_result})
                        continue

                # CRITICAL: Pass FULL query data as base64-encoded JSON.
                # The LLM's data_json is unreliable (JS templates, truncated, etc.)
                # We encode full query_results as base64 — the DATA section will
                # contain ONLY the raw base64 string, making it trivial for the
                # sandbox LLM to decode.
                import base64 as _b64_gen

                gen_prompt = args.get("prompt", "")

                # Build full dataset from all queries
                full_data = {}
                total_items = 0
                for qk, qv in query_results.items():
                    full_data[qk] = {
                        "items": qv.get("items", []),
                        "count": qv.get("count", 0),
                        "table": qv.get("table", ""),
                    }
                    total_items += len(qv.get("items", []))

                data_json_bytes = json.dumps(full_data, ensure_ascii=False, default=str).encode("utf-8")
                data_b64 = _b64_gen.b64encode(data_json_bytes).decode()

                # data_context = raw base64 string (nothing else)
                data_for_exec = data_b64

                # Prepend data loading instructions to the prompt
                gen_prompt = (
                    f"IMPORTANT — DATA LOADING (YOU MUST DO THIS FIRST):\n"
                    f"The full dataset ({total_items} items) is in the DATA section below as a raw base64 string.\n"
                    f"Copy the ENTIRE content of the DATA section into a Python variable and decode it:\n"
                    f"```python\n"
                    f"import json, base64\n"
                    f"# The DATA section below contains the raw base64 string — copy it all\n"
                    f"raw_b64 = '<entire content of DATA section>'\n"
                    f"data = json.loads(base64.b64decode(raw_b64))\n"
                    f"# data is a dict: {{'query_1': {{'items': [...], 'count': N, 'table': 'TableName'}}, ...}}\n"
                    f"# Use: items = data['query_1']['items']  # list of {total_items} dicts\n"
                    f"```\n"
                    f"The DATA section contains {total_items} real items from the database.\n"
                    f"NEVER use sample/dummy/fake data. NEVER create example data. Use ONLY the decoded data.\n"
                    f"NEVER limit to a subset — use ALL {total_items} items.\n\n"
                    f"TASK:\n{args.get('prompt', '')}"
                )

                code_result = _native_code_exec(
                    prompt=gen_prompt,
                    model_id=model_id,
                    data_context=data_for_exec,
                    task_id=file_task_id,
                    system_prompt=CODE_EXEC_SYSTEM,
                )

                # Track usage
                if code_result.get("usage"):
                    cu = code_result["usage"]
                    if isinstance(cu, dict):
                        usage_records.append(cu)
                    elif isinstance(cu, list):
                        usage_records.extend(cu)

                # Collect artifacts
                file_response = {
                    "success": code_result.get("success", False),
                    "files": [],
                    "text": code_result.get("text", "")[:500],
                }
                generated_filenames = []
                from hackathon_backend.services.lambdas.agent.core.storage import _use_s3 as _gen_s3_check, save_artifact as _gen_save
                for f in code_result.get("files", []):
                    file_url = f"/api/tasks/{file_task_id}/artifacts/{f['filename']}"
                    # Upload to S3 in Lambda mode and use presigned URL
                    if _gen_s3_check():
                        gen_path = f["path"]
                        if os.path.isfile(gen_path):
                            with open(gen_path, "rb") as _gfh:
                                s3_res = _gen_save(file_task_id, f["filename"], _gfh.read())
                            file_url = s3_res.get("url", file_url)
                    artifact = {
                        "filename": f["filename"],
                        "path": f["path"],
                        "task_id": file_task_id,
                        "type": f.get("type", "excel"),
                        "size_bytes": f.get("size_bytes", 0),
                        "url": file_url,
                    }
                    artifacts.append(artifact)
                    generated_filenames.append(f["filename"])
                    file_response["files"].append({
                        "filename": f["filename"],
                        "url": artifact["url"],
                    })

                    # Register artifact if running as task
                    if task_id:
                        from hackathon_backend.services.lambdas.agent.core.task_manager import add_task_artifact
                        add_task_artifact(task_id, artifact)

                if generated_filenames:
                    emit("file_generated", {
                        "message": f"Archivo generado: {', '.join(generated_filenames)}",
                        "files": generated_filenames,
                        "success": True,
                    })
                elif not code_result.get("success"):
                    emit("file_generated", {
                        "message": "Error generando archivo, reintentando...",
                        "success": False,
                    })

                messages.append({
                    "role": "tool", "tool_call_id": tool_call.id,
                    "content": json.dumps(file_response, ensure_ascii=False, default=str),
                })

            elif fn_name == "edit_file":
                edit_task_id = args.get("task_id", "")
                edit_filename = args.get("filename", "")
                edit_prompt = args.get("prompt", "")

                emit("generating", {
                    "message": f"Editando archivo {edit_filename}...",
                    "detail": edit_prompt[:100],
                    "model": model_id,
                })

                # Find the existing file — local first, then S3
                existing_path = os.path.join(ARTIFACTS_DIR, edit_task_id, edit_filename)
                if not os.path.isfile(existing_path):
                    # In Lambda mode, file may be in S3 — download it locally
                    from hackathon_backend.services.lambdas.agent.core.storage import _use_s3, get_artifact
                    if _use_s3():
                        file_bytes = get_artifact(edit_task_id, edit_filename)
                        if file_bytes:
                            os.makedirs(os.path.join(ARTIFACTS_DIR, edit_task_id), exist_ok=True)
                            with open(existing_path, "wb") as _fw:
                                _fw.write(file_bytes)
                    if not os.path.isfile(existing_path):
                        messages.append({
                            "role": "tool", "tool_call_id": tool_call.id,
                            "content": json.dumps({
                                "error": f"File not found: {edit_filename} (task_id={edit_task_id})"
                            }),
                        })
                        continue

                # Read existing file as base64 for the code sandbox
                import base64 as _b64
                with open(existing_path, "rb") as fh:
                    file_b64 = _b64.b64encode(fh.read()).decode()
                file_size = os.path.getsize(existing_path)

                # Build prompt for code execution: edit the existing file
                edit_code_prompt = (
                    f"You are editing an EXISTING file: {edit_filename} ({file_size} bytes).\n"
                    f"The file is provided as base64 in the variable EXISTING_FILE_B64.\n"
                    f"First decode it and load it, then apply the following edits:\n\n"
                    f"{edit_prompt}\n\n"
                    f"Save the modified file with the SAME filename."
                )

                emit("code_exec_start", {
                    "message": f"Editando {edit_filename} con AI code execution...",
                    "task_id": edit_task_id,
                })

                code_result = _native_code_exec(
                    prompt=edit_code_prompt,
                    model_id=model_id,
                    data_context=json.dumps({
                        "EXISTING_FILE_B64": file_b64,
                        "filename": edit_filename,
                        **(json.loads(args["data_json"]) if args.get("data_json") else {}),
                    }),
                    task_id=edit_task_id,
                    system_prompt=CODE_EXEC_SYSTEM,
                )

                # Track usage
                if code_result.get("usage"):
                    cu = code_result["usage"]
                    if isinstance(cu, dict):
                        usage_records.append(cu)
                    elif isinstance(cu, list):
                        usage_records.extend(cu)

                # Collect edited artifacts
                file_response = {
                    "success": code_result.get("success", False),
                    "files": [],
                    "text": code_result.get("text", "")[:500],
                    "edited": True,
                }
                from hackathon_backend.services.lambdas.agent.core.storage import _use_s3 as _s3_check, save_artifact as _save_art
                for f in code_result.get("files", []):
                    file_url = f"/api/tasks/{edit_task_id}/artifacts/{f['filename']}"
                    # Upload edited file to S3 in Lambda mode
                    if _s3_check():
                        edited_path = f["path"]
                        if os.path.isfile(edited_path):
                            with open(edited_path, "rb") as _fh:
                                s3_res = _save_art(edit_task_id, f["filename"], _fh.read())
                            file_url = s3_res.get("url", file_url)
                    artifact = {
                        "filename": f["filename"],
                        "path": f["path"],
                        "task_id": edit_task_id,
                        "type": f.get("type", "excel"),
                        "size_bytes": f.get("size_bytes", 0),
                        "url": file_url,
                        "edited": True,
                    }
                    artifacts.append(artifact)
                    file_response["files"].append({
                        "filename": f["filename"],
                        "url": artifact["url"],
                    })

                if code_result.get("files"):
                    fnames = [f["filename"] for f in code_result["files"]]
                    emit("file_generated", {
                        "message": f"Archivo editado: {', '.join(fnames)}",
                        "files": fnames,
                        "success": True,
                        "edited": True,
                    })

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
    return {
        "answer": "Se ha alcanzado el límite de iteraciones.",
        "chart": None,
        "sources": sources_collected,
        "artifacts": artifacts,
        "usage": usage_records,
    }


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------
def _parse_final_response(text: str, default_sources: list[dict]) -> dict:
    """Parse the agent's final text response into structured output."""
    result = {"answer": text, "chart": None, "sources": default_sources}

    if "```json" in text:
        try:
            start = text.index("```json") + 7
            end = text.index("```", start)
            parsed = json.loads(text[start:end].strip())
            if "answer" in parsed:
                result["answer"] = parsed["answer"]
            if parsed.get("chart"):
                result["chart"] = parsed["chart"]
            if parsed.get("sources"):
                result["sources"] = parsed["sources"]
            clean = (text[:start - 7] + text[end + 3:]).strip()
            if clean and not result.get("answer"):
                result["answer"] = clean
        except (ValueError, json.JSONDecodeError):
            pass

    return result


# ---------------------------------------------------------------------------
# Detect if a message needs background processing (heavy task)
# ---------------------------------------------------------------------------
_HEAVY_TASK_KEYWORDS: dict[str, list[str]] = {
    "cash_flow_forecast": [
        "prevision tesoreria", "prevision de tesoreria", "cash flow", "flujo de caja",
        "prevision de caja", "13 semanas", "forecast tesoreria",
        "previsión de tesorería", "previsión tesorería", "prevision caja", "tesoreria 13",
    ],
    "pack_reporting": [
        "pack reporting", "reporting mensual", "p&l mensual", "cuenta resultados", "balance mensual",
    ],
    "modelo_303": [
        "modelo 303", "iva trimestral", "liquidacion iva", "borrador 303",
    ],
    "aging_analysis": [
        "aging", "antiguedad", "cobros pendientes", "deuda por antiguedad", "facturas vencidas",
    ],
    "client_profitability": [
        "rentabilidad cliente", "rentabilidad por cliente", "margen por cliente",
    ],
    "modelo_347": [
        "modelo 347", "terceros 3005", "declaracion terceros",
    ],
    "three_way_matching": [
        "three way matching", "cruce tres vias", "albaranes facturas",
    ],
    "bank_reconciliation": [
        "conciliacion bancaria", "conciliación bancaria", "conciliar facturas",
        "conciliar transacciones", "reconciliacion", "reconciliación",
        "transacciones sin conciliar", "facturas sin conciliar",
        "bank reconciliation", "matching bancario", "cruce bancario",
        "conciliar banco", "conciliar pagos", "pagos sin conciliar",
        "conciliar las facturas", "conciliar las transacciones",
        "conciliar con el banco", "cruce con banco",
    ],
}

# Questions that should NOT be treated as heavy tasks
_INFORMATIONAL_PREFIXES = [
    "que es", "qué es", "que son", "qué son", "como funciona", "cómo funciona",
    "como se calcula", "cómo se calcula", "explicame", "explícame", "dime que es",
    "que significa", "qué significa", "para que sirve", "para qué sirve",
    "what is", "how does", "explain",
]


def detect_heavy_task(user_message: str) -> str | None:
    """
    Detect if a message requires heavy background processing.

    Returns the task_type string if heavy, or None for inline processing.
    This is used by the server to decide whether to run the agent inline
    (with streaming) or in a background task (with progress tracking).
    """
    msg_lower = user_message.lower()

    # Informational questions are never heavy
    for prefix in _INFORMATIONAL_PREFIXES:
        if msg_lower.startswith(prefix) or msg_lower.startswith("¿" + prefix):
            return None

    for task_type, keywords in _HEAVY_TASK_KEYWORDS.items():
        for kw in keywords:
            if kw in msg_lower:
                return task_type
    return None
