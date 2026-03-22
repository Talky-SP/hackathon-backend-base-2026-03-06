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
    "contabilizar_facturas",
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
- contabilizar_facturas: Process uploaded invoices/documents, create journal entries, register invoices, OCR invoices
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
        "contabilizar_facturas": [
            "contabilizar factura", "contabiliza estas factura", "registrar factura",
            "asientos contable", "hacer la conta", "haz la conta",
            "contabilizar esto", "registra estas factura", "procesar factura",
            "dar de alta factura", "meter factura", "subir factura",
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
PLAYBOOK — CIERRE CONTABLE (WORLD-CLASS FINANCIAL CONTROLLER):
You are the best financial controller in the world. The monthly accounting close is your
signature deliverable — thorough, precise, and actionable. You don't just check boxes,
you UNDERSTAND the financial reality and surface insights others would miss.

STEP 1 — FETCH DATA (parallel queries):
  - User_Expenses via UserIdInvoiceDateIndex for the target month
  - User_Invoice_Incomes via UserIdInvoiceDateIndex for the target month
  - Bank_Reconciliations by PK (ALL — you'll filter by month in code)
  - Payroll_Slips by PK (you'll filter by month in code)

STEP 2 — DEEP EXPLORATION (first run_code):
  Filter bank transactions for the target month (by bookingDate).
  Then EXPLORE the data like a forensic accountant:

  CASH REALITY (from bank — the ground truth):
  - Total inflows and outflows for the month
  - Classify bank movements: supplier payments, payroll, taxes, fees, income received
  - Use ai_enrichment and description/merchant fields for classification
  - What's the net cash position change for this month?

  INVOICES vs BANK (cross-reference):
  - Match expense invoices to bank outflows. How many matched vs unmatched?
  - Match income invoices to bank inflows. Any income received without an invoice?
  - Identify bank payments without any matching invoice (potential missing invoices)
  - Identify invoices without bank payment (pending / unpaid)

  EXPENSE DEEP DIVE:
  - Group by category: what are the main cost drivers?
  - Group by supplier: who are the top suppliers this month?
  - Any unusual amounts? First-time suppliers? Temporary CIFs?
  - Compare with typical monthly pattern — anything anomalous?

  PAYROLL DETECTION:
  - Even if Payroll_Slips table is empty, detect payroll in bank transactions
  - Look for: "nomina", "SS", "seguridad social", "IRPF", "mod.111" in descriptions
  - Estimate total payroll cost from bank data

  VAT POSITION:
  - IVA soportado: sum from expense invoices (ivas[] or vatTotalAmount)
  - IVA repercutido: sum from income invoices
  - Net position: repercutido - soportado (positive = owe AEAT, negative = claim refund)

  ANOMALY SCAN — be creative:
  - Any duplicate-looking invoices?
  - Bank txns on weekends? Round numbers? Unusual merchants?
  - Invoices with dates outside the target month?

  Print ALL findings. DO NOT set `result`. Continue to STEP 3.

STEP 3 — GENERATE CLOSING REPORT + TODO LIST (second run_code):
  Create a professional Excel with:
  - Sheet "Resumen Cierre": executive summary with close status and KPIs
  - Sheet "P&L Mensual": income - expenses = result, by category
  - Sheet "Gastos Detalle": all expense invoices with category, supplier, amount, status
  - Sheet "Movimientos Banco": bank transactions for the month, classified
  - Sheet "Conciliación": reconciliation status (matched vs pending, both sides)
  - Sheet "IVA": VAT summary by rate (4%, 10%, 21%)
  - Sheet "Tareas Pendientes": the TODO list for closing (see below)

  TODO LIST FORMAT (critical — frontend will render this):
  The `result` must include a "todo" field with structured tasks:
  ```
  result = {
      "answer": "Executive summary text...",
      "chart": {"type": "bar", ...},  # expenses by category
      "sources": [...only problematic items...],
      "todo": [
          {
              "id": "1",
              "priority": "critical",  # critical / high / medium / low
              "category": "conciliacion",  # conciliacion / facturacion / nominas / iva / revision
              "title": "Conciliar 28 transacciones bancarias pendientes",
              "description": "Hay 28 transacciones por 1,045,173.95€ sin conciliar, incluyendo un depósito a plazo de 1M€",
              "amount": 1045173.95,
              "items_count": 28,
              "blocking": true  # true = blocks the close
          },
          {
              "id": "2",
              "priority": "critical",
              "category": "facturacion",
              "title": "Registrar facturas de ingreso de febrero",
              "description": "No hay facturas de ingreso pero sí ingresos bancarios de 4,186.40€",
              "amount": 4186.40,
              "blocking": true
          },
          {
              "id": "3",
              "priority": "high",
              "category": "nominas",
              "title": "Registrar nóminas de febrero",
              "description": "Se detectan pagos de nómina en banco (Tadros 6,973.80€, López-Abente 4,674.56€) pero no hay registros en Payroll_Slips",
              "amount": 11648.36,
              "blocking": false
          },
          ...more tasks...
      ]
  }
  ```

  CLOSE STATUS:
  - "CERRADO": no blocking tasks, all reconciled, all invoices accounted for
  - "BLOQUEADO": has blocking tasks that prevent the close
  - "PENDIENTE": minor non-blocking issues to review

  The TODO list should be ordered by priority (critical first) and include EVERY
  actionable item found during exploration. Be specific — include amounts, counts,
  supplier names. Each task should be something a person can act on.

IMPORTANT:
- Bank is the ground truth. Filter by bookingDate for the target month.
- DO NOT use pandas. Use basic Python.
- Complete ALL steps. Only set `result` in STEP 3 with Excel.
- Keep sources concise: only pending/problematic items, NOT all data.""",
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
  DO NOT set `result` here — this is just exploration. You MUST continue to STEP 3.

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

MANDATORY KEYS FOR FRONTEND INTEGRATION:
  Every row in "Matches Propuestos" MUST include these DynamoDB keys (needed to execute real reconciliations):
  - invoice_categoryDate: the `categoryDate` field from User_Expenses (SK, format CATEGORY#YYYY-MM-DD#UUID)
  - txn_SK: the `SK` field from Bank_Reconciliations (sort key of the bank transaction)
  - invoice_userId: the `userId` (PK) of the invoice
  - txn_userId: the `userId` (PK) of the bank transaction
  These columns can be narrow/hidden but MUST be present. Without them the frontend cannot reconcile.

CRITICAL DATA RULES:
- Bank txns: amount < 0 = outflow (expense payment), amount > 0 = inflow (income received)
- Invoice reconciled: True = already matched, MISSING = unreconciled. NEVER False.
- DO NOT use reconciliationState field (always says UNRECONCILED, even for reconciled items).
- DO NOT filter by amount_due or amount_paid for reconciliation status.

CRITICAL: You MUST complete ALL 4 steps. Do NOT stop after exploration or matching.
Only set `result` in STEP 4 with the final summary. The user expects an Excel report.""",
    },

    "reportes_financieros": {
        "name": "Pack Reporting Financiero",
        "guidance": """\
PLAYBOOK — REPORTES FINANCIEROS (P&L):
Build a financial reporting pack using bank transactions as the PRIMARY source of truth,
enriched with invoice data for categorization and detail.

STEP 1 — FETCH DATA (parallel queries):
  - Bank_Reconciliations by PK — ALL bank transactions (this is the source of truth for real cash flow)
  - User_Expenses via UserIdInvoiceDateIndex for the period (for expense categorization + pending invoices)
  - User_Invoice_Incomes via UserIdInvoiceDateIndex for the period (for income categorization + pending invoices)
  - Payroll_Slips for the period (for payroll detail)

STEP 2 — BUILD P&L FROM BANK TRANSACTIONS (run_code):
  Bank transactions are the SINGLE SOURCE OF TRUTH for actual cash movements:
  - amount > 0 = INCOME (cash received)
  - amount < 0 = EXPENSE (cash paid out)

  Use ai_enrichment field for categorization:
  - ai_enrichment.category, ai_enrichment.payment_type, ai_enrichment.vendor_cif

  Group by month, then by category. Calculate:
  - Total Income (positive amounts)
  - Total Expenses (negative amounts, use abs())
  - Net Result = Income - Expenses

STEP 3 — ENRICH WITH INVOICE DATA (run_code):
  Use invoices to ADD DETAIL, not as a separate data source:
  - Match bank txns to invoices (via reconciled status or amount/date matching)
  - For matched txns: use invoice category, concept, supplier name for better labeling
  - For unmatched bank txns: use ai_enrichment for categorization
  - Identify PENDING invoices (reconciled != True): these are obligations not yet reflected in bank

  AVOID DUPLICATION:
  - NEVER sum bank transactions AND their matched invoices separately
  - Bank txns = what actually happened (cash basis)
  - Invoices = what should happen (accrual basis)
  - If building accrual P&L: use invoices for the period, flag which are paid vs pending
  - If building cash P&L: use bank transactions, enriched with invoice categories

STEP 4 — GENERATE EXCEL (run_code):
  - Sheet "P&L": Monthly columns, rows by category. Show Income, Expenses by category, Net Result
  - Sheet "Detalle Ingresos": Bank inflows with matched invoice detail where available
  - Sheet "Detalle Gastos": Bank outflows with matched invoice detail where available
  - Sheet "Pendiente": Invoices not yet paid (reconciled != True) — future obligations
  - Sheet "KPIs": Gross margin, operating margin, expense ratios, MoM comparison
  Color-code and format professionally. You MUST generate an Excel file.

IMPORTANT: The P&L should reflect REAL cash movements from bank transactions.
Invoices add context (categories, suppliers) but are NOT the primary numbers.""",
    },

    "deteccion_errores": {
        "name": "Deteccion de Errores Contables",
        "guidance": """\
PLAYBOOK — DETECCION DE ERRORES EXPLORATORIA:
You are an AI auditor. Find REAL accounting errors — not false positives.
Quality over quantity: 5 real errors are worth more than 200 false alarms.

STEP 1 — FETCH DATA (parallel queries):
  - User_Expenses by PK — ALL expense invoices
  - Bank_Reconciliations by PK — ALL bank transactions (for cross-reference)

STEP 2 — EXPLORE THE DATA (first run_code):
  UNDERSTAND the data before checking errors:
  - Count invoices, date range, document types (invoice vs credit_note)
  - Count reconciled (reconciled=True) vs unreconciled (field missing)
  - Amount distributions per category, supplier count, temporary CIFs
  - Print summary stats. DO NOT import pandas (use basic Python).
  DO NOT set `result` here. You MUST continue to STEP 3.

STEP 3 — ERROR DETECTION (second run_code):
  Check for REAL errors. Be precise — avoid false positives:

  a) DUPLICATES (BLOQUEANTE if confirmed):
     - EXACT duplicate: same supplier_cif + invoice_number → definite error
     - PROBABLE duplicate: same supplier_cif + same total + dates within 7 days → suspicious
     - Do NOT flag invoices with different invoice_numbers as duplicates just because same amount

  b) VAT / MATH ERRORS (BLOQUEANTE):
     - total != importe + sum(ivas[].amount) - retencion (with 0.02€ tolerance for rounding)
     - vatTotalAmount != sum(ivas[].amount)
     - Only flag if the difference is > 0.02€ (rounding tolerance)
     - Invoices with ivas=[] are OK (foreign suppliers, exempt operations)

  c) MISSING DATA (ADVERTENCIA, not BLOQUEANTE):
     - Temporary CIF (starts with TEMP-) → supplier not properly registered
     - Missing invoice_number, missing category
     - Missing due_date (only flag if amount_due > 0)

  d) DATE ANOMALIES (ADVERTENCIA):
     - invoice_date in the future (> today)
     - due_date before invoice_date
     - charge_date more than 180 days from invoice_date

  e) CROSS-REFERENCE INSIGHTS (INFORMATIVO, not BLOQUEANTE):
     - Bank payments > 1000€ without any matching invoice amount (ghost payments)
     - Unreconciled invoices older than 90 days (possibly forgotten)
     - DO NOT flag "reconciled invoice without bank match" as error — reconciliation
       may have been done via N-to-1 matching, partial payments, or manual process.
       The reconciled=True flag IS the source of truth. Trust it.

  f) OUTLIERS (INFORMATIVO):
     - Amounts > 3x the average for their category
     - Credit notes without a matching original invoice (by supplier + similar amount)

  IMPORTANT FALSE POSITIVE RULES:
  - reconciled=True means CORRECTLY reconciled. Do NOT flag these as errors.
  - Invoices with ivas=[] or no vatTotalAmount are often foreign/exempt — NOT errors.
  - Different invoices to same supplier on same day are NORMAL for recurring services.
  - amount_paid matching total with reconciled=True is CORRECT, not suspicious.
  DO NOT set `result` here. You MUST continue to STEP 4.

STEP 4 — GENERATE EXCEL REPORT (third run_code):
  Create Excel with:
  - Sheet "Resumen": total invoices, real errors found by severity, key findings
  - Sheet "Errores": all issues sorted by severity, with columns:
    severity, error_type, invoice_number, supplier, amount, description, categoryDate
  - Sheet "Detalle Facturas": all invoices with key fields for reference
  ALWAYS include categoryDate (SK) for each invoice for frontend integration.
  Color-code: red=BLOQUEANTE, yellow=ADVERTENCIA, blue=INFORMATIVO.

  Set `result` here with:
  - "answer": summary text with error counts and key findings
  - "chart": bar chart of errors by type
  - "sources": list of ONLY the problematic invoices (NOT all 230)

Severity guide:
  - BLOQUEANTE: Math errors (IVA mismatch > 0.02€), exact duplicates
  - ADVERTENCIA: Temporary CIFs, missing fields, date anomalies
  - INFORMATIVO: Outliers, old unreconciled invoices, ghost bank payments

CRITICAL: Complete ALL 4 steps. Only set `result` in STEP 4 with Excel.
DO NOT use pandas — use basic Python (collections, datetime). It's more reliable.""",
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
PLAYBOOK — PREDICCION DE CASHFLOW (WORLD-CLASS TREASURY ANALYSIS):
You are the best financial analyst and data scientist in the world. Your cash flow forecasts
are famous for being accurate, insightful, and actionable. You don't just average numbers —
you UNDERSTAND the business, detect patterns, and build intelligent projections.

STEP 1 — FETCH DATA (parallel queries):
  - Bank_Reconciliations by PK — ALL bank transactions (the ground truth)
  - User_Expenses by PK — ALL invoices (pending = known future outflows)

STEP 2 — DEEP FINANCIAL EXPLORATION (first run_code):
  Think like a CFO studying their treasury. EXPLORE creatively:

  CASH FLOW ANATOMY:
  - Group all transactions by week (bookingDate). Build a complete weekly time series.
  - Separate: inflows (amount > 0), outflows (amount < 0), net per week.
  - Calculate running balance week by week. What's the current position?

  PATTERN RECOGNITION:
  - Use ai_enrichment (payment_type, category, vendor_cif) to classify each transaction.
  - Identify RECURRING payments: same merchant + similar amount repeating monthly.
    (rent, salaries, social security, insurance, software, subscriptions)
  - Identify VARIABLE flows: supplier payments that vary in timing and amount.
  - Identify ONE-OFF transactions: unusually large amounts that won't repeat.
    (capital injections, one-time purchases, extraordinary items)
  - Look at DESCRIPTIONS/MERCHANTS for clues about what each payment is.

  TREND & SEASONALITY:
  - Are inflows growing, declining, or stable? Calculate month-over-month growth.
  - Is there weekly seasonality? (e.g., more outflows on Mondays, inflows on Fridays)
  - Is there monthly seasonality? (e.g., rent on day 1, salaries on day 28)
  - What's the BURN RATE? (average weekly net cash consumption/generation)
  - What's the RUNWAY? (current balance / average weekly burn = weeks until zero)

  PENDING OBLIGATIONS:
  - List all unreconciled invoices (reconciled != True) — these WILL need to be paid.
  - Group by due_date: how much is due this week, next week, in 30/60/90 days?
  - Identify largest upcoming payments (top 5 pending invoices by amount).

  Print ALL insights. This exploration drives the quality of your forecast.
  DO NOT set `result`. Continue to STEP 3.

STEP 3 — INTELLIGENT FORECAST MODEL (second run_code):
  Build a SOPHISTICATED forecast using everything you learned:

  a) BASELINE — RECURRING CASHFLOWS (high confidence, 90%):
     For each identified recurring payment/income:
     - Project at its historical frequency and average amount
     - Apply any detected trend (growing/shrinking)
     - Place in the correct week based on historical timing pattern

  b) VARIABLE FLOWS — WEIGHTED PROJECTION (medium confidence, 70%):
     - Use EXPONENTIALLY WEIGHTED moving average (recent weeks count 2x)
     - Apply detected monthly trend as growth/decay factor
     - For supplier payments: cross-reference with pending invoices for better timing

  c) SCHEDULED PAYMENTS — FROM PENDING INVOICES (high confidence):
     - Invoices with due_date → place as outflow in that specific week
     - Invoices without due_date → estimate using supplier's historical payment lag
     - Large one-time pending invoices: flag separately with exact week

  d) SCENARIO ANALYSIS (this is what makes you world-class):
     Build THREE scenarios:
     - OPTIMISTA: inflows +15%, outflows -5%, all pending delayed 2 weeks
     - BASE (most likely): your best estimate from a/b/c above
     - PESIMISTA: inflows -15%, outflows +10%, all pending paid immediately
     Calculate balance trajectory for each scenario.

  e) RISK INDICATORS:
     - Minimum projected balance across all scenarios
     - Week where cash is tightest (lowest balance)
     - Probability of negative balance (if pessimistic goes negative)
     - Recommended safety buffer
     - Days of cash runway at current burn rate

  DO NOT set `result`. Continue to STEP 4.

