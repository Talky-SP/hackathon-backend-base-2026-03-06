"""
Task Playbooks — semantic intent classification + structured guidance.

Replaces keyword-based detection with a cheap LLM call (Gemini Flash)
that classifies user intent into a task type. When a known task type is
detected, its playbook guidance is injected into the system prompt.

Usage:
    task_type = classify_intent(question, model="gemini-3.0-flash")
    if task_type != "general":
        guidance = get_playbook_guidance(task_type)
        # inject guidance into system prompt
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task type enum
# ---------------------------------------------------------------------------
TASK_TYPES = [
    "cierre_contable",
    "conciliacion",
    "reportes_financieros",
    "deteccion_errores",
    "auditoria_iva",
    "control_fraude",
    "analisis_gastos",
    "optimizacion_proveedores",
    "rentabilidad",
    "prediccion_cashflow",
    "simulacion",
    "explicacion_humana",
    "general",
]

# ---------------------------------------------------------------------------
# Intent classification prompt
# ---------------------------------------------------------------------------
_CLASSIFY_PROMPT = """\
Classify the following user question into exactly one task type.
Return ONLY the task type string, nothing else.

Task types:
- cierre_contable: Monthly/quarterly accounting close, period close, journal entries review
- conciliacion: Bank reconciliation, matching invoices with bank transactions
- reportes_financieros: P&L, balance sheet, financial reporting pack, pack reporting
- deteccion_errores: Finding accounting errors, duplicate invoices, missing entries
- auditoria_iva: VAT audit, Modelo 303, IVA trimestral, tax compliance
- control_fraude: Fraud detection, anomalies, unusual transactions
- analisis_gastos: Expense analysis, spending by category/supplier, cost breakdown
- optimizacion_proveedores: Supplier optimization, price comparison, best deals
- rentabilidad: Profitability analysis, margins, client/product profitability
- prediccion_cashflow: Cash flow forecast, treasury prediction, 13-week forecast
- simulacion: What-if scenarios, decision simulation, impact analysis
- explicacion_humana: Explain a concept, what is X, how does Y work
- general: Any other question that doesn't fit the above categories

Question: {question}

Task type:"""


def classify_intent(question: str, model: str = "gemini-3.0-flash") -> str:
    """
    Classify user intent into a task type using a cheap/fast LLM call.

    Cost: ~50 input + 5 output tokens = ~$0.000008 with Gemini Flash.
    Latency: ~200ms.

    Falls back to keyword detection if LLM call fails.
    """
    try:
        from hackathon_backend.services.lambdas.agent.core.config import traced_completion
        response = traced_completion(
            model_id=model,
            messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(question=question)}],
            step="intent_classification",
            temperature=0.0,
            max_tokens=20,
        )
        text = (response.choices[0].message.content or "").strip().lower()
        # Clean up: LLM might return with quotes or extra text
        text = text.strip('"\'` \n')
        if text in TASK_TYPES:
            log.info(f"[playbooks] Intent classified: {text}")
            return text
        # Partial match
        for t in TASK_TYPES:
            if t in text:
                log.info(f"[playbooks] Intent partial match: {t} from '{text}'")
                return t
        log.info(f"[playbooks] Intent not recognized: '{text}', falling back to keyword")
    except Exception as e:
        log.warning(f"[playbooks] LLM classification failed: {e}, falling back to keyword")

    return _keyword_fallback(question)


def _keyword_fallback(question: str) -> str:
    """Fast keyword-based fallback for intent classification."""
    q = question.lower()

    _KEYWORD_MAP = {
        "cierre_contable": [
            "cierre contable", "cierre mensual", "cierre trimestral", "cerrar mes",
            "cerrar periodo", "asientos cierre", "cierre de mes",
        ],
        "conciliacion": [
            "conciliacion", "conciliación", "conciliar", "reconciliacion",
            "reconciliación", "matching bancario", "cruce bancario",
        ],
        "reportes_financieros": [
            "p&l", "cuenta de resultados", "balance", "pack reporting",
            "reporting mensual", "informe financiero", "estado financiero",
        ],
        "deteccion_errores": [
            "errores contables", "duplicados", "facturas duplicadas",
            "errores en factura", "discrepancias", "descuadre",
        ],
        "auditoria_iva": [
            "modelo 303", "iva trimestral", "liquidacion iva", "auditoria iva",
            "borrador 303", "iva soportado", "iva repercutido",
        ],
        "control_fraude": [
            "fraude", "anomalia", "anomalía", "sospechoso", "irregularidad",
        ],
        "analisis_gastos": [
            "analisis de gastos", "análisis de gastos", "gastos por categoria",
            "desglose gastos", "cuanto gasto", "cuánto gasto",
        ],
        "optimizacion_proveedores": [
            "optimizar proveedores", "comparar precios", "mejor proveedor",
            "ahorro proveedores", "negociar proveedor",
        ],
        "rentabilidad": [
            "rentabilidad", "margen", "beneficio por cliente",
            "rentabilidad cliente", "profitability",
        ],
        "prediccion_cashflow": [
            "cash flow", "flujo de caja", "tesoreria", "tesorería",
            "prevision de caja", "forecast", "13 semanas",
        ],
        "simulacion": [
            "simulacion", "simulación", "que pasaria", "qué pasaría",
            "what if", "escenario", "impacto de",
        ],
        "explicacion_humana": [
            "que es", "qué es", "explicame", "explícame", "como funciona",
            "cómo funciona", "que significa", "qué significa",
        ],
    }

    for task_type, keywords in _KEYWORD_MAP.items():
        for kw in keywords:
            if kw in q:
                return task_type

    return "general"


# ---------------------------------------------------------------------------
# Playbook guidance per task type
# ---------------------------------------------------------------------------
PLAYBOOKS: dict[str, dict[str, Any]] = {
    "cierre_contable": {
        "name": "Cierre Contable Mensual",
        "guidance": """\
