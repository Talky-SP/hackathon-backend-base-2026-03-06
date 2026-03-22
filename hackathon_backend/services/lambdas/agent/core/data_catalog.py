"""
Semantic Data Catalog — structured description of all DynamoDB tables
available to the agent.

Replaces the free-text _SCHEMA_BLOCK with typed metadata that can be
used for:
  1. System prompt generation (get_schema_prompt)
  2. Dataset card field selection
  3. Field-type awareness in helpers
"""
from __future__ import annotations

TABLE_CATALOG: dict[str, dict] = {
    # ---------------------------------------------------------------
    # Financial Core
    # ---------------------------------------------------------------
    "User_Expenses": {
        "description": "Facturas de gasto (expense invoices). ~50/mes por location.",
        "pk": "userId",
        "pk_note": "actually locationId (legacy naming)",
        "sk": "categoryDate",
        "sk_format": "YYYY-MM-DD#UUID",
        "key_fields": {
            "total": "number — importe total con IVA (EUR)",
            "importe": "number — base imponible sin IVA",
            "ivas": "list — [{rate, base_imponible, amount}]",
            "retencion": "number — retencion IRPF",
            "vatTotalAmount": "number — IVA total",
            "supplier": "string — nombre proveedor",
            "supplier_cif": "string — CIF/NIF proveedor",
            "invoice_number": "string",
            "invoice_date": "string — YYYY-MM-DD",
            "due_date": "string — YYYY-MM-DD",
            "pnl_date": "string — YYYY-MM-DD (fecha P&L, OFTEN NULL — prefer invoice_date for date queries)",
            "charge_date": "string — YYYY-MM-DD",
            "category": "string — categoria contable",
            "concept": "string — concepto contable",
            "reconciled": "bool — True=conciliada, MISSING=no conciliada (NEVER False). THIS is the reliable reconciliation field.",
            "reconciliationState": "string — UNRELIABLE (always UNRECONCILED). DO NOT use for filtering. Use 'reconciled' field instead.",
            "documentKind": "string — invoice|credit_note",
            "amount_due": "number",
            "amount_paid": "number",
        },
        "gsis": {
            "UserIdInvoiceDateIndex": {"pk": "userId", "sk": "invoice_date", "use": "Rango de fechas factura"},
            "UserIdSupplierCifIndex": {"pk": "userId", "sk": "supplier_cif", "use": "Filtrar por proveedor"},
            "UserIdPnlDateIndex": {"pk": "userId", "sk": "pnl_date", "use": "Gastos por rango P&L (WARNING: pnl_date often NULL — prefer UserIdInvoiceDateIndex)"},
            "UserByReconStateDate": {"pk": "userId", "sk": "recon_state_date", "use": "Estado conciliacion+fecha (R#date/U#date)"},
            "UserSupplierDateIndex": {"pk": "userSupplierKey ({userId}#{cif})", "sk": "charge_date", "use": "Pagos proveedor por fecha"},
        },
        "numeric_fields": ["total", "importe", "vatTotalAmount", "retencion", "amount_due", "amount_paid"],
        "date_fields": ["invoice_date", "due_date", "pnl_date", "charge_date"],
        "group_fields": ["category", "concept", "supplier", "supplier_cif", "documentKind"],
    },

    "User_Invoice_Incomes": {
        "description": "Facturas de ingreso (income invoices). Misma estructura que User_Expenses.",
        "pk": "userId",
        "sk": "categoryDate",
        "sk_format": "YYYY-MM-DD#UUID",
        "key_fields": {
            "total": "number — importe total con IVA",
            "importe": "number — base imponible",
            "ivas": "list",
            "retencion": "number",
            "vatTotalAmount": "number",
            "client_name": "string",
            "client_cif": "string",
            "invoice_number": "string",
            "invoice_date": "string — YYYY-MM-DD",
            "due_date": "string — YYYY-MM-DD",
            "category": "string",
            "concept": "string",
            "reconciled": "bool",
            "documentKind": "string",
            "amount_due": "number",
            "amount_paid": "number",
        },
        "gsis": {
            "UserIdInvoiceDateIndex": {"pk": "userId", "sk": "invoice_date", "use": "Rango fechas"},
            "UserIdClientCifIndex": {"pk": "userId", "sk": "client_cif", "use": "Filtrar por cliente"},
            "UserByReconStateDate": {"pk": "userId", "sk": "recon_state_date", "use": "Estado conciliacion"},
        },
        "numeric_fields": ["total", "importe", "vatTotalAmount", "retencion", "amount_due", "amount_paid"],
        "date_fields": ["invoice_date", "due_date", "pnl_date", "charge_date"],
        "group_fields": ["category", "concept", "client_name", "client_cif", "documentKind"],
    },

    "Bank_Reconciliations": {
        "description": "Movimientos bancarios + estado de conciliacion. PK=locationId, SK=MTXN#bookingDate#txnId.",
        "pk": "locationId",
        "sk": "SK",
        "sk_format": "MTXN#{bookingDate}#{transactionId}",
        "key_fields": {
            "amount": "number — negativo=gasto, positivo=ingreso",
            "bookingDate": "string — YYYY-MM-DD",
            "description": "string — descripcion banco",
            "merchant": "string — nombre comercio",
            "balance": "number — saldo tras movimiento",
            "status": "string — PENDING|MATCHED|UNMATCHED",
            "reconciled": "bool — true si conciliado (MISSING en no conciliados, no false)",
            "matched_expense_id": "string — categoryDate factura gasto conciliada",
            "matched_invoice_id": "string — categoryDate factura ingreso conciliada",
            "matched_payroll_id": "string — categoryDate nomina conciliada",
            "match_type": "string — 1-1|1-N|N-1|N-M",
            "vendor_cif": "string",
            "customer_cif": "string",
            "ai_enrichment": "map — {payment_type, vendor_name, vendor_cif, account_type}",
            "transactionId": "string",
        },
        "gsis": {
            "LocationByStatusDate": {"pk": "locationId", "sk": "status_date", "use": "Filtrar por status+fecha (solo MATCHED indexados)"},
            "ByVendorCif": {"pk": "vendor_cif", "sk": None, "use": "Buscar por CIF proveedor"},
            "ByCustomerCif": {"pk": "customer_cif", "sk": None, "use": "Buscar por CIF cliente"},
            "LocationByPayrollDate": {"pk": "locationId", "sk": "payroll_date", "use": "Conciliacion nominas"},
        },
        "data_notes": (
            "UNRECONCILED txns: status=PENDING, campo 'reconciled' NO EXISTE (no es false). "
            "No usar LocationByStatusDate GSI para no conciliados — no estan indexados. "
            "Para obtener todos: query PK=locationId sin GSI, luego filtrar status en codigo."
        ),
        "numeric_fields": ["amount", "balance"],
        "date_fields": ["bookingDate"],
        "group_fields": ["status", "merchant", "match_type"],
    },

    "Payroll_Slips": {
        "description": "Nominas. PK=locationId, SK=YYYY-MM-DD#employee_nif.",
        "pk": "locationId",
        "sk": "categoryDate",
        "sk_format": "YYYY-MM-DD#employee_nif",
        "key_fields": {
            "employee_nif": "string",
            "org_cif": "string — CIF empresa",
            "payroll_date": "string — YYYY-MM-DD",
            "payroll_info": "map — {gross_amount, net_amount, company_ss_contribution, irpf_amount, irpf_percentage, employee_ss_contribution, company_total_cost}",
        },
        "gsis": {
            "OrgCifPeriodIndex": {"pk": "org_cif", "sk": "period_key (PERIOD#{yyyy-mm}#EMP#{nif})", "use": "Nominas por periodo"},
            "LocationEmployeeDateIndex": {"pk": "locationId", "sk": "employee_date_key (EMP#{nif}#DATE#{date})", "use": "Nominas por empleado+fecha"},
        },
        "numeric_fields": [],
        "date_fields": ["payroll_date"],
        "group_fields": ["employee_nif", "org_cif"],
    },

    "Delivery_Notes": {
        "description": "Albaranes de entrega.",
        "pk": "userId",
        "sk": "categoryDate",
        "key_fields": {
            "total": "number",
            "supplier": "string",
            "supplier_cif": "string",
            "invoice_date": "string — YYYY-MM-DD",
            "delivery_note_number": "string",
        },
        "gsis": {
            "DeliveryNoteNumberIndex": {"pk": "delivery_note_number", "sk": None, "use": "Buscar por numero albaran"},
            "ProviderCIFReconciledIndex": {"pk": "supplier_cif", "sk": "reconciled_date", "use": "Albaranes por proveedor"},
        },
        "numeric_fields": ["total"],
        "date_fields": ["invoice_date"],
        "group_fields": ["supplier", "supplier_cif"],
    },

    "Providers": {
        "description": "Maestro proveedores. PK=locationId, SK=cif.",
        "pk": "locationId",
        "sk": "cif",
        "key_fields": {
            "nombre": "string — nombre proveedor",
            "cif": "string — CIF/NIF",
            "trade_name": "string — nombre comercial",
            "facturas": "list — lista categoryDates de facturas",
            "provincia": "string",
            "emails": "list",
            "phones": "list",
            "website": "string",
        },
        "gsis": {},
        "numeric_fields": [],
        "date_fields": [],
        "group_fields": ["provincia"],
    },

    "Customers": {
        "description": "Maestro clientes. PK=locationId, SK=cif.",
        "pk": "locationId",
        "sk": "cif",
        "key_fields": {
            "nombre": "string",
            "cif": "string",
            "facturas": "list",
        },
        "gsis": {},
        "numeric_fields": [],
        "date_fields": [],
        "group_fields": [],
    },

    "Employees": {
        "description": "Empleados. PK=locationId, SK=employeeNif.",
        "pk": "locationId",
        "sk": "employeeNif",
        "key_fields": {
            "employeeNif": "string",
            "name": "string",
            "position": "string",
            "socialSecurityNumber": "string",
        },
        "gsis": {
            "LocationStatusIndex": {"pk": "location_status_key ({locationId}#{status})", "sk": "lastPayrollDate", "use": "Empleados activos/inactivos"},
        },
        "numeric_fields": [],
        "date_fields": [],
        "group_fields": ["position"],
    },

    "Daily_Stats": {
        "description": "Estadisticas diarias POS (TPV). PK=locationId, SK=dayKey.",
        "pk": "locationId",
        "sk": "dayKey",
        "key_fields": {"dayKey": "string — YYYY-MM-DD"},
        "gsis": {},
        "numeric_fields": [],
        "date_fields": ["dayKey"],
        "group_fields": [],
    },

    "Monthly_Stats": {
        "description": "Estadisticas mensuales POS. PK=locationId, SK=monthKey.",
        "pk": "locationId",
        "sk": "monthKey",
        "key_fields": {"monthKey": "string — YYYY-MM"},
        "gsis": {},
        "numeric_fields": [],
        "date_fields": ["monthKey"],
        "group_fields": [],
    },

    # ---------------------------------------------------------------
    # New tables (Phase 1 additions)
    # ---------------------------------------------------------------
    "Provider_Products": {
        "description": "Productos por proveedor con historial de precios. PK=providerId (locationId#cif), SK=productId.",
        "pk": "providerId",
        "pk_note": "Composite: locationId#cif",
        "sk": "productId",
        "key_fields": {
            "productName": "string",
            "category": "string",
            "locationId": "string",
            "providerCif": "string",
            "providerName": "string",
            "last_price": "number — ultimo precio conocido",
            "price_history": "list — historial de precios",
        },
        "gsis": {
            "LocationProductsIndex": {"pk": "locationId", "sk": "productId", "use": "Productos de un location"},
            "ProviderCifIndex": {"pk": "providerCif", "sk": None, "use": "Productos por CIF proveedor"},
            "CategoryIndex": {"pk": "category", "sk": None, "use": "Productos por categoria"},
        },
        "numeric_fields": ["last_price"],
        "date_fields": [],
        "group_fields": ["category", "providerName", "providerCif"],
    },

    "Location_Custom_PnL": {
        "description": "Entradas manuales P&L (ajustes contables). PK=locationId, SK=pnl_date_entry_id.",
        "pk": "locationId",
        "sk": "pnl_date_entry_id",
        "key_fields": {
            "pnl_date": "string — YYYY-MM-DD",
            "amount": "number",
            "kind": "string — INCOME|EXPENSE|ADJUSTMENT",
            "description": "string",
            "category": "string",
        },
        "gsis": {
            "LocationKindDateIndex": {"pk": "locationKindKey", "sk": "pnl_date", "use": "Entradas por tipo+fecha"},
        },
        "numeric_fields": ["amount"],
        "date_fields": ["pnl_date"],
        "group_fields": ["kind", "category"],
    },

    "Location_Budgets": {
        "description": "Presupuestos por categoria y fecha. PK=locationId, SK=dateCategoryKey.",
        "pk": "locationId",
        "sk": "dateCategoryKey",
        "key_fields": {
            "budgetAmount": "number — importe presupuestado",
            "dateKey": "string — YYYY-MM",
            "category": "string",
        },
        "gsis": {
            "CategoryByDateIndex": {"pk": "locationCategoryKey", "sk": "dateKey", "use": "Presupuesto por categoria+fecha"},
        },
        "numeric_fields": ["budgetAmount"],
        "date_fields": ["dateKey"],
        "group_fields": ["category"],
    },

    "Cierre_Caja": {
        "description": "Cierre de caja diario. PK=locationId, SK=fecha.",
        "pk": "locationId",
        "sk": "fecha",
        "key_fields": {
            "fecha": "string — YYYY-MM-DD",
            "total_efectivo": "number",
            "total_tarjeta": "number",
            "total_ventas": "number",
        },
        "gsis": {},
        "numeric_fields": ["total_efectivo", "total_tarjeta", "total_ventas"],
        "date_fields": ["fecha"],
        "group_fields": [],
    },

    "Stock_Inventory": {
        "description": "Inventario de ingredientes/stock. PK=locationId, SK=ingredientId.",
        "pk": "locationId",
        "sk": "ingredientId",
        "key_fields": {
            "name": "string — nombre ingrediente",
            "quantity": "number — cantidad actual",
            "unit": "string — unidad medida",
            "last_price": "number — ultimo precio",
            "category": "string",
        },
        "gsis": {
            "NameIndex": {"pk": "name", "sk": None, "use": "Buscar por nombre"},
        },
        "numeric_fields": ["quantity", "last_price"],
        "date_fields": [],
        "group_fields": ["category"],
    },

    "Location_Accounting_Accounts": {
        "description": "Plan contable por location. PK=locationId, SK=accountCode (ACC#code).",
        "pk": "locationId",
        "sk": "accountCode",
        "sk_format": "ACC#{code}",
        "key_fields": {
            "accountCode": "string — codigo cuenta (e.g. 6280001)",
            "accountName": "string — nombre cuenta",
            "searchTerm": "string — termino busqueda",
        },
        "gsis": {
            "LocationSearchTermIndex": {"pk": "locationId", "sk": "searchTerm", "use": "Buscar cuentas"},
        },
        "numeric_fields": [],
        "date_fields": [],
        "group_fields": [],
    },

    "GC_Transactions_By_Account": {
        "description": "Transacciones bancarias raw (GoCardless). PK=accountId, SK=SK.",
        "pk": "accountId",
        "pk_note": "GoCardless account ID, not locationId",
        "sk": "SK",
        "key_fields": {
            "amount": "number",
            "bookingDate": "string — YYYY-MM-DD",
            "description": "string",
            "counterparty": "string",
        },
        "gsis": {},
        "numeric_fields": ["amount"],
        "date_fields": ["bookingDate"],
        "group_fields": [],
    },
}