STEP 4 — GENERATE WORLD-CLASS REPORT (third run_code):
  Create a professional Excel:
  - Sheet "Forecast 13 Semanas": week-by-week with 3 scenarios (optimista/base/pesimista)
    Columns: Week, Inflows, Outflows, Net, Balance (Base), Balance (Optimista), Balance (Pesimista)
  - Sheet "Analisis Categorias": flows breakdown by category with historical avg + projected
  - Sheet "Pagos Pendientes": upcoming invoices sorted by due_date with supplier and amount
  - Sheet "Historico Semanal": actual weekly data that fed the model
  - Sheet "Resumen Ejecutivo": key metrics, runway, alerts, recommendations
  Color-code risk weeks (red if any scenario goes negative, yellow if tight).

  CHART FORMAT for `result`:
  The chart MUST show HISTORICAL + FORECAST with different visual treatment:
  - Use TWO datasets in the chart:
    Dataset 1: "Histórico" — historical weekly balances (last 8-12 weeks of real data)
    Dataset 2: "Proyección Base" — forecasted 13 weeks
  - Historical data labels: all the past weeks (S-12, S-11, ..., S-1, Actual)
  - Forecast data labels: future weeks (S+1, S+2, ..., S+13)
  - The two series OVERLAP at "Actual" (current week) to create a continuous line
  - This way the frontend renders past in one color and future in another.

  Example chart structure:
  ```
  result = {
      "answer": "Summary text...",
      "chart": {
          "type": "line",
          "title": "Proyección de Tesorería - 13 Semanas",
          "labels": ["S-8","S-7","S-6","S-5","S-4","S-3","S-2","S-1","Actual","S+1","S+2",...,"S+13"],
          "datasets": [
              {"label": "Histórico", "data": [real_balances..., current, null, null, ...]},
              {"label": "Proyección Base", "data": [null, null, ..., current, projected_1, projected_2, ...]},
              {"label": "Escenario Pesimista", "data": [null,..., current, pessimistic_1, ...]},
          ]
      },
      "sources": [{"metric": "Saldo Actual", "value": X}, {"metric": "Saldo Proyectado S+13", ...}]
  }
  ```
  Use null values so each dataset only shows its segment. They connect at "Actual".