PLAYBOOK — CIERRE CONTABLE:
You are performing a monthly accounting close. Follow this checklist:

1. QUERY User_Expenses (UserIdInvoiceDateIndex, pnl_date range for the month) — get all expense invoices.
2. QUERY User_Invoice_Incomes (same index) — get all income invoices.
3. QUERY Payroll_Slips for the month — payroll costs.
4. QUERY Bank_Reconciliations — all transactions for the month.
5. In run_code, perform these checks:
   a) Revenue completeness: all income invoices accounted for
   b) Expense completeness: all expense invoices categorized
   c) Bank reconciliation status: % matched vs unmatched
   d) Payroll booked correctly: gross, SS, IRPF entries balanced
   e) VAT check: IVA soportado vs repercutido totals
   f) Identify: missing invoices (bank txns without matching invoice), uncategorized expenses
6. Generate summary with key metrics and any blocking issues.

IMPORTANT: Start with multiple parallel dynamo_query calls to fetch all data at once.""",
        "suggested_queries": [
            {"table": "User_Expenses", "index": "UserIdInvoiceDateIndex"},
            {"table": "User_Invoice_Incomes", "index": "UserIdInvoiceDateIndex"},
            {"table": "Payroll_Slips"},
            {"table": "Bank_Reconciliations"},
        ],
    },

    "conciliacion": {
        "name": "Conciliacion Bancaria Inteligente",
        "guidance": """\
PLAYBOOK — CONCILIACION BANCARIA EXPLORATORIA:
You are an AI forensic accountant. The unreconciled items are the HARD cases that a simple
algorithm couldn't match. You must EXPLORE the data creatively to find matches.

STEP 1 — FETCH DATA:
  Query Bank_Reconciliations, User_Expenses, User_Invoice_Incomes (all by PK, no filters).

STEP 2 — EXPLORE (run_code): Understand the unreconciled landscape BEFORE trying to match.
  - Separate unreconciled bank txns: `[t for t in txns if t.get('status') != 'MATCHED']`
  - Separate unreconciled invoices: `[i for i in invoices if not i.get('reconciled')]`
    (reconciled=True means matched. Field MISSING = unreconciled. NEVER use reconciliationState.)
  - Print stats: how many of each? What amount ranges? What date ranges?
  - Look at the merchant/description field in bank txns — does it contain supplier names or CIFs?
  - Look at ai_enrichment field — it may have vendor_cif, payment_type, category.
  - List unique suppliers in unreconciled invoices and unique merchants in unreconciled txns.
  - Identify patterns: Are there txns that look like they aggregate multiple invoices?

