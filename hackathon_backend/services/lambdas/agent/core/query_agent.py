"""
Specialized Query Agent — plans and executes multi-step DynamoDB queries.

This agent:
1. Receives a user question + data_requests from the orchestrator
2. Plans an optimal query strategy (e.g., find provider CIF first, then filter invoices)
3. Executes queries using GSIs (no scans), hardcoding locationId for security
4. Runs Python code to compute metrics/aggregations
5. Returns: answer text, chart data, and source references

Security: locationId is ALWAYS hardcoded — the LLM cannot override it.
"""
from __future__ import annotations

import json
import os
import traceback
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key, Attr
from langfuse import observe

from hackathon_backend.services.lambdas.agent.core.config import completion

AWS_PROFILE = os.getenv("AWS_PROFILE", "hackathon-equipo1")
AWS_REGION = os.getenv("AWS_REGION", "eu-west-3")

_dynamodb = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        _dynamodb = session.resource("dynamodb")
    return _dynamodb


def _sanitize(obj: Any) -> Any:
    """Convert Decimal/set to JSON-safe types."""
    if isinstance(obj, Decimal):
        f = float(obj)
        return int(f) if f == int(f) else f
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, set):
        return [_sanitize(i) for i in obj]
    return obj


# ---------------------------------------------------------------------------
# Source extraction — pulls the "paper reference" fields from invoice items
# ---------------------------------------------------------------------------
def _extract_source(item: dict) -> dict | None:
    """Extract source reference from an invoice item for the frontend."""
    cat_date = item.get("categoryDate")
    if not cat_date:
        return None

    source = {
        "categoryDate": cat_date,
        "supplier": item.get("supplier") or item.get("client_name"),
        "supplier_cif": item.get("supplier_cif") or item.get("client_cif"),
        "invoice_date": item.get("invoice_date"),
        "due_date": item.get("due_date"),
        "total": item.get("total"),
        "importe": item.get("importe"),
        "reconciled": bool(item.get("reconciled")) if item.get("reconciled") is not None else False,
        "category": item.get("category"),
        "concept": item.get("concept"),
    }

    # Extract bounding boxes from field_images
    field_images = item.get("field_images", {})
    if isinstance(field_images, dict):
        total_fi = field_images.get("invoice_amounts_total", {})
        if isinstance(total_fi, dict) and total_fi.get("bounding_box"):
            source["total_bounding_box"] = _sanitize(total_fi["bounding_box"])

    return source


# ---------------------------------------------------------------------------
# DynamoDB query functions — the agent calls these via tool use
# ---------------------------------------------------------------------------
STAGE = os.getenv("TABLE_ENV_PREFIX", "Dev")

# Fields to keep when sending DynamoDB items to LLM (strip heavy fields)
KEEP_FIELDS = {
    "userId", "locationId", "categoryDate", "category", "concept",
    "total", "importe", "ivas", "supplier", "supplier_cif",
    "client_name", "client_cif", "invoice_date", "due_date", "pnl_date",
    "charge_date", "reconciled", "amount_due", "amount_paid",
    "documentKind", "vatTotalAmount", "vatDeductibleAmount",
    "vatNonDeductibleAmount", "vatOperationType", "retencion", "invoice_number",
    "accountingEntries", "all_products",
    "recon_state_date", "invoice_supplier_id",
    "nombre", "cif", "trade_name", "facturas", "provincia",
    "emails", "phones", "website",
    "amount", "bookingDate", "description", "merchant", "status",
    "transactionId", "SK", "matched_expense_id", "matched_invoice_id",
    "employee_nif", "org_cif", "payroll_info", "payroll_date",
    "employeeNif", "name", "position",
    "dayKey", "monthKey",
}