IMPORTANT:
- Bank_Reconciliations = ground truth. Pending invoices = known future outflows.
- DO NOT use pandas. Use basic Python (collections, datetime, statistics).
- Complete ALL 4 steps. Only set `result` in STEP 4 with Excel.
- Your analysis should be so good that a CFO would trust it for real treasury decisions.""",
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

    "contabilizar_facturas": {
        "name": "Contabilizar Facturas / Procesar Documentos",
        "guidance": """\
PLAYBOOK — CONTABILIZAR FACTURAS / PROCESAR DOCUMENTOS EN LOTE:
You are the best accountant in the world. The user has uploaded documents (invoices,
receipts, payslips, bank statements) and wants you to process them.

STRATEGY:
- If 1-4 documents: analyze them directly from the multimodal attachments.
- If 5+ documents: use dispatch_subagents to process them in parallel batches.
  Split into batches of 3-5 documents per subagent.

STEP 1 — DISPATCH SUBAGENTS (for 5+ docs):
  Call dispatch_subagents with subtasks. Each subtask gets 3-5 document paths and an objective:
  "Extract all financial data from these invoices: supplier name, CIF/NIF, invoice number,
   date, line items, base amount, VAT rate, VAT amount, total. Cross-reference with
   User_Expenses in DynamoDB to check if already registered."

  The subagent objective should match the user's request:
  - "Contabilizar": extract data + match with DB + flag new/existing
  - "Auditar": extract data + validate amounts + check for errors
  - "Registrar": extract data + prepare for entry

