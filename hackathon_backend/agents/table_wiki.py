"""
Single source of truth for all DynamoDB table schemas.

Consolidated from CDK constructs (hackathon_backend/constructs/databases/).
Other files (table_schema.py, schemas.py) import from here.

For each table: description, PK/SK with types + format + examples,
every GSI with name/PK/SK/format/query recipe, key fields, composite key breakdowns.

GSIs marked with "internal": True are workflow/export/review indexes not needed
by the data analyst agent. They are suppressed in get_wiki_text() by default.
"""

from __future__ import annotations

TABLE_WIKI: dict[str, dict] = {
    # ------------------------------------------------------------------
    # 1. User_Expenses
    # ------------------------------------------------------------------
    "User_Expenses": {
        "description": (
            "Expense invoices (facturas de gasto). Richest table: OCR data, "
            "VAT breakdown, accounting entries, reconciliation, workflow state."
        ),
        "table_name_pattern": "{Stage}_User_Expenses",
        "pk": {
            "name": "userId",
            "type": "S",
            "description": "Tenant/location ID (multi-tenant key)",
            "example": "deloitte-84",
        },
        "sk": {
            "name": "categoryDate",
            "type": "S",
            "format": "{CATEGORY}#{YYYY-MM-DD}#{HH:MM:SS.mmm}#{UUID}",
            "example": "COMPRAS#2024-08-29#10:30:00.000#a1b2c3d4",
            "description": "Category + date + unique ID composite key",
        },
        "gsis": {
            "InvoiceNumberSupplierIndex": {
                "pk": "userId",
                "sk": "invoice_supplier_id",
                "sk_format": "{invoice_number}#{normalized_cif}",
                "query_recipe": "Query by userId + begins_with(invoice_supplier_id, invoice_number) for duplicate detection",
                "use_case": "Duplicate detection, lookup by invoice number + supplier",
            },
            "UserIdInvoiceDateIndex": {
                "pk": "userId",
                "sk": "invoice_date",
                "sk_format": "YYYY-MM-DD",
                "query_recipe": "Query by userId + invoice_date BETWEEN :start AND :end for date range queries",
                "use_case": "Date range queries. Essential for P&L, Modelo 303, reports",
            },
            "UserIdSupplierCifIndex": {
                "pk": "userId",
                "sk": "supplier_cif",
                "query_recipe": "Query by userId + supplier_cif = :cif for supplier-specific expenses",
                "use_case": "Queries by supplier. Aging analysis, Modelo 347 (>3005 EUR)",
            },
            "UserIdPnlDateIndex": {
                "pk": "userId",
                "sk": "pnl_date",
                "sk_format": "YYYY-MM-DD",
                "query_recipe": "Query by userId + pnl_date BETWEEN :start AND :end for P&L date range",
                "use_case": "P&L queries. Effective accounting date",
            },
            "UserByReconStateDate": {
                "pk": "userId",
                "sk": "recon_state_date",
                "sk_format": "R#{date} or U#{date}",
                "query_recipe": "Query by userId + begins_with(recon_state_date, 'R#') for reconciled, 'U#' for unreconciled",
                "use_case": "Filter reconciled (R) vs unreconciled (U) invoices by date",
            },
            "UserSupplierDateIndex": {
                "pk": "userSupplierKey",
                "pk_format": "{userId}#{supplier_cif}",
                "sk": "charge_date",
                "sk_format": "YYYY-MM-DD",
                "query_recipe": "Set userSupplierKey = '{userId}#{supplier_cif}', query by charge_date range for cash flow forecast",
                "use_case": "Cash flow forecast. Supplier invoices sorted by payment date",
            },
            "UserIdInvoiceIdIndex": {
                "pk": "userId",
                "sk": "invoiceid",
                "query_recipe": "Query by userId + invoiceid = :id for direct invoice lookup",
                "use_case": "Direct invoice lookup by ID",
            },
            "UserNeedsReviewIndex": {
                "pk": "needsReviewPK",
                "pk_format": "{userId}#PENDING_REVIEW",
                "sk": "categoryDate",
                "query_recipe": "Set needsReviewPK = '{userId}#PENDING_REVIEW' to get all items needing review",
                "use_case": "Items pending review",
                "internal": True,
            },
            "UserByProcessingStatusIndex": {
                "pk": "processing_status",
                "sk": "categoryDate",
                "query_recipe": "Query by processing_status = :status (e.g. 'PROCESSING', 'COMPLETED')",
                "use_case": "Filter by processing status",
                "internal": True,
            },
            "UserWorkflowStateIndex": {
                "pk": "workflowStatePK",
                "pk_format": "{userId}#{workflowState}",
                "sk": "categoryDate",
                "query_recipe": "Set workflowStatePK = '{userId}#{state}' for workflow filtering",
                "use_case": "Filter by workflow state",
                "internal": True,
            },
            "UserDisplayStateIndex": {
                "pk": "displayStatePK",
                "pk_format": "{userId}#{displayState}",
                "sk": "categoryDate",
                "query_recipe": "Set displayStatePK = '{userId}#{state}' for display state filtering",
                "use_case": "Filter by display state",
                "internal": True,
            },
            "UserNeedsExportIndex": {
                "pk": "needsExportPK",
                "pk_format": "{userId}#PENDING_EXPORT",
                "sk": "categoryDate",
                "query_recipe": "Set needsExportPK = '{userId}#PENDING_EXPORT' to get items pending export",
                "use_case": "Items pending export",
                "internal": True,
            },
            "UserHasChangesIndex": {
                "pk": "hasChangesPK",
                "pk_format": "{userId}#HAS_CHANGES",
                "sk": "categoryDate",
                "query_recipe": "Set hasChangesPK = '{userId}#HAS_CHANGES' to get modified items",
                "use_case": "Items with uncommitted changes",
                "internal": True,
            },
            "UserPendingReconciliationVerificationIndex": {
                "pk": "reconciliationVerifiedPK",
                "pk_format": "{userId}#PENDING_RECONCILIATION_VERIFICATION",
                "sk": "categoryDate",
                "query_recipe": "Set reconciliationVerifiedPK = '{userId}#PENDING_RECONCILIATION_VERIFICATION'",
                "use_case": "Items pending reconciliation verification",
                "internal": True,
            },
            "UserNeedsSuenlaceExportIndex": {
                "pk": "needsSuenlaceExportPK",
                "pk_format": "{userId}#PENDING_SUENLACE_EXPORT",
                "sk": "categoryDate",
                "query_recipe": "Set needsSuenlaceExportPK = '{userId}#PENDING_SUENLACE_EXPORT'",
                "use_case": "Items pending Suenlace export",
                "internal": True,
            },
            "UserConciliationNeedsExportIndex": {
                "pk": "conciliationNeedsExportPK",
                "pk_format": "{userId}#PENDING_CONCILIATION_EXPORT",
                "sk": "categoryDate",
                "query_recipe": "Set conciliationNeedsExportPK = '{userId}#PENDING_CONCILIATION_EXPORT'",
                "use_case": "Items pending conciliation export",
                "internal": True,
            },
            "UserReconciliationNeedsA3ExportIndex": {
                "pk": "reconciliationNeedsA3ExportPK",
                "pk_format": "{userId}#PENDING_RECONCILIATION_A3_EXPORT",
                "sk": "categoryDate",
                "query_recipe": "Set reconciliationNeedsA3ExportPK = '{userId}#PENDING_RECONCILIATION_A3_EXPORT'",
                "use_case": "Items pending A3 reconciliation export",
                "internal": True,
            },
            "UserA3ExportQueueIndex": {
                "pk": "queuedForA3ExportPK",
                "pk_format": "{userId}#IN_A3_EXPORT_QUEUE",
                "sk": "categoryDate",
                "query_recipe": "Set queuedForA3ExportPK = '{userId}#IN_A3_EXPORT_QUEUE'",
                "use_case": "Items in A3 export queue",
                "internal": True,
            },
        },
        "filter_only_fields": [
            "supplier (name — use contains() for partial match)",
            "category",
            "concept",
            "documentKind (invoice | credit_note)",
            "gestorId",
            "reconciled (Boolean)",
            "vatOperationType (NORMAL | INTRACOMUNITARIA | ISP | EXENTA)",
        ],
        "key_fields": [
            {"name": "importe", "type": "Decimal", "description": "Tax base before taxes"},
            {"name": "total", "type": "Decimal", "description": "Total invoice amount"},
            {"name": "ivas", "type": "List", "description": "VAT breakdown: [{rate, base, amount}]"},
            {"name": "vatTotalAmount", "type": "Decimal", "description": "Total VAT amount"},
            {"name": "vatDeductibleAmount", "type": "Decimal", "description": "Deductible VAT"},
            {"name": "vatNonDeductibleAmount", "type": "Decimal", "description": "Non-deductible VAT"},
            {"name": "vatOperationType", "type": "S", "description": "NORMAL | INTRACOMUNITARIA | ISP | EXENTA"},
            {"name": "retencion", "type": "Decimal", "description": "IRPF withholding amount"},
            {"name": "supplier", "type": "S", "description": "Supplier name"},
            {"name": "supplier_cif", "type": "S", "description": "Supplier tax ID (CIF/NIF)"},
            {"name": "invoice_date", "type": "S", "description": "Issue date (YYYY-MM-DD)"},
            {"name": "due_date", "type": "S", "description": "Due date (critical for cash flow)"},
            {"name": "pnl_date", "type": "S", "description": "Effective P&L date"},
            {"name": "category", "type": "S", "description": "Expense category (e.g. COMPRAS)"},
            {"name": "concept", "type": "S", "description": "Expense subcategory"},
            {"name": "documentKind", "type": "S", "description": "invoice | credit_note"},
            {"name": "accountingEntries", "type": "List", "description": "[{accountCode, accountName, debit, credit, kind}]"},
            {"name": "reconciled", "type": "Boolean", "description": "Bank reconciliation status"},
            {"name": "matched_transaction_id", "type": "S", "description": "Linked bank transaction ID"},
            {"name": "amount_due", "type": "Decimal", "description": "Amount still due"},
            {"name": "amount_paid", "type": "Decimal", "description": "Amount already paid"},
            {"name": "gestorId", "type": "S", "description": "Manager/processor ID (e.g. 'talky')"},
            {"name": "invoiceid", "type": "S", "description": "Unique invoice identifier"},
        ],
        "source_fields": ["userId", "invoiceid", "categoryDate", "supplier", "supplier_cif", "invoice_date", "due_date", "reconciled", "total"],
    },

    # ------------------------------------------------------------------
    # 2. User_Invoice_Incomes
    # ------------------------------------------------------------------
    "User_Invoice_Incomes": {
        "description": "Income invoices (facturas de ingreso). Mirrors User_Expenses structure but for sales.",
        "table_name_pattern": "{Stage}_User_Invoice_Incomes",
        "pk": {
            "name": "userId",
            "type": "S",
            "description": "Tenant/location ID",
            "example": "deloitte-84",
        },
        "sk": {
            "name": "categoryDate",
            "type": "S",
            "format": "{CATEGORY}#{YYYY-MM-DD}#{HH:MM:SS.mmm}#{UUID}",
            "example": "VENTAS#2024-08-29#10:30:00.000#a1b2c3d4",
        },
        "gsis": {
            "InvoiceNumberSupplierIndex": {
                "pk": "userId", "sk": "invoice_supplier_id",
                "sk_format": "{invoice_number}#{normalized_cif}",
                "query_recipe": "Query by userId + begins_with(invoice_supplier_id, invoice_number)",
                "use_case": "Duplicate detection for income invoices",
            },
            "UserIdInvoiceDateIndex": {
                "pk": "userId", "sk": "invoice_date",
                "sk_format": "YYYY-MM-DD",
                "query_recipe": "Query by userId + invoice_date BETWEEN :start AND :end",
                "use_case": "Date range queries for income",
            },
            "UserIdSupplierCifIndex": {
                "pk": "userId", "sk": "supplier_cif",
                "query_recipe": "Query by userId + supplier_cif = :cif",
                "use_case": "Queries by supplier CIF on income side",
            },
            "UserIdPnlDateIndex": {
                "pk": "userId", "sk": "pnl_date",
                "sk_format": "YYYY-MM-DD",
                "query_recipe": "Query by userId + pnl_date BETWEEN :start AND :end",
                "use_case": "P&L date queries for income",
            },
            "UserByReconStateDate": {
                "pk": "userId", "sk": "recon_state_date",
                "sk_format": "R#{date} or U#{date}",
                "query_recipe": "Query by userId + begins_with(recon_state_date, 'R#') or 'U#'",
                "use_case": "Reconciled vs unreconciled income invoices",
            },
            "UserSupplierDateIndex": {
                "pk": "userSupplierKey", "sk": "charge_date",
                "pk_format": "{userId}#{client_cif}",
                "sk_format": "YYYY-MM-DD",
                "query_recipe": "Set userSupplierKey = '{userId}#{client_cif}', query by charge_date range",
                "use_case": "Cash flow forecast for receivables",
            },
            "UserIdClientCifIndex": {
                "pk": "userId", "sk": "client_cif",
                "query_recipe": "Query by userId + client_cif = :cif for client-specific income",
                "use_case": "Queries by client. Profitability, Modelo 347, receivables",
            },
            "UserIdInvoiceIdIndex": {
                "pk": "userId", "sk": "invoiceid",
                "query_recipe": "Query by userId + invoiceid = :id",
                "use_case": "Direct invoice lookup by ID",
            },
            "UserNeedsReviewIndex": {
                "pk": "needsReviewPK", "sk": "categoryDate",
                "pk_format": "{userId}#PENDING_REVIEW",
                "query_recipe": "Set needsReviewPK = '{userId}#PENDING_REVIEW'",
                "use_case": "Items pending review",
                "internal": True,
            },
            "UserByProcessingStatusIndex": {
                "pk": "processing_status", "sk": "categoryDate",
                "query_recipe": "Query by processing_status = :status",
                "use_case": "Filter by processing status",
                "internal": True,
            },
            "UserWorkflowStateIndex": {
                "pk": "workflowStatePK", "sk": "categoryDate",
                "pk_format": "{userId}#{workflowState}",
                "query_recipe": "Set workflowStatePK = '{userId}#{state}'",
                "use_case": "Filter by workflow state",
                "internal": True,
            },
            "UserDisplayStateIndex": {
                "pk": "displayStatePK", "sk": "categoryDate",
                "pk_format": "{userId}#{displayState}",
                "query_recipe": "Set displayStatePK = '{userId}#{state}'",
                "use_case": "Filter by display state",
                "internal": True,
            },
            "UserNeedsExportIndex": {
                "pk": "needsExportPK", "sk": "categoryDate",
                "pk_format": "{userId}#PENDING_EXPORT",
                "query_recipe": "Set needsExportPK = '{userId}#PENDING_EXPORT'",
                "use_case": "Items pending export",
                "internal": True,
            },
            "UserHasChangesIndex": {
                "pk": "hasChangesPK", "sk": "categoryDate",
                "pk_format": "{userId}#HAS_CHANGES",
                "query_recipe": "Set hasChangesPK = '{userId}#HAS_CHANGES'",
                "use_case": "Items with uncommitted changes",
                "internal": True,
            },
            "UserPendingReconciliationVerificationIndex": {
                "pk": "reconciliationVerifiedPK", "sk": "categoryDate",
                "pk_format": "{userId}#PENDING_RECONCILIATION_VERIFICATION",
                "query_recipe": "Set reconciliationVerifiedPK = '{userId}#PENDING_RECONCILIATION_VERIFICATION'",
                "use_case": "Items pending reconciliation verification",
                "internal": True,
            },
            "UserNeedsSuenlaceExportIndex": {
                "pk": "needsSuenlaceExportPK", "sk": "categoryDate",
                "pk_format": "{userId}#PENDING_SUENLACE_EXPORT",
                "query_recipe": "Set needsSuenlaceExportPK = '{userId}#PENDING_SUENLACE_EXPORT'",
                "use_case": "Items pending Suenlace export",
                "internal": True,
            },
            "UserConciliationNeedsExportIndex": {
                "pk": "conciliationNeedsExportPK", "sk": "categoryDate",
                "pk_format": "{userId}#PENDING_CONCILIATION_EXPORT",
                "query_recipe": "Set conciliationNeedsExportPK = '{userId}#PENDING_CONCILIATION_EXPORT'",
                "use_case": "Items pending conciliation export",
                "internal": True,
            },
            "UserReconciliationNeedsA3ExportIndex": {
                "pk": "reconciliationNeedsA3ExportPK", "sk": "categoryDate",
                "pk_format": "{userId}#PENDING_RECONCILIATION_A3_EXPORT",
                "query_recipe": "Set reconciliationNeedsA3ExportPK = '{userId}#PENDING_RECONCILIATION_A3_EXPORT'",
                "use_case": "Items pending A3 reconciliation export",
                "internal": True,
            },
            "UserA3ExportQueueIndex": {
                "pk": "queuedForA3ExportPK", "sk": "categoryDate",
                "pk_format": "{userId}#IN_A3_EXPORT_QUEUE",
                "query_recipe": "Set queuedForA3ExportPK = '{userId}#IN_A3_EXPORT_QUEUE'",
                "use_case": "Items in A3 export queue",
                "internal": True,
            },
        },
        "filter_only_fields": [
            "client_name (name — use contains() for partial match)",
            "category",
            "concept",
            "documentKind",
            "reconciled (Boolean)",
        ],
        "key_fields": [
            {"name": "importe", "type": "Decimal", "description": "Base amount"},
            {"name": "total", "type": "Decimal", "description": "Total invoice amount"},
            {"name": "ivas", "type": "List", "description": "VAT breakdown"},
            {"name": "client_name", "type": "S", "description": "Client name"},
            {"name": "client_cif", "type": "S", "description": "Client tax ID"},
            {"name": "invoice_date", "type": "S", "description": "Issue date"},
            {"name": "due_date", "type": "S", "description": "Due date"},
            {"name": "pnl_date", "type": "S", "description": "P&L date"},
            {"name": "category", "type": "S", "description": "Category"},
            {"name": "concept", "type": "S", "description": "Subcategory"},
            {"name": "reconciled", "type": "Boolean", "description": "Reconciliation status"},
            {"name": "amount_due", "type": "Decimal", "description": "Amount still due"},
            {"name": "amount_paid", "type": "Decimal", "description": "Amount paid"},
        ],
        "source_fields": ["userId", "invoiceid", "categoryDate", "client_name", "client_cif", "invoice_date", "due_date", "reconciled", "total"],
    },

    # ------------------------------------------------------------------
    # 3. Bank_Reconciliations
    # ------------------------------------------------------------------
    "Bank_Reconciliations": {
        "description": "Bank transactions with full reconciliation lifecycle.",
        "table_name_pattern": "{Stage}_Bank_Reconciliations",
        "pk": {
            "name": "locationId",
            "type": "S",
            "description": "Tenant/location ID",
        },
        "sk": {
            "name": "SK",
            "type": "S",
            "format": "MTXN#{bookingDate}#{transactionId}",
            "example": "MTXN#2024-08-29#txn-abc123",
        },
        "gsis": {
            "PendingByDate": {
                "pk": "GSI1PK", "sk": "GSI1SK",
                "query_recipe": "Query by GSI1PK + GSI1SK range for pending items by date",
                "use_case": "Pending items sorted by date",
            },
            "ByMatchedExpense": {
                "pk": "GSI2PK", "sk": "GSI2SK",
                "query_recipe": "Query by GSI2PK to find matched expense relationships",
                "use_case": "Find transactions matched to expenses",
            },
            "TransactionsByCanonicalId": {
                "pk": "SK", "sk": "locationId",
                "query_recipe": "Query by SK (canonical transaction ID) across locations",
                "use_case": "Cross-location transaction lookup",
            },
            "LocationByStatusDate": {
                "pk": "locationId", "sk": "status_date",
                "sk_format": "{status}#{bookingDate}",
                "query_recipe": "Query by locationId + begins_with(status_date, 'PENDING#') or 'MATCHED#' or 'UNMATCHED#'",
                "use_case": "Cash flow. Filter PENDING/MATCHED/UNMATCHED by date",
            },
            "LocationDisplayStateIndex": {
                "pk": "displayStatePK", "sk": "displayStateUpdatedAt",
                "pk_format": "{locationId}#{displayState}",
                "query_recipe": "Set displayStatePK = '{locationId}#{state}'",
                "use_case": "Filter by display state",
                "internal": True,
            },
            "ByVendorCif": {
                "pk": "vendor_cif", "sk": None,
                "query_recipe": "Query by vendor_cif = :cif (no sort key)",
                "use_case": "All transactions for a vendor CIF",
            },
            "LocationByPayrollDate": {
                "pk": "locationId", "sk": "payroll_date",
                "query_recipe": "Query by locationId + payroll_date BETWEEN :start AND :end",
                "use_case": "Payroll-related bank transactions",
            },
            "LocationByVendorAiId": {
                "pk": "locationId", "sk": "vendor_ai_id",
                "query_recipe": "Query by locationId + vendor_ai_id = :id",
                "use_case": "Transactions by AI-detected vendor",
            },
            "LocationByCustomerAiId": {
                "pk": "locationId", "sk": "customer_ai_id",
                "query_recipe": "Query by locationId + customer_ai_id = :id",
                "use_case": "Transactions by AI-detected customer",
            },
            "ByCustomerCif": {
                "pk": "customer_cif", "sk": None,
                "query_recipe": "Query by customer_cif = :cif (no sort key)",
                "use_case": "All transactions for a customer CIF",
            },
            "HungarianReviewByLocation": {
                "pk": "hungarian_review_pk", "sk": "hungarian_review_type",
                "pk_format": "{locationId}#PENDING_AI_REVIEW",
                "query_recipe": "Set hungarian_review_pk = '{locationId}#PENDING_AI_REVIEW'",
                "use_case": "Items pending Hungarian algorithm review",
                "internal": True,
            },
        },
        "filter_only_fields": [
            "merchant (name)",
            "description (transaction description)",
            "reconciled (Boolean)",
            "match_type (1-1 | 1-N | N-1 | N-M)",
        ],
        "key_fields": [
            {"name": "transactionId", "type": "S", "description": "Bank transaction ID"},
            {"name": "bookingDate", "type": "S", "description": "Transaction date"},
            {"name": "amount", "type": "Decimal", "description": "Amount (negative=expense, positive=income)"},
            {"name": "merchant", "type": "S", "description": "Merchant name"},
            {"name": "description", "type": "S", "description": "Transaction description"},
            {"name": "reconciled", "type": "Boolean", "description": "Reconciliation status"},
            {"name": "status", "type": "S", "description": "PENDING | MATCHED | UNMATCHED"},
            {"name": "matched_invoice_id", "type": "S", "description": "Matched invoice ID"},
            {"name": "matched_expense_id", "type": "S", "description": "Matched expense ID"},
            {"name": "match_type", "type": "S", "description": "1-1 | 1-N | N-1 | N-M"},
        ],
        "source_fields": ["locationId", "SK", "merchant", "bookingDate", "amount", "reconciled", "status"],
    },

    # ------------------------------------------------------------------
    # 4. Payroll_Slips
    # ------------------------------------------------------------------
    "Payroll_Slips": {
        "description": "Payroll slips processed by OCR. Employee salary, SS contributions, IRPF.",
        "table_name_pattern": "{Stage}_Payroll_Slips",
        "pk": {
            "name": "locationId",
            "type": "S",
            "description": "Tenant/location ID",
        },
        "sk": {
            "name": "categoryDate",
            "type": "S",
            "format": "{payroll_date}#{employee_nif}",
            "example": "2024-08#E12345678A",
        },
        "gsis": {
            "LocationEmployeeDateIndex": {
                "pk": "locationId", "sk": "employee_date_key",
                "sk_format": "EMP#{nif}#DATE#{date}",
                "query_recipe": "Query by locationId + begins_with(employee_date_key, 'EMP#{nif}#') for employee history",
                "use_case": "Payroll history per employee",
            },
            "OrgCifPeriodIndex": {
                "pk": "org_cif", "sk": "period_key",
                "sk_format": "PERIOD#{yyyy-mm}#EMP#{nif}",
                "query_recipe": "Query by org_cif + begins_with(period_key, 'PERIOD#{yyyy-mm}') for monthly totals",
                "use_case": "Monthly totals. All payrolls for a company in a month",
            },
            "LocationNeedsReviewIndex": {
                "pk": "needsReviewPK", "sk": "categoryDate",
                "pk_format": "{locationId}#PENDING_REVIEW",
                "query_recipe": "Set needsReviewPK = '{locationId}#PENDING_REVIEW'",
                "use_case": "Payroll slips pending review",
                "internal": True,
            },
            "LocationNeedsExportIndex": {
                "pk": "needsExportPK", "sk": "categoryDate",
                "pk_format": "{locationId}#PENDING_EXPORT",
                "query_recipe": "Set needsExportPK = '{locationId}#PENDING_EXPORT'",
                "use_case": "Payroll slips pending export",
                "internal": True,
            },
            "LocationWorkflowStateIndex": {
                "pk": "workflowStatePK", "sk": "categoryDate",
                "pk_format": "{locationId}#{workflowState}",
                "query_recipe": "Set workflowStatePK = '{locationId}#{state}'",
                "use_case": "Filter by workflow state",
                "internal": True,
            },
            "LocationDisplayStateIndex": {
                "pk": "displayStatePK", "sk": "categoryDate",
                "pk_format": "{locationId}#{displayState}",
                "query_recipe": "Set displayStatePK = '{locationId}#{state}'",
                "use_case": "Filter by display state",
                "internal": True,
            },
            "OrgEmployeeIndex": {
                "pk": "org_employee_key", "sk": "payroll_date",
                "pk_format": "{org_cif}#EMP#{employee_nif}",
                "query_recipe": "Set org_employee_key = '{org_cif}#EMP#{nif}', query by payroll_date range",
                "use_case": "Employee payroll history across a specific org",
            },
            "NeedsReviewIndex": {
                "pk": "needsReview", "sk": "categoryDate",
                "query_recipe": "Query by needsReview = 'true' or 'false'",
                "use_case": "Global review status filter",
                "internal": True,
            },
            "LocationPendingReconciliationVerificationIndex": {
                "pk": "reconciliationVerifiedPK", "sk": "categoryDate",
                "pk_format": "{locationId}#PENDING_RECONCILIATION_VERIFICATION",
                "query_recipe": "Set reconciliationVerifiedPK = '{locationId}#PENDING_RECONCILIATION_VERIFICATION'",
                "use_case": "Payrolls pending reconciliation verification",
                "internal": True,
            },
            "LocationNeedsSuenlaceExportIndex": {
                "pk": "needsSuenlaceExportPK", "sk": "categoryDate",
                "pk_format": "{locationId}#PENDING_SUENLACE_EXPORT",
                "query_recipe": "Set needsSuenlaceExportPK = '{locationId}#PENDING_SUENLACE_EXPORT'",
                "use_case": "Payrolls pending Suenlace export",
                "internal": True,
            },
            "LocationConciliationNeedsExportIndex": {
                "pk": "conciliationNeedsExportPK", "sk": "categoryDate",
                "pk_format": "{locationId}#PENDING_CONCILIATION_EXPORT",
                "query_recipe": "Set conciliationNeedsExportPK = '{locationId}#PENDING_CONCILIATION_EXPORT'",
                "use_case": "Payrolls pending conciliation export",
                "internal": True,
            },
            "LocationReconciliationNeedsA3ExportIndex": {
                "pk": "reconciliationNeedsA3ExportPK", "sk": "categoryDate",
                "pk_format": "{locationId}#PENDING_RECONCILIATION_A3_EXPORT",
                "query_recipe": "Set reconciliationNeedsA3ExportPK = '{locationId}#PENDING_RECONCILIATION_A3_EXPORT'",
                "use_case": "Payrolls pending A3 reconciliation export",
                "internal": True,
            },
            "LocationA3ExportQueueIndex": {
                "pk": "queuedForA3ExportPK", "sk": "categoryDate",
                "pk_format": "{locationId}#IN_A3_EXPORT_QUEUE",
                "query_recipe": "Set queuedForA3ExportPK = '{locationId}#IN_A3_EXPORT_QUEUE'",
                "use_case": "Payrolls in A3 export queue",
                "internal": True,
            },
        },
        "key_fields": [
            {"name": "employee_nif", "type": "S", "description": "Employee tax ID"},
            {"name": "org_cif", "type": "S", "description": "Organization CIF"},
            {"name": "payroll_info.gross_amount", "type": "Decimal", "description": "Gross salary"},
            {"name": "payroll_info.net_amount", "type": "Decimal", "description": "Net salary"},
            {"name": "payroll_info.company_ss_contribution", "type": "Decimal", "description": "Company SS contribution"},
            {"name": "payroll_info.employee_ss_contribution", "type": "Decimal", "description": "Employee SS contribution"},
            {"name": "payroll_info.irpf_amount", "type": "Decimal", "description": "IRPF withholding"},
            {"name": "accountingEntries", "type": "List", "description": "Accounting entries"},
        ],
        "source_fields": ["locationId", "categoryDate", "employee_nif", "org_cif", "payroll_info"],
    },

    # ------------------------------------------------------------------
    # 5. Delivery_Notes
    # ------------------------------------------------------------------
    "Delivery_Notes": {
        "description": "Delivery notes for three-way matching (invoice-delivery-order).",
        "table_name_pattern": "{Stage}_Delivery_Notes",
        "pk": {
            "name": "userId",
            "type": "S",
            "description": "Tenant/location ID",
        },
        "sk": {
            "name": "categoryDate",
            "type": "S",
        },
        "gsis": {
            "DeliveryNoteNumberIndex": {
                "pk": "delivery_note_number", "sk": None,
                "query_recipe": "Query by delivery_note_number = :number (no sort key)",
                "use_case": "Lookup by delivery note number",
            },
            "UserSupplierDeliveryNoteIndex": {
                "pk": "userSupplierCombination", "sk": "delivery_note_number",
                "pk_format": "{userId}#{supplierCIF}",
                "query_recipe": "Set userSupplierCombination = '{userId}#{supplierCIF}', query by delivery_note_number",
                "use_case": "Three-way matching by supplier + delivery note number",
            },
            "DeliveryNotesByProcessingStatusIndex": {
                "pk": "processing_status", "sk": "categoryDate",
                "query_recipe": "Query by processing_status = :status",
                "use_case": "Filter by processing status",
                "internal": True,
            },
            "ProviderCIFReconciledIndex": {
                "pk": "supplier_cif", "sk": "reconciled_date",
                "sk_format": "{TRUE|FALSE}#{delivery_note_date}",
                "query_recipe": "Query by supplier_cif + begins_with(reconciled_date, 'TRUE#') or 'FALSE#'",
                "use_case": "Supplier delivery notes by reconciliation status",
            },
        },
        "key_fields": [
            {"name": "supplier_cif", "type": "S", "description": "Supplier CIF"},
            {"name": "delivery_note_number", "type": "S", "description": "Delivery note number"},
            {"name": "items", "type": "List", "description": "Line items"},
        ],
        "source_fields": ["userId", "categoryDate", "supplier_cif", "delivery_note_number"],
    },

    # ------------------------------------------------------------------
    # 6. Employees
    # ------------------------------------------------------------------
    "Employees": {
        "description": "Employee master data with payroll aggregates.",
        "table_name_pattern": "{Stage}_Employees",
        "pk": {
            "name": "locationId",
            "type": "S",
            "description": "Tenant/location ID",
        },
        "sk": {
            "name": "employeeNif",
            "type": "S",
            "description": "Employee tax ID (NIF)",
        },
        "gsis": {
            "OrgCifEmployeeIndex": {
                "pk": "org_cif", "sk": "employeeNif",
                "query_recipe": "Query by org_cif + employeeNif for org-level employee lookup",
                "use_case": "Employee lookup by organization CIF",
            },
            "EmployeeNifIndex": {
                "pk": "employeeNif", "sk": "locationId",
                "query_recipe": "Query by employeeNif to find all locations for an employee",
                "use_case": "Reverse lookup: find locations by employee NIF",
            },
            "LocationStatusIndex": {
                "pk": "location_status_key", "sk": "lastPayrollDate",
                "pk_format": "{locationId}#{status}",
                "query_recipe": "Set location_status_key = '{locationId}#ACTIVE' or '#INACTIVE', sort by lastPayrollDate",
                "use_case": "Active/inactive employees sorted by last payroll date",
            },
            "SocialSecurityIndex": {
                "pk": "socialSecurityNumber", "sk": "locationId",
                "query_recipe": "Query by socialSecurityNumber to find employee across locations",
                "use_case": "Lookup by social security number",
            },
        },
        "key_fields": [
            {"name": "employeeNif", "type": "S", "description": "Employee NIF"},
            {"name": "name", "type": "S", "description": "Full name"},
            {"name": "position", "type": "S", "description": "Job position"},
            {"name": "lastPayrollDate", "type": "S", "description": "Last payroll date"},
        ],
        "source_fields": ["employeeNif", "name", "locationId"],
    },

    # ------------------------------------------------------------------
    # 7. Providers
    # ------------------------------------------------------------------
    "Providers": {
        "description": (
            "Supplier/vendor master data per location. "
            "USE THIS TABLE to resolve supplier names to CIFs. Small table (~50 items), safe to scan."
        ),
        "table_name_pattern": "{Stage}_Providers",
        "pk": {
            "name": "locationId",
            "type": "S",
            "description": "Tenant/location ID",
        },
        "sk": {
            "name": "cif",
            "type": "S",
            "description": "Provider CIF (lowercase key)",
        },
        "gsis": {},
        "key_fields": [
            {"name": "cif", "type": "S", "description": "Provider CIF"},
            {"name": "nombre", "type": "S", "description": "Provider name (FIELD IS 'nombre', NOT 'name')"},
            {"name": "trade_name", "type": "S", "description": "Trade/commercial name"},
            {"name": "provincia", "type": "S", "description": "Province"},
            {"name": "emails", "type": "SS", "description": "Contact emails"},
        ],
        "source_fields": ["cif", "nombre", "locationId"],
    },

    # ------------------------------------------------------------------
    # 8. Customers
    # ------------------------------------------------------------------
    "Customers": {
        "description": (
            "Customer master data per location. "
            "Use to resolve client names to CIFs. Small table, safe to scan."
        ),
        "table_name_pattern": "{Stage}_Customers",
        "pk": {
            "name": "locationId",
            "type": "S",
            "description": "Tenant/location ID",
        },
        "sk": {
            "name": "cif",
            "type": "S",
            "description": "Customer CIF (lowercase key)",
        },
        "gsis": {},
        "key_fields": [
            {"name": "cif", "type": "S", "description": "Customer CIF"},
            {"name": "nombre", "type": "S", "description": "Customer name (FIELD IS 'nombre', NOT 'name')"},
            {"name": "trade_name", "type": "S", "description": "Trade/commercial name"},
        ],
        "source_fields": ["cif", "nombre", "locationId"],
    },

    # ------------------------------------------------------------------
    # 9. Suppliers (Legacy)
    # ------------------------------------------------------------------
    "Suppliers": {
        "description": "LEGACY suppliers table — typically empty. Use Providers instead.",
        "table_name_pattern": "{Stage}_Suppliers",
        "pk": {
            "name": "locationId",
            "type": "S",
        },
        "sk": {
            "name": "CIF",
            "type": "S",
            "description": "Supplier CIF (UPPERCASE key name)",
        },
        "gsis": {},
        "key_fields": [
            {"name": "CIF", "type": "S", "description": "Supplier CIF"},
            {"name": "name", "type": "S", "description": "Supplier name"},
        ],
        "source_fields": ["CIF", "name", "locationId"],
    },

    # ------------------------------------------------------------------
    # 10. Companies (Commercial Registry)
    # ------------------------------------------------------------------
    "Companies": {
        "description": "Commercial registry data. Company profiles with financial info.",
        "table_name_pattern": "{Stage}_Companies",
        "pk": {
            "name": "PK",
            "type": "S",
            "format": "COMPANY#{company_id}",
            "example": "COMPANY#B12345678",
        },
        "sk": {
            "name": "SK",
            "type": "S",
            "format": "METADATA",
            "example": "METADATA",
        },
        "gsis": {
            "ByNamePrefixIndex": {
                "pk": "company_name_prefix_4", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by company_name_prefix_4 = :prefix (first 4 chars normalized), sorted by revenue desc",
                "use_case": "Autocomplete / typeahead by company name prefix",
            },
            "ByNameWordIndex": {
                "pk": "name_word_1", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by name_word_1 = :word, sorted by revenue desc",
                "use_case": "Search companies by first word of name",
            },
            "ByNameWord2Index": {
                "pk": "name_word_2", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by name_word_2 = :word, sorted by revenue desc",
                "use_case": "Search companies by second word of name",
            },
            "ByFullNameIndex": {
                "pk": "company_name_normalized", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by company_name_normalized = :name for exact match",
                "use_case": "Exact company name lookup",
            },
            "ByCityIndex": {
                "pk": "city", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by city = :city, sorted by revenue desc",
                "use_case": "Companies in a city sorted by revenue",
            },
            "ByProvinceIndex": {
                "pk": "province", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by province = :province, sorted by revenue desc",
                "use_case": "Companies in a province sorted by revenue",
            },
            "ByRevenueTierIndex": {
                "pk": "revenue_tier", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by revenue_tier = :tier, sorted by revenue desc",
                "use_case": "Companies by revenue tier",
            },
            "ByCnaeCodeIndex": {
                "pk": "cnae_code", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by cnae_code = :code, sorted by revenue desc",
                "use_case": "Companies by industry code (CNAE)",
            },
            "ByCityAndNameIndex": {
                "pk": "city", "sk": "company_name_normalized",
                "query_recipe": "Query by city + begins_with(company_name_normalized, :prefix)",
                "use_case": "Companies in a city filtered by name prefix",
            },
            "ByCifIndex": {
                "pk": "cif", "sk": "revenue", "sk_type": "N",
                "query_recipe": "Query by cif = :cif for direct CIF lookup",
                "use_case": "Direct company lookup by CIF",
            },
        },
        "key_fields": [
            {"name": "cif", "type": "S", "description": "Company CIF"},
            {"name": "company_name_normalized", "type": "S", "description": "Normalized name"},
            {"name": "city", "type": "S", "description": "City"},
            {"name": "province", "type": "S", "description": "Province"},
            {"name": "revenue", "type": "N", "description": "Annual revenue"},
            {"name": "cnae_code", "type": "S", "description": "Industry code"},
            {"name": "revenue_tier", "type": "S", "description": "Revenue tier bucket"},
        ],
        "source_fields": ["PK", "cif", "company_name_normalized", "city", "revenue"],
    },

    # ------------------------------------------------------------------
    # 11. Organizations
    # ------------------------------------------------------------------
    "Organizations": {
        "description": "Organization entities (no sort key).",
        "table_name_pattern": "{Stage}_Organizations",
        "pk": {
            "name": "organizationId",
            "type": "S",
        },
        "sk": None,
        "gsis": {},
        "key_fields": [
            {"name": "organizationId", "type": "S", "description": "Unique org ID"},
        ],
        "source_fields": ["organizationId"],
    },

    # ------------------------------------------------------------------
    # 12. Organization_Locations
    # ------------------------------------------------------------------
    "Organization_Locations": {
        "description": "Mapping between organizations and their locations.",
        "table_name_pattern": "{Stage}_Organization_Locations",
        "pk": {
            "name": "organizationId",
            "type": "S",
        },
        "sk": {
            "name": "locationId",
            "type": "S",
        },
        "gsis": {
            "ByLocationId": {
                "pk": "locationId", "sk": "organizationId",
                "query_recipe": "Query by locationId to find which org a location belongs to",
                "use_case": "Reverse lookup: org for a location",
            },
        },
        "key_fields": [
            {"name": "organizationId", "type": "S", "description": "Organization ID"},
            {"name": "locationId", "type": "S", "description": "Location ID"},
        ],
        "source_fields": ["organizationId", "locationId"],
    },

    # ------------------------------------------------------------------
    # 13. User_Invoice_Category_Configs
    # ------------------------------------------------------------------
    "User_Invoice_Category_Configs": {
        "description": "Invoice category configuration per location.",
        "table_name_pattern": "{Stage}_User_Invoice_Category_Configs",
        "pk": {
            "name": "pk",
            "type": "S",
            "format": "L#{locationId}",
        },
        "sk": {
            "name": "sk",
            "type": "S",
            "format": "CFG#ACTIVE or CFG#V#{version}",
        },
        "gsis": {},
        "key_fields": [],
        "source_fields": ["pk", "sk"],
    },

    # ------------------------------------------------------------------
    # 14. Document_Ibans
    # ------------------------------------------------------------------
    "Document_Ibans": {
        "description": "IBAN search index linking IBANs to documents.",
        "table_name_pattern": "{Stage}_Document_Ibans",
        "pk": {
            "name": "PK",
            "type": "S",
            "format": "{userId}#IBAN#{iban_normalized}",
        },
        "sk": {
            "name": "SK",
            "type": "S",
            "format": "DOC#{categoryDate}",
        },
        "gsis": {
            "UserIdIbanIndex": {
                "pk": "userId", "sk": "iban_normalized",
                "query_recipe": "Query by userId + begins_with(iban_normalized, :prefix) for IBAN search",
                "use_case": "Find documents by IBAN",
            },
        },
        "key_fields": [
            {"name": "userId", "type": "S", "description": "User/location ID"},
            {"name": "iban_normalized", "type": "S", "description": "Normalized IBAN"},
        ],
        "source_fields": ["PK", "SK", "userId", "iban_normalized"],
    },

    # ------------------------------------------------------------------
    # 15. Daily_Stats
    # ------------------------------------------------------------------
    "Daily_Stats": {
        "description": "Pre-calculated daily statistics per location (dashboard acceleration).",
        "table_name_pattern": "{Stage}_Daily_Stats",
        "pk": {
            "name": "locationId",
            "type": "S",
        },
        "sk": {
            "name": "dayKey",
            "type": "S",
            "format": "YYYY-MM-DD",
        },
        "gsis": {},
        "key_fields": [
            {"name": "locationId", "type": "S", "description": "Location ID"},
            {"name": "dayKey", "type": "S", "description": "Date key"},
        ],
        "source_fields": ["locationId", "dayKey"],
    },

    # ------------------------------------------------------------------
    # 16. Monthly_Stats
    # ------------------------------------------------------------------
    "Monthly_Stats": {
        "description": "Pre-calculated monthly statistics per location.",
        "table_name_pattern": "{Stage}_Monthly_Stats",
        "pk": {
            "name": "locationId",
            "type": "S",
        },
        "sk": {
            "name": "monthKey",
            "type": "S",
            "format": "YYYY-MM",
        },
        "gsis": {},
        "key_fields": [
            {"name": "locationId", "type": "S", "description": "Location ID"},
            {"name": "monthKey", "type": "S", "description": "Month key"},
        ],
        "source_fields": ["locationId", "monthKey"],
    },
}