# All table names available to the agent
ALL_TABLE_NAMES = sorted(TABLE_CATALOG.keys())


def get_schema_prompt(tables: list[str] | None = None) -> str:
    """
    Generate a compact schema description for the system prompt.

    Returns ~2500 tokens covering all tables with their key fields, GSIs,
    and query patterns. This replaces the old free-text _SCHEMA_BLOCK.
    """
    target = tables or ALL_TABLE_NAMES
    lines = ["TABLES AND QUERY PATTERNS:\n"]

    for i, name in enumerate(target, 1):
        cat = TABLE_CATALOG.get(name)
        if not cat:
            continue

        pk_info = cat["pk"]
        if cat.get("pk_note"):
            pk_info += f" ({cat['pk_note']})"

        sk_info = cat["sk"]
        if cat.get("sk_format"):
            sk_info += f" ({cat['sk_format']})"

        lines.append(f"{i}. {name}: {cat['description']}")
        lines.append(f"   PK={pk_info}, SK={sk_info}")

        # GSIs
        gsis = cat.get("gsis", {})
        if gsis:
            gsi_parts = []
            for gsi_name, gsi_def in gsis.items():
                sk_part = f",sk={gsi_def['sk']}" if gsi_def.get("sk") else ""
                gsi_parts.append(f"{gsi_name}(pk={gsi_def['pk']}{sk_part}) — {gsi_def['use']}")
            lines.append(f"   GSIs: {'; '.join(gsi_parts)}")

        # Key fields (compact)
        fields = cat.get("key_fields", {})
        if fields:
            field_strs = [f"{k} ({v.split(' — ')[0]})" for k, v in fields.items()]
            lines.append(f"   Fields: {', '.join(field_strs)}")

        # Special data notes
        if cat.get("data_notes"):
            lines.append(f"   NOTE: {cat['data_notes']}")

        lines.append("")

    return "\n".join(lines)