STEP 3 — MATCH CREATIVELY (run_code): Try multiple strategies, from obvious to creative:
  a) EXACT amount match: abs(txn.amount) == invoice.total (within 0.01€ tolerance)
  b) CIF match: txn.ai_enrichment.vendor_cif == invoice.supplier_cif
  c) Name fuzzy match: supplier name appears in txn.description or txn.merchant
  d) Date proximity: bookingDate close to invoice_date or due_date or charge_date
  e) N-to-1 aggregation: sum of N invoices from same supplier == one bank txn
  f) 1-to-N split: one invoice paid in multiple bank transactions
  g) Amount with fees: txn.amount ≈ invoice.total ± small bank fee (1-5€)
  h) Partial payments: txn.amount is a percentage of invoice.total (check amount_paid field)

  Combine multiple signals into a confidence score. Be creative — these are the hard cases!

STEP 4 — GENERATE REPORT (run_code): Create Excel with:
  - Sheet "Matches Propuestos": matched pairs with confidence score, reasoning for each match
  - Sheet "Txns Sin Match": remaining unmatched bank transactions
  - Sheet "Facturas Sin Match": remaining unmatched invoices
  - Sheet "Resumen": stats, match distribution by confidence, insights discovered

CRITICAL DATA RULES:
- Bank txns: amount < 0 = outflow (expense payment), amount > 0 = inflow (income received)
- Invoice reconciled: True = already matched, MISSING = unreconciled. NEVER False.
- DO NOT use reconciliationState field (always says UNRECONCILED, even for reconciled items).
- DO NOT filter by amount_due or amount_paid for reconciliation status.""",
    },

    "reportes_financieros": {
        "name": "Pack Reporting Financiero",
        "guidance": """\
PLAYBOOK — REPORTES FINANCIEROS:
Build a financial reporting pack (P&L, KPIs).

1. QUERY User_Expenses via UserIdInvoiceDateIndex for the period.
2. QUERY User_Invoice_Incomes via UserIdInvoiceDateIndex.
3. QUERY Payroll_Slips for the period.
4. In run_code, build:
   - P&L: Revenue - COGS - OpEx - Payroll = Operating Profit
   - Group expenses by category/concept
   - Calculate KPIs: gross margin, operating margin, expense ratios
   - Month-over-month comparison if multi-month
5. Generate Excel with P&L sheet, KPIs sheet, category breakdown.""",
    },

    "deteccion_errores": {
        "name": "Deteccion de Errores Contables",
        "guidance": """\
PLAYBOOK — DETECCION ERRORES:
Find accounting errors and anomalies.

1. QUERY User_Expenses for the period.
2. In run_code, check for:
   - Duplicate invoices (same supplier_cif + invoice_number + total)
   - Missing fields (no category, no concept, no supplier_cif)
   - Unusual amounts (outliers > 3 std dev from mean per category)
   - Date inconsistencies (invoice_date > today, pnl_date != invoice month)
   - VAT errors: total != importe + vatTotalAmount - retencion
   - Credit notes without matching invoice
3. Rank findings by severity (blocking, warning, info).""",
    },

    "auditoria_iva": {
        "name": "Auditoria de IVA / Modelo 303",
        "guidance": """\
PLAYBOOK — AUDITORIA IVA:
Prepare VAT audit or Modelo 303 draft.

1. QUERY User_Expenses via UserIdInvoiceDateIndex for the quarter.
   Key fields: ivas[], vatTotalAmount, vatDeductibleAmount, vatOperationType, importe, total
2. QUERY User_Invoice_Incomes for the same quarter.
3. In run_code, compute:
   - IVA soportado deducible (by VAT rate: 4%, 10%, 21%)
   - IVA repercutido (by VAT rate)
   - Special operations: intracomunitarias, ISP, exentas
   - Liquidacion: repercutido - soportado deducible
   - Validate: sum(ivas[].amount) == vatTotalAmount for each invoice
4. Generate Excel with Modelo 303 structure.""",
    },

    "control_fraude": {
        "name": "Control de Fraude y Anomalias",
        "guidance": """\
PLAYBOOK — CONTROL FRAUDE:
Detect potential fraud or anomalies.