# ---------------------------------------------------------------------------
# Utility: get wiki text for LLM context
# ---------------------------------------------------------------------------

def get_wiki_text(include_internal_gsis: bool = False) -> str:
    """Return a structured text summary of all tables for LLM system prompts.

    Organizes each table into QUERYABLE (GSI-backed) vs FILTER-ONLY sections
    and appends common lookup patterns at the end.

    Args:
        include_internal_gsis: If True, include workflow/export/review GSIs.
            Default False — suppresses ~35 internal GSIs to reduce prompt noise.
    """
    lines: list[str] = []

    for table_name, schema in TABLE_WIKI.items():
        lines.append(f"\n## {table_name}")
        lines.append(f"  {schema['description']}")
        lines.append(f"  Table: {{Stage}}_{table_name}")

        # PK
        pk = schema["pk"]
        pk_desc = pk.get("description", "")
        lines.append(f"  PK: {pk['name']} ({pk['type']}){' — ' + pk_desc if pk_desc else ''}")
        if pk.get("format"):
            lines.append(f"      Format: {pk['format']}")

        # SK
        sk = schema.get("sk")
        if sk:
            lines.append(f"  SK: {sk['name']} ({sk['type']})")
            if sk.get("format"):
                lines.append(f"      Format: {sk['format']}")
            if sk.get("example"):
                lines.append(f"      Example: {sk['example']}")

        # QUERYABLE section — non-internal GSIs
        analyst_gsis = {
            name: gsi for name, gsi in schema.get("gsis", {}).items()
            if include_internal_gsis or not gsi.get("internal", False)
        }

        if analyst_gsis:
            lines.append("  QUERYABLE (use in KeyConditionExpression — fast, indexed):")
            # Always mention the base table PK+SK first
            if sk:
                lines.append(f"    - Base table: {pk['name']} + {sk['name']}")
            for gsi_name, gsi in analyst_gsis.items():
                sk_part = f" + {gsi['sk']}" if gsi.get("sk") else ""
                sk_type = f"({gsi.get('sk_type', 'S')})" if gsi.get("sk") else ""
                lines.append(f"    - {gsi_name}: {gsi['pk']}{sk_part}{sk_type}")
                if gsi.get("pk_format"):
                    lines.append(f"      PK format: {gsi['pk_format']}")
                if gsi.get("sk_format"):
                    lines.append(f"      SK format: {gsi['sk_format']}")
                if gsi.get("query_recipe"):
                    lines.append(f"      Recipe: {gsi['query_recipe']}")
        elif sk:
            lines.append("  QUERYABLE:")
            lines.append(f"    - Base table only: {pk['name']} + {sk['name']}")

        # FILTER-ONLY section
        filter_fields = schema.get("filter_only_fields", [])
        if filter_fields:
            lines.append("  FILTER-ONLY (use in FilterExpression — applied after query):")
            for ff in filter_fields:
                lines.append(f"    - {ff}")

        # Key fields (cap at 10)
        if schema.get("key_fields"):
            field_strs = []
            for f in schema["key_fields"][:10]:
                if isinstance(f, dict):
                    field_strs.append(f"{f['name']} ({f['type']}): {f['description']}")
                else:
                    field_strs.append(str(f))
            lines.append("  Key fields:")
            for fs in field_strs:
                lines.append(f"    - {fs}")

    # ----- COMMON LOOKUP PATTERNS -----
    lines.append("\n## COMMON LOOKUP PATTERNS")

    lines.append("""
### Supplier Name -> Expenses (MULTI-TABLE LOOKUP)
1. Query Providers table: method=query, key_condition="locationId = :uid"
   Add FilterExpression: contains(nombre, :name_fragment)
   ExpressionAttributeValues: {":fragment": "search term"}
   ProjectionExpression: "cif, nombre"
   IMPORTANT: The field is 'nombre' (NOT 'name')!
2. Extract the CIF from matching provider(s)
3. Query User_Expenses using UserIdSupplierCifIndex: userId=:uid + supplier_cif=:cif
NOTE: Supplier names are mixed case. Use contains() for partial matching.

### Client Name -> Income Invoices
Same pattern but use Customers table (field is 'nombre'), then UserIdClientCifIndex on User_Invoice_Incomes.

### Date Range -> Expenses
Query User_Expenses.UserIdInvoiceDateIndex: userId=:uid + invoice_date BETWEEN :start AND :end
Example: :start='2025-01-01', :end='2025-01-31' for January 2025

### Category -> Expenses
Query User_Expenses base table: userId=:uid + begins_with(categoryDate, :cat)
Example: :cat='COMPRAS#' for purchases, 'SERVICIOS PROFESIONALES#' for services

### Bank Transactions by Month
Query Bank_Reconciliations base table: locationId=:uid + begins_with(SK, :prefix)
Example: :prefix='MTXN#2025-03' for March 2025

### Bank Transactions by Status
Query Bank_Reconciliations.LocationByStatusDate: locationId=:uid + begins_with(status_date, :status_prefix)
Example: :status_prefix='PENDING#' for pending, 'MATCHED#' for matched

### List All Providers/Customers
Query Providers/Customers table: method=query, key_condition=locationId=:uid
Small tables (~50 items), returns all providers/customers with name and CIF.""")

    return "\n".join(lines)


def get_table_names() -> list[str]:
    """Return all table short names (without stage prefix)."""
    return list(TABLE_WIKI.keys())


def get_table(name_fragment: str) -> dict | None:
    """Find a table schema by name fragment."""
    fragment = name_fragment.lower().replace(" ", "_")
    for name, schema in TABLE_WIKI.items():
        if fragment in name.lower():
            return schema
    return None


def resolve_table_name(short_name: str, stage: str) -> str:
    """Resolve 'User_Expenses' + 'dev' -> 'Dev_User_Expenses'."""
    return f"{stage.capitalize()}_{short_name}"