def get_slim_fields(table_name: str) -> set[str]:
    """Get the key fields for a table (used for dataset card samples)."""
    cat = TABLE_CATALOG.get(table_name)
    if not cat:
        return set()
    return set(cat.get("key_fields", {}).keys())


def get_numeric_fields(table_name: str) -> list[str]:
    """Get numeric field names for a table (used for dataset card stats)."""
    cat = TABLE_CATALOG.get(table_name)
    if not cat:
        return []
    return cat.get("numeric_fields", [])


def get_date_fields(table_name: str) -> list[str]:
    """Get date field names for a table (used for dataset card date ranges)."""
    cat = TABLE_CATALOG.get(table_name)
    if not cat:
        return []
    return cat.get("date_fields", [])


def get_group_fields(table_name: str) -> list[str]:
    """Get fields suitable for group-by distributions."""
    cat = TABLE_CATALOG.get(table_name)
    if not cat:
        return []
    return cat.get("group_fields", [])


# DynamoDB reserved words that need ExpressionAttributeNames
_RESERVED_WORDS = {
    "status", "name", "description", "type", "key", "value", "count",
    "date", "comment", "data", "source", "position", "role", "location",
    "number", "size", "time", "year", "month", "day", "hour", "minute",
    "second", "action", "domain", "limit", "order", "by",
    # Financial fields that are DynamoDB reserved words
    "total", "amount", "percent", "sum", "index", "table", "all",
    "between", "group", "level", "range", "select", "set", "user",
    "zone", "replace", "exists", "values", "abort", "absolute",
    "language", "both", "transaction", "condition", "exchange",
    "primary", "global", "last", "first", "next", "end", "start",
}