@observe(name="dynamo_query")
def _execute_query(
    table_name: str,
    location_id: str,
    index_name: str | None = None,
    pk_field: str = "userId",
    pk_value: str | None = None,
    sk_field: str | None = None,
    sk_condition: dict | None = None,
    filter_expression: dict | None = None,
    limit: int | None = None,
    fields_to_return: list[str] | None = None,
) -> dict:
    """
    Execute a DynamoDB query. locationId is ALWAYS enforced.

    sk_condition format: {"op": "between|begins_with|eq|gt|lt", "value": ..., "value2": ...}
    filter_expression format: {"field": ..., "op": "eq|contains|begins_with", "value": ...}
    """
    ddb = _get_dynamodb()
    full_table_name = f"{STAGE}_{table_name}"
    table = ddb.Table(full_table_name)

    # SECURITY: Always use location_id, never trust LLM-provided PK value
    # for userId/locationId fields
    actual_pk_value = location_id
    if pk_field not in ("userId", "locationId", "PK"):
        # Composite PK like userSupplierKey — must contain locationId
        if pk_value and location_id in str(pk_value):
            actual_pk_value = pk_value
        else:
            actual_pk_value = pk_value if pk_value else location_id

    query_kwargs: dict[str, Any] = {}
    if index_name:
        query_kwargs["IndexName"] = index_name

    # Build key condition
    kc = Key(pk_field).eq(actual_pk_value)
    if sk_field and sk_condition:
        op = sk_condition.get("op", "eq")
        val = sk_condition["value"]
        if op == "between":
            val2 = sk_condition["value2"]
            kc = kc & Key(sk_field).between(val, val2)
        elif op == "begins_with":
            kc = kc & Key(sk_field).begins_with(val)
        elif op == "eq":
            kc = kc & Key(sk_field).eq(val)
        elif op == "gt":
            kc = kc & Key(sk_field).gt(val)
        elif op == "lt":
            kc = kc & Key(sk_field).lt(val)
    query_kwargs["KeyConditionExpression"] = kc

    # Filter expression (post-query)
    if filter_expression:
        filters = filter_expression if isinstance(filter_expression, list) else [filter_expression]
        fe = None
        for f in filters:
            field = f["field"]
            op = f.get("op", "eq")
            val = f["value"]
            if op == "eq":
                cond = Attr(field).eq(val)
            elif op == "ne":
                cond = Attr(field).ne(val)
            elif op == "contains":
                cond = Attr(field).contains(val)
            elif op == "begins_with":
                cond = Attr(field).begins_with(val)
            elif op == "exists":
                cond = Attr(field).exists() if val else Attr(field).not_exists()
            elif op == "gt":
                cond = Attr(field).gt(val)
            elif op == "lt":
                cond = Attr(field).lt(val)
            else:
                cond = Attr(field).eq(val)
            fe = cond if fe is None else (fe & cond)
        if fe:
            query_kwargs["FilterExpression"] = fe

    if limit:
        query_kwargs["Limit"] = limit

    # Projection expression
    if fields_to_return:
        # Always include key fields + source fields
        essential = {"userId", "locationId", "categoryDate", "supplier", "supplier_cif",
                     "client_name", "client_cif", "invoice_date", "due_date", "total",
                     "importe", "reconciled", "category", "concept", "field_images"}
        all_fields = list(set(fields_to_return) | essential)
        # DynamoDB reserved words need expression attribute names
        # Skip projection for simplicity — fields are small enough
        pass

    # Execute with pagination
    items = []
    try:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response and (not limit or len(items) < limit):
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = table.query(**query_kwargs)
            items.extend(response.get("Items", []))
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "table": table_name,
            "items": [],
            "count": 0,
        }

    items = _sanitize(items)
    if limit:
        items = items[:limit]

    return {
        "success": True,
        "table": table_name,
        "items": items,
        "count": len(items),
    }


