"""
DynamoDB table schemas — used as context for the LLM to construct proper queries.

Each schema describes the table's keys, GSIs, and key fields so the orchestrator
and DB query agent know what's queryable and how.
"""

TABLE_SCHEMAS = {
    "User_Expenses": {
        "description": "Expense invoices (facturas de gasto). Richest table: OCR data, VAT breakdown, accounting entries, reconciliation, workflow state.",
        "pk": {"name": "userId", "type": "S", "description": "locationId (multi-tenant key)"},
        "sk": {"name": "categoryDate", "type": "S", "format": "YYYY-MM-DD#HH:MM:SS.mmm#UUID"},
        "gsis": {
            "InvoiceNumberSupplierIndex": {
                "pk": "userId", "sk": "invoice_supplier_id",
                "sk_format": "{invoice_number}#{normalized_cif}",
                "use_case": "Duplicate detection, lookup by invoice number + supplier",
            },
            "UserIdInvoiceDateIndex": {
                "pk": "userId", "sk": "invoice_date",
                "sk_format": "YYYY-MM-DD",
                "use_case": "Date range queries. Essential for P&L, Modelo 303, reports",
            },
            "UserIdSupplierCifIndex": {
                "pk": "userId", "sk": "supplier_cif",
                "use_case": "Queries by supplier. Aging analysis, Modelo 347 (>3005 EUR)",
            },
            "UserIdPnlDateIndex": {
                "pk": "userId", "sk": "pnl_date",
                "sk_format": "YYYY-MM-DD",
                "use_case": "P&L queries. Effective accounting date",
            },
            "UserByReconStateDate": {
                "pk": "userId", "sk": "recon_state_date",
                "sk_format": "R#{date} or U#{date}",
                "use_case": "Filter reconciled (R) vs unreconciled (U) invoices by date",
            },
            "UserSupplierDateIndex": {
                "pk": "userSupplierKey", "sk": "charge_date",
                "pk_format": "{userId}#{supplier_cif}",
                "use_case": "Cash flow forecast. Supplier invoices sorted by payment date",
            },
        },
        "key_fields": [
            "importe (Decimal) - Tax base before taxes",
            "total (Decimal) - Total invoice amount",
            "ivas (List) - VAT breakdown: [{rate, base, amount}]",
            "vatTotalAmount, vatDeductibleAmount, vatNonDeductibleAmount",
            "vatOperationType - NORMAL | INTRACOMUNITARIA | ISP | EXENTA",
            "retencion (Decimal) - IRPF withholding amount",
            "supplier, supplier_cif - Supplier name and tax ID",
            "invoice_date - Issue date",
            "due_date - Due date (critical for cash flow)",
            "pnl_date - Effective P&L date",
            "category, concept - Expense category and subcategory",
            "documentKind - invoice | credit_note",
            "accountingEntries (List) - [{accountCode, accountName, debit, credit, kind}]",
            "reconciled (Boolean) - Bank reconciliation status",
            "matched_transaction_id - Linked bank transaction ID",
            "amount_due, amount_paid - Partial/full payment tracking",
        ],
    },
    "User_Invoice_Incomes": {
        "description": "Income invoices (facturas de ingreso). Mirrors User_Expenses but for sales.",
        "pk": {"name": "userId", "type": "S", "description": "locationId"},
        "sk": {"name": "categoryDate", "type": "S", "format": "YYYY-MM-DD#HH:MM:SS.mmm#UUID"},
        "gsis": {
            "UserIdInvoiceDateIndex": {
                "pk": "userId", "sk": "invoice_date",
                "use_case": "Date range queries for income",
            },
            "UserIdPnlDateIndex": {
                "pk": "userId", "sk": "pnl_date",
                "use_case": "P&L date queries for income",
            },
            "UserIdClientCifIndex": {
                "pk": "userId", "sk": "client_cif",
                "use_case": "Queries by client. Profitability, Modelo 347, receivables",
            },
            "UserByReconStateDate": {
                "pk": "userId", "sk": "recon_state_date",
                "use_case": "Reconciled vs unreconciled income invoices",
            },
            "UserSupplierDateIndex": {
                "pk": "userSupplierKey", "sk": "charge_date",
                "pk_format": "{userId}#{client_cif}",
                "use_case": "Cash flow forecast for receivables",
            },
        },
        "key_fields": [
            "importe, total - Base and total amounts",
            "ivas (List) - VAT breakdown",
            "client_name, client_cif - Client name and tax ID",
            "invoice_date, due_date, pnl_date",
            "category, concept",
            "reconciled, amount_due, amount_paid",
        ],
    },
    "Bank_Reconciliations": {
        "description": "Bank transactions with full reconciliation lifecycle.",
        "pk": {"name": "locationId", "type": "S"},
        "sk": {"name": "SK", "type": "S", "format": "MTXN#{bookingDate}#{transactionId}"},
        "gsis": {
            "LocationByStatusDate": {
                "pk": "locationId", "sk": "status_date",
                "sk_format": "{status}#{bookingDate}",
                "use_case": "Cash flow. Filter PENDING/MATCHED/UNMATCHED by date",
            },
            "ByVendorCif": {
                "pk": "vendor_cif", "sk": None,
                "use_case": "All transactions for a vendor CIF",
            },
            "ByCustomerCif": {
                "pk": "customer_cif", "sk": None,
                "use_case": "All transactions for a customer CIF",
            },
            "LocationByPayrollDate": {
                "pk": "locationId", "sk": "payroll_date",
                "use_case": "Payroll-related bank transactions",
            },
        },
        "key_fields": [
            "transactionId, bookingDate",
            "amount (Decimal) - Negative=expense, Positive=income",
            "merchant, description",
            "reconciled (Boolean), status (PENDING|MATCHED|UNMATCHED)",
            "matched_invoice_id, matched_expense_id, matched_payroll_id",
            "ai_enrichment (Map) - {payment_type, vendor_name, vendor_cif, account_type}",
            "match_type - 1-1 | 1-N | N-1 | N-M",
        ],
    },
    "Payroll_Slips": {
        "description": "Payroll slips processed by OCR. Employee salary, SS contributions, IRPF.",
        "pk": {"name": "locationId", "type": "S"},
        "sk": {"name": "categoryDate", "type": "S", "format": "{payroll_date}#{employee_nif}"},
        "gsis": {
            "LocationEmployeeDateIndex": {
                "pk": "locationId", "sk": "employee_date_key",
                "sk_format": "EMP#{nif}#DATE#{date}",
                "use_case": "Payroll history per employee",
            },
            "OrgCifPeriodIndex": {
                "pk": "org_cif", "sk": "period_key",
                "sk_format": "PERIOD#{yyyy-mm}#EMP#{nif}",
                "use_case": "Monthly totals. All payrolls for a company in a month",
            },
        },
        "key_fields": [
            "employee_nif, org_cif",
            "payroll_info.gross_amount, payroll_info.net_amount",
            "payroll_info.company_ss_contribution, payroll_info.employee_ss_contribution",
            "payroll_info.irpf_amount",
            "accountingEntries (List)",
        ],
    },
    "Delivery_Notes": {
        "description": "Delivery notes for three-way matching (invoice-delivery-order).",
        "pk": {"name": "userId", "type": "S"},
        "sk": {"name": "categoryDate", "type": "S"},
        "gsis": {
            "UserSupplierDeliveryNoteIndex": {
                "pk": "userSupplierKey",
                "pk_format": "{userId}#{supplier_cif}",
                "sk": "delivery_note_number",
                "use_case": "Three-way matching by supplier + delivery note number",
            },
        },
        "key_fields": ["supplier_cif, delivery_note_number, items"],
    },
    "Employees": {
        "description": "Employee master data with payroll aggregates.",
        "pk": {"name": "locationId", "type": "S"},
        "sk": {"name": "employeeNif", "type": "S"},
        "gsis": {},
        "key_fields": ["employeeNif, name, position, payroll aggregates"],
    },
    "Providers": {
        "description": "Supplier/vendor master data per location.",
        "pk": {"name": "locationId", "type": "S"},
        "sk": {"name": "cif", "type": "S"},
        "gsis": {},
        "key_fields": ["cif, name, address, contact info"],
    },
    "Customers": {
        "description": "Customer master data per location.",
        "pk": {"name": "locationId", "type": "S"},
        "sk": {"name": "cif", "type": "S"},
        "gsis": {},
        "key_fields": ["cif, name, address, contact info"],
    },
    "Daily_Stats": {
        "description": "Pre-calculated daily statistics per location (dashboard acceleration).",
        "pk": {"name": "locationId", "type": "S"},
        "sk": {"name": "dayKey", "type": "S"},
        "gsis": {},
        "key_fields": ["Aggregated daily metrics"],
    },
    "Monthly_Stats": {
        "description": "Pre-calculated monthly statistics per location.",
        "pk": {"name": "locationId", "type": "S"},
        "sk": {"name": "monthKey", "type": "S"},
        "gsis": {},
        "key_fields": ["Aggregated monthly metrics"],
    },
}


def get_schemas_summary() -> str:
    """Return a compact text summary of all table schemas for LLM context."""
    lines = []
    for table_name, schema in TABLE_SCHEMAS.items():
        lines.append(f"\n## {table_name}")
        lines.append(f"  {schema['description']}")
        lines.append(f"  PK: {schema['pk']['name']} ({schema['pk'].get('description', '')})")
        sk = schema.get('sk', {})
        if sk:
            lines.append(f"  SK: {sk['name']} (format: {sk.get('format', 'N/A')})")
        if schema.get("gsis"):
            lines.append("  GSIs:")
            for gsi_name, gsi in schema["gsis"].items():
                lines.append(f"    - {gsi_name}: PK={gsi['pk']}, SK={gsi.get('sk', 'N/A')} → {gsi['use_case']}")
        lines.append(f"  Key fields: {', '.join(schema['key_fields'][:5])}")
    return "\n".join(lines)