STEP 2 — CONSOLIDATE (run_code after dispatch completes):
  Receive all subagent results. Merge into a single dataset:
  - Deduplicate (same invoice number / CIF / date / amount)
  - Cross-reference with DB records (query User_Expenses for the date range)
  - Classify: already_registered, new, duplicate, error

STEP 3 — GENERATE JOURNAL ENTRIES (run_code):
  For each new invoice, generate accounting journal entries:
  ```
  result_entries = []
  for inv in new_invoices:
      result_entries.append({
          "date": inv["date"],
          "description": f"Fra. {inv['invoice_number']} - {inv['supplier']}",
          "entries": [
              {"account": "600", "concept": inv["concept"], "debit": inv["base_amount"], "credit": 0},
              {"account": "472", "concept": f"IVA soportado {inv['vat_rate']}%", "debit": inv["vat_amount"], "credit": 0},
              {"account": "410", "concept": inv["supplier"], "debit": 0, "credit": inv["total"]},
          ]
      })
  ```

STEP 4 — GENERATE EXCEL (run_code):
  Create comprehensive Excel with sheets:
  - "Resumen": summary stats (total invoices, total amount, new vs existing, errors)
  - "Facturas Procesadas": all extracted data in table format
  - "Asientos Contables": journal entries ready for import
  - "Ya Registradas": invoices found in DB (no action needed)
  - "Errores": documents that couldn't be processed or have issues

  Set result with structured summary:
  ```python
  result = {
      "answer": "Procesadas 50 facturas. 35 nuevas (42,300.00 EUR), 12 ya registradas, 3 con errores.",
      "chart": {
          "type": "pie",
          "title": "Estado de facturas procesadas",
          "labels": ["Nuevas", "Ya registradas", "Errores"],
          "datasets": [{"label": "Facturas", "data": [35, 12, 3]}]
      },
      "sources": [...problematic items only...],
  }
  ```

IMPORTANT:
- Each subagent has FULL access: vision (read documents), DynamoDB (cross-reference), code (process).
- Subagents run in PARALLEL — 10 subagents processing 5 docs each = 50 docs in ~30 seconds.
- Cost per subagent: ~$0.30-0.50. Total for 50 docs: ~$3-5.
- DO NOT use pandas. Use basic Python.
- Document paths are in the user message (saved_path field in attachments).
- If no documents attached, ask the user to upload them.""",
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