def get_projection_fields(table_name: str) -> set[str]:
    """Get all fields worth projecting for a table (keys + key_fields + common refs).

    Returns the union of PK, SK, key_fields, and essential reference fields.
    This is used to build a DynamoDB ProjectionExpression that avoids
    fetching heavy fields like field_images, raw_text, original_json, etc.
    """
    cat = TABLE_CATALOG.get(table_name)
    if not cat:
        return set()
    fields = set(cat.get("key_fields", {}).keys())
    # Always include PK and SK
    fields.add(cat["pk"])
    fields.add(cat["sk"])
    # Include ai_enrichment for bank reconciliations (nested map used in analysis)
    if table_name == "Bank_Reconciliations":
        fields.add("ai_enrichment")
    return fields


def build_projection_expression(fields: set[str]) -> tuple[str, dict[str, str]] | None:
    """Build DynamoDB ProjectionExpression + ExpressionAttributeNames for reserved words.

    Returns (projection_expr, attr_names) or None if fields is empty.
    """
    if not fields:
        return None
    parts = []
    attr_names: dict[str, str] = {}
    for f in sorted(fields):
        if f.lower() in _RESERVED_WORDS:
            alias = f"#{f}"
            attr_names[alias] = f
            parts.append(alias)
        else:
            parts.append(f)
    return ", ".join(parts), attr_names