1. QUERY User_Expenses for the period.
2. QUERY Bank_Reconciliations.
3. In run_code, flag:
   - Round-number transactions (exact 1000, 5000, etc.)
   - Weekend/holiday transactions
   - Same amount to same supplier multiple times in short period
   - Suppliers with no CIF or temporary CIF
   - Bank transactions with no matching invoice
   - Unusually high amounts per category
4. Rank by risk score.""",
    },

    "analisis_gastos": {
        "name": "Analisis de Gastos Accionable",
        "guidance": """\
PLAYBOOK — ANALISIS GASTOS:
Deep analysis of spending patterns.

1. QUERY User_Expenses via UserIdInvoiceDateIndex (or by PK for full history).
2. In run_code:
   - Group by category → total, count, avg per invoice
   - Group by supplier → top 10 suppliers by spend
   - Monthly trend: monthly_totals() for each category
   - Identify: fastest growing categories, largest single invoices
   - Compare with Location_Budgets if available
3. Generate chart with category breakdown and trends.""",
    },

    "optimizacion_proveedores": {
        "name": "Optimizacion de Proveedores",
        "guidance": """\
PLAYBOOK — OPTIMIZACION PROVEEDORES:
Analyze supplier performance and find optimization opportunities.

1. QUERY User_Expenses — group by supplier_cif.
2. QUERY Provider_Products (LocationProductsIndex) — price history.
3. QUERY Providers — supplier master data.
4. In run_code:
   - Total spend per supplier
   - Price evolution per product (from Provider_Products)
   - Identify: price increases, single-source dependencies
   - Compare similar products across suppliers
5. Generate actionable recommendations.""",
    },

    "rentabilidad": {
        "name": "Analisis de Rentabilidad",
        "guidance": """\
PLAYBOOK — RENTABILIDAD:
Analyze profitability by client, category, or period.

1. QUERY User_Invoice_Incomes — revenue by client.
2. QUERY User_Expenses — costs by category.
3. QUERY Payroll_Slips — labor costs.
4. In run_code:
   - Revenue per client/category
   - Direct costs allocation
   - Gross margin calculation
   - Operating margin after payroll
   - Rank by profitability""",
    },

    "prediccion_cashflow": {
        "name": "Prediccion de Cash Flow",
        "guidance": """\
PLAYBOOK — PREDICCION CASHFLOW:
Build a 13-week cash flow forecast from bank data.

1. QUERY Bank_Reconciliations by PK — ALL transactions (amount, bookingDate, ai_enrichment).
2. In run_code:
   - Classify by ai_enrichment.payment_type and amount sign
   - Calculate weekly averages per category (last 12 weeks)
   - Project 13 weeks forward
   - Opening balance = last known bank balance
   - Weekly: inflows, outflows, net, cumulative balance
   - Flag weeks where balance goes negative
3. Generate Excel with forecast table + line chart.

IMPORTANT: Bank_Reconciliations is the ONLY source of truth for real cash movements.
Do NOT use invoice data for cash flow — use actual bank transactions.""",
    },

    "simulacion": {
        "name": "Simulacion de Decisiones",
        "guidance": """\
PLAYBOOK — SIMULACION:
Model what-if scenarios based on current financial data.

1. Query the relevant data (expenses, income, bank) as baseline.
2. In run_code, apply the user's hypothetical changes:
   - Revenue change: multiply income by factor
   - Cost change: adjust expense categories
   - New hire: add payroll cost
   - Price change: recalculate margins
3. Compare baseline vs scenario with delta analysis.""",
    },

    "explicacion_humana": {
        "name": "Explicacion Tipo Humano",
        "guidance": """\
PLAYBOOK — EXPLICACION:
The user wants an explanation, not data analysis.
Answer directly without querying the database unless the explanation
requires specific data from their business.
Keep it clear, concise, and in the user's language.""",
    },
}


def get_playbook_guidance(task_type: str) -> str:
    """Get the playbook guidance text for a task type."""
    playbook = PLAYBOOKS.get(task_type)
    if not playbook:
        return ""
    return playbook.get("guidance", "")


def get_playbook_name(task_type: str) -> str:
    """Get the human-readable name for a task type."""
    playbook = PLAYBOOKS.get(task_type)
    return playbook.get("name", task_type) if playbook else task_type