# ---------------------------------------------------------------------------
# Code execution — runs Python code on fetched data for metrics
# ---------------------------------------------------------------------------
@observe(name="execute_analysis_code")
def _execute_code(code: str, data_context: dict) -> dict:
    """
    Execute Python code for data analysis. The code has access to:
    - `data`: dict with all fetched query results
    - `json`, `Decimal` modules
    Returns whatever the code assigns to `result`.
    """
    safe_globals = {
        "__builtins__": {
            "len": len, "sum": sum, "min": min, "max": max, "abs": abs,
            "round": round, "sorted": sorted, "enumerate": enumerate,
            "zip": zip, "map": map, "filter": filter, "list": list,
            "dict": dict, "set": set, "tuple": tuple, "str": str,
            "int": int, "float": float, "bool": bool, "range": range,
            "isinstance": isinstance, "type": type, "print": print,
            "any": any, "all": all, "reversed": reversed,
        },
        "json": json,
        "Decimal": Decimal,
        "data": data_context,
        "result": None,
    }

    try:
        exec(code, safe_globals)
        result = safe_globals.get("result")
        return {"success": True, "result": _sanitize(result)}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}", "result": None}


# ---------------------------------------------------------------------------
# Tool definitions for the query agent LLM
# ---------------------------------------------------------------------------
QUERY_AGENT_TOOLS = [
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
                        "description": "PK value. For userId/locationId this is auto-set to the user's locationId. Only provide for composite keys like userSupplierKey.",
                    },
                    "sk_field": {
                        "type": "string",
                        "description": "Sort key field name for the index being queried",
                    },
                    "sk_condition": {
                        "type": "object",
                        "description": "Sort key condition",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["eq", "between", "begins_with", "gt", "lt"],
                            },
                            "value": {"type": "string"},
                            "value2": {"type": "string", "description": "Second value for 'between' operator"},
                        },
                        "required": ["op", "value"],
                    },
                    "filter_expression": {
                        "description": "Post-query filter(s). Single object or array of objects.",
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
                            {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "field": {"type": "string"},
                                        "op": {"type": "string"},
                                        "value": {},
                                    },
                                },
                            },
                        ],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max items to return (optional)",
                    },
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
                "Execute Python code to analyze/aggregate the data fetched from previous queries. "
                "You have access to `data` dict which contains all previous query results keyed by 'query_N'. "
                "Each entry has: items (list of dicts), count (int), table (str). "
                "Assign your result to the `result` variable. "
                "Available: len, sum, min, max, round, sorted, enumerate, zip, map, filter, list, dict, set, etc.\n\n"
                "Example:\n"
                "```python\n"
                "items = data['query_1']['items']\n"
                "total_amount = sum(float(i.get('total', 0) or 0) for i in items)\n"
                "by_supplier = {}\n"
                "for i in items:\n"
                "    s = i.get('supplier', 'Unknown')\n"
                "    by_supplier[s] = by_supplier.get(s, 0) + float(i.get('total', 0) or 0)\n"
                "result = {'total': round(total_amount, 2), 'by_supplier': by_supplier}\n"
                "```"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Use `data` dict for query results. Assign output to `result`.",
                    },
                },
                "required": ["code"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Query Agent system prompt
# ---------------------------------------------------------------------------
QUERY_AGENT_SYSTEM = """\
You are a specialized DynamoDB query agent for a financial management system.
You receive a user's question and must plan + execute the optimal queries to answer it.

YOUR CAPABILITIES:
1. `dynamo_query` — Execute DynamoDB queries using GSIs for optimal performance
2. `run_analysis` — Run Python code to compute metrics, aggregations, and chart data

TABLES AND QUERY PATTERNS:

1. User_Expenses (expense invoices):
   PK=userId, SK=categoryDate (CATEGORY#YYYY-MM-DD#UUID)
   GSIs: UserIdInvoiceDateIndex(pk=userId,sk=invoice_date), UserIdSupplierCifIndex(pk=userId,sk=supplier_cif),
         UserIdPnlDateIndex(pk=userId,sk=pnl_date), UserByReconStateDate(pk=userId,sk=recon_state_date R#date/U#date),
         UserSupplierDateIndex(pk=userSupplierKey={userId}#{cif},sk=charge_date)
   Fields: total,importe,ivas[{type,base_imponible,amount}],supplier,supplier_cif,invoice_date,due_date,
           category,concept,reconciled,documentKind(invoice/credit_note),vatTotalAmount,retencion,
           accountingEntries[{accountCode,accountName,amount,side(DEBE/HABER),kind(provider/expense/asset/vat)}],
           all_products[{product_name,unit_price,quantity,final_price}],amount_due,amount_paid

2. User_Invoice_Incomes (income invoices):
   PK=userId, SK=categoryDate. Same GSIs pattern as expenses but with client_name/client_cif instead of supplier.
   GSIs: UserIdInvoiceDateIndex, UserIdClientCifIndex(pk=userId,sk=client_cif), UserByReconStateDate

3. Providers (supplier master): PK=locationId, SK=cif
   Fields: nombre,cif,trade_name,facturas(list of expense categoryDates),provincia,emails,phones

4. Customers: PK=locationId, SK=cif. Fields: nombre,cif,facturas

5. Bank_Reconciliations: PK=locationId, SK=MTXN#{bookingDate}#{transactionId}
   GSI: LocationByStatusDate(pk=locationId,sk=status_date={status}#{bookingDate})
   Fields: amount,bookingDate,description,merchant,status(PENDING/MATCHED),transactionId

6. Payroll_Slips: PK=locationId, SK=categoryDate({date}#{nif})
   GSI: OrgCifPeriodIndex(pk=org_cif,sk=PERIOD#{yyyy-mm}#EMP#{nif})
   Fields: employee_nif,payroll_info{gross_amount,net_amount,company_ss_contribution,irpf_amount}

7. Employees: PK=locationId, SK=employeeNif
8. Daily_Stats: PK=locationId, SK=dayKey
9. Monthly_Stats: PK=locationId, SK=monthKey

WORKFLOW:
1. PLAN: Think step by step. For complex questions, chain queries:
   - "Gasto en Makro?" → Query Providers to find Makro's CIF → Query User_Expenses with UserIdSupplierCifIndex
   - "Facturas sin pagar?" → Query UserByReconStateDate with sk begins_with "U#"
   - "Cuentas contables de X?" → Get expenses, look at accountingEntries field
   - "Productos/lamparas?" → Get expenses, look at all_products[].product_name field
   - "Prevision gastos?" → Query recent months, analyze trends with run_analysis

2. EXECUTE: Run queries. Each result stored as query_1, query_2, etc.
3. ANALYZE: ALWAYS call run_analysis as final step with structured output.

RULES:
- Use GSIs, never full scans. Date queries → UserIdInvoiceDateIndex. Supplier → UserIdSupplierCifIndex.
- Reconciliation: recon_state_date begins_with "R#" = paid, "U#" = unpaid. Also check reconciled field.
- For "unpaid": if reconciled is None/False/missing → unpaid. If reconciled is True → paid.
- locationId is auto-enforced for security.

CRITICAL: You MUST ALWAYS finish by calling run_analysis to compute the final structured answer.
NEVER just write a text response — always call run_analysis as the last step.
Your run_analysis code MUST assign to `result` a dict with this exact structure:

```python
result = {
    "answer": "Your text answer in the user's language (Spanish if they wrote in Spanish)",
    "chart": {  # or None if no chart makes sense
        "type": "bar|line|pie|table",
        "title": "Chart title",
        "labels": ["Label1", "Label2", ...],
        "datasets": [{"label": "Series name", "data": [1, 2, 3]}]
    },
    "sources": [  # List of invoice/document references used
        {
            "categoryDate": "COMPRAS#2024-08-29#uuid",
            "supplier": "Supplier Name",
            "supplier_cif": "B12345678",
            "invoice_date": "2024-08-29",
            "due_date": "2024-09-28",
            "total": 1234.56,
            "importe": 1000.00,
            "reconciled": false,
            "category": "COMPRAS",
            "concept": "MATERIAL LABORATORIO",
            "total_bounding_box": {"Height": 0.01, "Left": 0.88, "Top": 0.07, "Width": 0.06}
        }
    ]
}
```

For the sources, extract from each invoice item used:
- categoryDate (the full SK string — this is the document ID for the frontend)
- supplier/client name and CIF
- invoice_date, due_date
- total, importe (amounts)
- reconciled (true if reconciled field is truthy, false otherwise)
- category, concept
- total_bounding_box from field_images.invoice_amounts_total.bounding_box (if exists)

NUMBER FORMATTING: Use Spanish format in the answer text (1.234,56 EUR). Keep raw numbers in chart data and sources.
LANGUAGE: Always respond in the same language the user writes in.
TODAY'S DATE: 2026-03-20. Use this for relative date references ("last month" = February 2026).
"""


# ---------------------------------------------------------------------------
# Main query agent entry point
# ---------------------------------------------------------------------------
# Type for streaming callbacks
from typing import Callable

EventCallback = Callable[[str, dict], None]


def _noop_callback(event: str, data: dict) -> None:
    pass


@observe(name="query_agent")
def run_query_agent(
    user_question: str,
    data_requests: list[dict],
    location_id: str,
    model_id: str = "claude-sonnet-4.5",
    chart_suggestion: dict | None = None,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """
    Run the query agent: plans queries, executes them, analyzes results.

    Returns:
        {
            "answer": str,
            "chart": dict | None,
            "sources": list[dict],
        }
    """
    # Build context message with data requests info
    context_parts = [
        f"USER QUESTION: {user_question}",
        f"\nDATA REQUESTS FROM ORCHESTRATOR:",
    ]
    for i, req in enumerate(data_requests, 1):
        context_parts.append(f"\n{i}. Table: {req['table']}")
        context_parts.append(f"   Description: {req.get('description', '')}")
        if req.get("date_range"):
            context_parts.append(f"   Date range: {req['date_range']['from']} to {req['date_range']['to']}")
        if req.get("filters"):
            context_parts.append(f"   Filters: {json.dumps(req['filters'])}")
        if req.get("fields_needed"):
            context_parts.append(f"   Fields needed: {', '.join(req['fields_needed'])}")

    if chart_suggestion and chart_suggestion.get("type") != "none":
        context_parts.append(f"\nCHART SUGGESTION: type={chart_suggestion.get('type')}, title={chart_suggestion.get('title', '')}")
    context_parts.append("\nPlan your queries, execute them, analyze the data, and provide the final answer.")

    user_content = "\n".join(context_parts)

    messages: list[dict] = [
        {"role": "system", "content": QUERY_AGENT_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    emit = on_event or _noop_callback
    emit("agent_start", {"question": user_question, "data_requests": data_requests, "model": model_id})

    # Agent loop: keep calling tools until the LLM is done
    query_results: dict[str, dict] = {}
    query_counter = 0
    max_iterations = 15
    sources_collected: list[dict] = []
    usage_records: list[dict] = []

    for iteration in range(max_iterations):
        emit("thinking", {"step": iteration + 1, "message": "Planificando siguiente paso..." if iteration == 0 else "Analizando resultados..."})

        response = completion(
            model_id=model_id,
            messages=messages,
            tools=QUERY_AGENT_TOOLS,
            temperature=0.1,
        )

        # Track usage per iteration
        u = getattr(response, "usage", None)
        usage_records.append({
            "model": model_id,
            "step": f"query_agent_iter_{iteration + 1}",
            "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
            "total_tokens": getattr(u, "total_tokens", 0) or 0,
        })

        choice = response.choices[0]

        # If no tool calls, the agent is done
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            final_text = choice.message.content or ""
            emit("agent_done", {"message": "Respuesta generada"})
            result = _parse_final_response(final_text, sources_collected)
            result["usage"] = usage_records
            return result

        # Process tool calls
        messages.append(choice.message)

        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps({"error": "Invalid JSON in tool arguments"}),
                })
                continue

            if fn_name == "dynamo_query":
                query_counter += 1
                query_key = f"query_{query_counter}"

                table_label = args["table_name"].replace("_", " ")
                idx_label = f" (GSI: {args.get('index_name')})" if args.get("index_name") else ""
                emit("querying", {"message": f"Consultando {table_label}{idx_label}...", "table": args["table_name"], "query_key": query_key})

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
                    fields_to_return=args.get("fields_to_return"),
                )

                query_results[query_key] = result

                if result.get("success"):
                    emit("query_result", {"query_key": query_key, "table": args["table_name"], "count": result["count"],
                                          "message": f"Encontrados {result['count']} registros en {table_label}"})
                else:
                    emit("query_error", {"query_key": query_key, "table": args["table_name"], "error": result.get("error", "?")})

                # Collect sources from invoice/transaction items
                if result.get("success"):
                    if args["table_name"] in ("User_Expenses", "User_Invoice_Incomes"):
                        for item in result["items"]:
                            src = _extract_source(item)
                            if src:
                                sources_collected.append(src)
                    elif args["table_name"] == "Bank_Reconciliations":
                        for item in result["items"]:
                            sources_collected.append({
                                "categoryDate": item.get("SK", ""),
                                "supplier": item.get("merchant") or item.get("description", ""),
                                "invoice_date": item.get("bookingDate"),
                                "total": item.get("amount"),
                                "reconciled": item.get("status") == "MATCHED",
                                "category": "BANK",
                            })

                # Truncate items in tool response to avoid token overflow
                response_for_llm = {
                    "query_key": query_key,
                    "success": result["success"],
                    "table": result["table"],
                    "count": result["count"],
                }
                if not result["success"]:
                    response_for_llm["error"] = result.get("error", "Unknown error")
                else:
                    # Send items but truncate if too many
                    items_for_llm = result["items"][:50]
                    slim_items = []
                    for it in items_for_llm:
                        slim = {k: v for k, v in it.items() if k in KEEP_FIELDS}
                        slim_items.append(slim)
                    response_for_llm["items"] = slim_items
                    if result["count"] > 100:
                        response_for_llm["note"] = f"Showing first 100 of {result['count']} items. Use run_analysis on the full dataset."

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(response_for_llm, ensure_ascii=False, default=str),
                })

            elif fn_name == "run_analysis":
                emit("analyzing", {"message": "Ejecutando analisis de datos..."})
                code = args["code"]
                result = _execute_code(code, query_results)

                if result["success"] and isinstance(result["result"], dict):
                    analysis_result = result["result"]
                    # If analysis returned final structured answer, capture it
                    if "answer" in analysis_result:
                        emit("agent_done", {"message": "Respuesta generada"})
                        return {
                            "answer": analysis_result.get("answer", ""),
                            "chart": analysis_result.get("chart"),
                            "sources": analysis_result.get("sources", sources_collected),
                            "usage": usage_records,
                        }
                    if "sources" in analysis_result:
                        sources_collected = analysis_result.get("sources", sources_collected)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

            else:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps({"error": f"Unknown tool: {fn_name}"}),
                })

    # Max iterations reached — return what we have
    return {
        "answer": "Se ha alcanzado el limite de iteraciones del agente de consultas.",
        "chart": None,
        "sources": sources_collected,
        "usage": usage_records,
    }


def _parse_final_response(text: str, default_sources: list[dict]) -> dict:
    """Parse the agent's final text response into structured output."""
    # Try to find JSON in the response
    result = {"answer": text, "chart": None, "sources": default_sources}

    # Look for a JSON block with our expected structure
    if "```json" in text:
        try:
            json_start = text.index("```json") + 7
            json_end = text.index("```", json_start)
            parsed = json.loads(text[json_start:json_end].strip())
            if "answer" in parsed:
                result["answer"] = parsed["answer"]
            if "chart" in parsed and parsed["chart"]:
                result["chart"] = parsed["chart"]
            if "sources" in parsed and parsed["sources"]:
                result["sources"] = parsed["sources"]
            # Clean the JSON block from the answer if it was embedded
            clean_text = (text[:json_start - 7] + text[json_end + 3:]).strip()
            if clean_text and not result.get("answer"):
                result["answer"] = clean_text
        except (ValueError, json.JSONDecodeError):
            pass

    return result
