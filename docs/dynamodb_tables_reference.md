# DynamoDB Tables Reference - Talky Platform

Complete reference of all DynamoDB tables in the Talky platform. For each table: purpose, key schema, GSIs, streams, TTL, and relevant fields.

**Naming convention:** All table names use `config.resource_name("BaseName")` which adds environment prefix/suffix (e.g., `Dev_User_Expenses`, `Prod_User_Expenses`).

**Legacy note:** Many tables use `userId` as PK but actually store `locationId`. This is a legacy naming convention from when the system had 1:1 user-to-location relationship.

---

## Table of Contents

### Financial Core
1. [User_Expenses](#1-user_expenses) - Expense invoices (the main financial table)
2. [User_Invoice_Incomes](#2-user_invoice_incomes) - Income invoices (issued invoices)
3. [Delivery_Notes](#3-delivery_notes) - Delivery notes (albaranes)
4. [Payroll_Slips](#4-payroll_slips) - Payroll/nominas

### Bank Reconciliation
5. [Bank_Reconciliations](#5-bank_reconciliations) - Bank transaction matches
6. [Vendors_AI](#6-vendors_ai) - AI-grouped vendor names for reconciliation
7. [Clients_AI](#7-clients_ai) - AI-grouped client names for income reconciliation
8. [Reconciliation_Suggestions](#8-reconciliation_suggestions) - Complex 1-N / N-1 suggestions
9. [Supplier_Payment_Patterns](#9-supplier_payment_patterns) - Temporal guardrails for payments

### Banking (GoCardless)
10. [Gocardless_Connections](#10-gocardless_connections) - Bank account connections
11. [Gocardless_User_Agreements](#11-gocardless_user_agreements) - User consent agreements
12. [GC_Transactions_By_Account](#12-gc_transactions_by_account) - Cached bank transactions
13. [GC_Balances_By_Account](#13-gc_balances_by_account) - Cached bank balances

### Providers & Products
14. [Providers](#14-providers) - Provider master data per location
15. [Customers](#15-customers) - Customer master data per location
16. [Provider_Products](#16-provider_products) - Products by provider
17. [Suppliers](#17-suppliers) - Legacy suppliers table
18. [Providers_General](#18-providers_general) - General provider catalog
19. [Providers_Global](#19-providers_global) - National Spanish provider search

### Accounting & P&L
20. [Location_Accounting_Accounts](#20-location_accounting_accounts) - Chart of accounts per location
21. [Company_Accounting_Accounts](#21-company_accounting_accounts) - Chart of accounts per company CIF
22. [Location_Custom_PnL](#22-location_custom_pnl) - Custom P&L entries
23. [Location_Budgets](#23-location_budgets) - Budget targets by category/date
24. [Location_Transaction_Tags](#24-location_transaction_tags) - Custom transaction tags
25. [User_Invoice_Category_Configs](#25-user_invoice_category_configs) - Invoice category configuration

### Delivery Note Reconciliation
26. [Delivery_Notes_and_Invoices_tracker](#26-delivery_notes_and_invoices_tracker) - DN-Invoice matching
27. [Delivery_Notes_Reconciliation_Tracking](#27-delivery_notes_reconciliation_tracking) - DN reconciliation events
28. [Delivery_Notes_Processing_Status](#28-delivery_notes_processing_status) - DN processing pipeline status
29. [Invoice_Delivery_Stats](#29-invoice_delivery_stats) - Reconciliation statistics
30. [Invoice_Delivery_Incidences](#30-invoice_delivery_incidences) - Reconciliation incidents

### Companies & Organizations
31. [Companies](#31-companies) - Spanish company registry (CNAE, revenue, CIF)
32. [Company_Gestors](#32-company_gestors) - Gestor-company relationships
33. [Gestor_Providers](#33-gestor_providers) - Gestor-provider relationships
34. [A3_Files_Queue](#34-a3_files_queue) - A3 accounting export queue
35. [Organizations](#35-organizations) - Multi-location tenant root
36. [Organization_Members](#36-organization_members) - Org-user relationships
37. [Organization_Locations](#37-organization_locations) - Org-location relationships

### Employees
38. [Employees](#38-employees) - Employee master with payroll tracking
39. [Payroll_OCR_Tracking](#39-payroll_ocr_tracking) - Payroll OCR pipeline status

### Analytics & Stats
40. [Daily_Stats](#40-daily_stats) - Daily POS statistics
41. [Monthly_Stats](#41-monthly_stats) - Monthly POS statistics
42. [Cierre_Caja](#42-cierre_caja) - Cash register closings
43. [Daily_TPV_Reports](#43-daily_tpv_reports) - Daily payment terminal reports

### Pipeline & OCR Tracking
44. [Pipeline_Document_Tracking](#44-pipeline_document_tracking) - Full document pipeline tracking
45. [Pipeline_Stats](#45-pipeline_stats) - Aggregated pipeline statistics
46. [Invoices_OCR_Tracking](#46-invoices_ocr_tracking) - Invoice OCR processing status
47. [Invoices_OCR_Daily_Stats](#47-invoices_ocr_daily_stats) - OCR daily stats
48. [Invoices_OCR_Dedup_Locks](#48-invoices_ocr_dedup_locks) - Deduplication locks
49. [Document_Hashes](#49-document_hashes) - SHA256 pre-OCR dedup
50. [Invoice_Processing_Status](#50-invoice_processing_status) - Invoice processing pipeline
51. [Invoice_Learning](#51-invoice_learning) - Invoice pattern learning
52. [Delivery_Note_Learning](#52-delivery_note_learning) - DN pattern learning

### Document Indexes
53. [Document_IBANs](#53-document_ibans) - Inverted IBAN-to-document index
54. [Document_Search_Index](#54-document_search_index) - Universal tag-to-document index

### Testing & Quality
55. [Reconciliation_Datasets](#55-reconciliation_datasets) - Reconciliation test datasets
56. [Reconciliation_Test_Runs](#56-reconciliation_test_runs) - Reconciliation test results
57. [OCR_Golden_Annotations](#57-ocr_golden_annotations) - OCR golden dataset annotations
58. [OCR_Test_Runs](#58-ocr_test_runs) - OCR test results
59. [Stock_Golden_Annotations](#59-stock_golden_annotations) - Stock golden dataset annotations
60. [Stock_Test_Runs](#60-stock_test_runs) - Stock test results
61. [Bank_Reconciliation_Debug_Index](#61-bank_reconciliation_debug_index) - Debug S3 key index

### Stock & Inventory
62. [Stock_Inventory](#62-stock_inventory) - Ingredient stock per location
63. [Stock_Transfers](#63-stock_transfers) - Inter-location stock transfers
64. [Stock_Transfer_Product_Locks](#64-stock_transfer_product_locks) - Auto-creation locks

### Escandallos (Recipes)
65. [Escandallos](#65-escandallos) - Recipe definitions
66. [Escandallo_Pending_Ingredients](#66-escandallo_pending_ingredients) - Pending ingredient resolution
67. [Escandallo_Ingredient_Usage_Suggestions](#67-escandallo_ingredient_usage_suggestions) - AI ingredient suggestions
68. [Escandallo_Ingredient_Debug_Index](#68-escandallo_ingredient_debug_index) - Debug traces
69. [Escandallos_AI_Executions](#69-escandallos_ai_executions) - AI execution tracking

### POS & Products
70. [Tabs](#70-tabs) - POS tickets/tabs
71. [Location_Tabs](#71-location_tabs) - Location-scoped tabs
72. [Product_Catalog](#72-product_catalog) - POS product catalog
73. [Product_Data](#73-product_data) - Product sales data
74. [Product_Count](#74-product_count) - Product stock counts
75. [Products_Normalizer_Product_Errors](#75-products_normalizer_product_errors) - Normalization errors

### Other
76. [Users_Locations](#76-users_locations) - User-location associations
77. [User_Locations_Data](#77-user_locations_data) - Location metadata
78. [FX_Rates](#78-fx_rates) - Currency exchange rates cache
79. [Licenses](#79-licenses) - Client licenses
80. [Langfuse_Prompts](#80-langfuse_prompts) - AI prompt versioning
81. [User_Subscriptions](#81-user_subscriptions) - Recurring payment subscriptions
82. [Workers](#82-workers) - Restaurant staff
83. [Workers_Shifts](#83-workers_shifts) - Staff shift records
84. [Talkier_AI_User_Chats](#84-talkier_ai_user_chats) - AI assistant chat history
85. [Talkiers_Reports](#85-talkiers_reports) - AI-generated reports

---

## Financial Core

### 1. User_Expenses

**Purpose:** Main expense invoices table. Stores every scanned/OCR-processed expense invoice (facturas de gasto). The central table for financial analytics, reconciliation, and accounting export.

| Property | Value |
|---|---|
| Table Name | `User_Expenses` |
| PK | `userId` (STRING) - **actually stores locationId** (legacy naming) |
| SK | `categoryDate` (STRING) - format: `YYYY-MM-DD#{uuid}` |
| Stream | NEW_AND_OLD_IMAGES |
| Billing | PAY_PER_REQUEST |

**GSIs (19 total):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | InvoiceNumberSupplierIndex | userId | invoice_supplier_id | Lookup by invoice number + supplier combo |
| 2 | UserIdInvoiceDateIndex | userId | invoice_date | Date range queries (YYYY-MM-DD) |
| 3 | UserIdSupplierCifIndex | userId | supplier_cif | Filter by supplier CIF |
| 4 | UserIdPnlDateIndex | userId | pnl_date | P&L date range queries |
| 5 | UserByReconStateDate | userId | recon_state_date | Filter by reconciliation state + date |
| 6 | UserSupplierDateIndex | userSupplierKey (`{userId}#{cif}`) | charge_date | Supplier payment date queries |
| 7 | UserIdInvoiceIdIndex | userId | invoiceid | Lookup by document ID |
| 8 | UserNeedsReviewIndex | needsReviewPK (`{userId}#PENDING_REVIEW`) | categoryDate | Review queue for gestoria |
| 9 | UserByProcessingStatusIndex | processing_status | categoryDate | Filter by processing pipeline state |
| 10 | UserWorkflowStateIndex | workflowStatePK (`{userId}#{state}`) | categoryDate | Canonical workflow state filter |
| 11 | UserDisplayStateIndex | displayStatePK (`{userId}#{displayState}`) | categoryDate | Frontend display state filter |
| 12 | UserNeedsExportIndex | needsExportPK (`{userId}#PENDING_EXPORT`) | categoryDate | Pending A3 export |
| 13 | UserHasChangesIndex | hasChangesPK (`{userId}#HAS_CHANGES`) | categoryDate | Changes pending processing |
| 14 | UserPendingReconciliationVerificationIndex | reconciliationVerifiedPK | categoryDate | Pending reconciliation verification |
| 15 | UserNeedsSuenlaceExportIndex | needsSuenlaceExportPK | categoryDate | Pending Suenlace .DAT export |
| 16 | UserConciliationNeedsExportIndex | conciliationNeedsExportPK | categoryDate | Pending conciliation export |
| 17 | UserReconciliationNeedsA3ExportIndex | reconciliationNeedsA3ExportPK | categoryDate | Pending A3 reconciliation export |
| 18 | UserA3ExportQueueIndex | queuedForA3ExportPK (`{userId}#IN_A3_EXPORT_QUEUE`) | categoryDate | A3 export queue |

**Key Fields (200+ total, grouped by domain):**

**Identity:**
- `invoiceid` (S) - Unique document ID (UUID)
- `kind` (S) - "EXPENSE" or "INCOME"
- `invoice_number` (S) - Invoice number as printed on document
- `invoice_supplier_id` (S) - Composite: `{invoice_number}#{supplier_cif}`
- `originalFileName` (S) - Original uploaded filename
- `invoice_url` (S) - S3 URI of original document (`s3://bucket/key`)
- `method` (S) - Upload method (whatsapp, web, email, api)
- `Source` (S) - "Web" / "WhatsApp" / "Email"

**Supplier:**
- `supplier` (S) - Supplier name (as extracted by OCR)
- `supplier_cif` (S) - Supplier CIF/NIF
- `supplier_province` (S) - Supplier province
- `isTemporarySupplierCIF` (BOOL) - True if CIF starts with "TEMP-"

**Client (for income invoices):**
- `client_name` (S) - Client name
- `client_cif` (S) - Client CIF
- `client_address` (S) - Client address

**Dates:**
- `invoice_date` (S) - Invoice date (YYYY-MM-DD)
- `due_date` (S) - Due date (YYYY-MM-DD)
- `pnl_date` (S) - P&L assignment date (YYYY-MM-DD)
- `charge_date` (S) - Effective charge date (due_date or invoice_date fallback)
- `period` (S) - Billing period
- `createdAt` (S) - ISO timestamp of creation

**Financial:**
- `importe` (N) - Base amount (before tax)
- `total` (N) - Total amount (with tax)
- `ivas` (L) - List of VAT breakdowns: `[{base, rate, amount}]`
- `retencion` (N) - Withholding tax amount
- `retencion_type` (S) - Withholding type (IRPF, etc.)
- `descuentos_generales` (L) - General discounts list
- `descuentos_generales_total` (N) - Total discount amount

**Currency:**
- `currency` (M) - `{code, symbol, source, confidence, evidence}`
- `language` (M) - `{code, confidence, evidence, source}`

**Accounting:**
- `concept` (S) - Account concept (e.g., "Alimentacion")
- `category` (S) - Account type/category
- `gestorId` (S) - Gestor ID (default: "talky")
- `companyCif` (S) - Company CIF for accounting
- `providerAccountCode` (S) - Provider account code (e.g., "4000001")
- `providerAccountName` (S) - Provider account description
- `expenseAccountCode` (S) - Expense account code (e.g., "6280001")
- `expenseAccountName` (S) - Expense account description
- `accountingEntries` (L) - Full list of accounting journal entries
- `vatOperationType` (S) - VAT type: NORMAL / INTRACOMUNITARIA / ISP / EXENTA
- `vatDeductibilityBucket` (S) - VAT deductibility classification
- `vatTotalAmount` (N) - Total VAT amount
- `vatDeductibleAmount` (N) - Deductible VAT portion
- `vatNonDeductibleAmount` (N) - Non-deductible VAT portion

**Income-specific:**
- `incomeAccountCode` (S) - Income account code
- `customerAccountCode` (S) - Customer account code
- `vatAccountCode` (S) - VAT account code

**Workflow & State:**
- `processing_status` (S) - Pipeline state: PENDING_PRODUCTS, pending_user_review, COMPLETED
- `workflowState` (S) - Canonical state: INVOICE_PENDING_EXPORT, INVOICE_SENT, etc.
- `workflowStatePK` (S) - `{userId}#{workflowState}`
- `displayStatePK` (S) - `{userId}#{displayState}` (materialized for read-model)
- `reconciliationState` (S) - UNRECONCILED, RECONCILED, PARTIALLY_RECONCILED
- `reconciliationReviewState` (S) - PENDING_REVIEW, REVIEWED
- `settlementMode` (S) - NONE, BANK_TRANSFER, CASH, etc.
- `a3InvoiceVerificationStatus` (S) - PENDING, VERIFIED, REJECTED
- `Automated` (N) - 1 if processed automatically

**Reconciliation:**
- `reconciled` (BOOL) - Whether reconciled with bank transaction
- `reconciliationVerified` (BOOL) - Whether reconciliation was user-verified
- `has_changes` (BOOL) - Pending changes from reconciliation
- `conciliationNeedsReview` (BOOL) - Needs manual conciliation review
- `recon_state_date` (S) - Composite: `{reconciliationState}#{date}`

**Export Flags:**
- `needsSuenlaceExport` (BOOL) - Pending Suenlace .DAT export
- `conciliationNeedsExport` (BOOL) - Pending conciliation export
- `reconciliationNeedsA3Export` (BOOL) - Pending A3 export
- `queuedForA3Export` (BOOL) - In A3 export queue
- `needsReview` (S) - "true"/"false" for review flag

**Review:**
- `needsReviewReason` (S) - Why it needs review
- `needsReviewReasons` (L) - List of review reasons
- `talkyVerified` (BOOL) - Verified by Talky AI
- `needsReviewSuppressed` (S) - Reason for suppressing review

**Document Classification:**
- `documentClassification` (M) - Classification result map
- `documentKind` (S) - "invoice", "credit_note", "delivery_note", "payroll"
- `documentKindConfidence` (N) - Classification confidence 0-1

**Credit Notes:**
- `isProviderCredit` (BOOL) - Is a credit note
- `providerCreditAmount` (N) - Credit amount
- `providerCreditConsumed` (BOOL) - Whether credit was applied

**Assets Detection:**
- `hasAssets` (BOOL) - Has capital assets
- `assetsDetection` (M) - Asset detection details
- `isMixedInvoice` (BOOL) - Contains both assets and expenses

**Math Validation:**
- `mathTotalsCoherent` (BOOL) - Whether amounts add up
- `mathTotalsDiff` (N) - Difference found in validation

**Products (nested list):**
- `products` (L) - List of line items, each with:
  - `product_name` (S) - Product name
  - `quantity` (N) - Quantity
  - `unit` (S) - Unit of measure
  - `unit_price` (N) - Unit price
  - `price` (N) - Line total
  - `final_price` (N) - Final price after discounts
  - `product_id` (S) - Normalized product ID
  - `matching_confidence` (S) - HIGH/MEDIUM/LOW
  - `_assigned_by` (S) - Assignment method (ai_matching, active_inheritance, etc.)

---

### 2. User_Invoice_Incomes

**Purpose:** Income invoices (facturas emitidas). Same schema as User_Expenses but for revenue side. Stores invoices issued to clients.

| Property | Value |
|---|---|
| Table Name | `User_Invoice_Incomes` |
| PK | `userId` (STRING) - **actually stores locationId** |
| SK | `categoryDate` (STRING) - `YYYY-MM-DD#{uuid}` |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (20 total):** Same as User_Expenses plus:

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| Extra | UserIdClientCifIndex | userId | client_cif | Filter by client CIF (income-specific) |

All other 19 GSIs identical to User_Expenses (InvoiceNumberSupplierIndex, UserIdInvoiceDateIndex, etc.)

**Fields:** Same schema as User_Expenses. Key difference: `kind` = "INCOME" and uses `client_cif` / `client_name` as primary counterparty instead of `supplier_cif`.

---

### 3. Delivery_Notes

**Purpose:** Delivery notes (albaranes). Documents goods received from suppliers before formal invoice. Used for DN-Invoice reconciliation.

| Property | Value |
|---|---|
| Table Name | `Delivery_Notes` |
| PK | `userId` (STRING) - **actually stores locationId** |
| SK | `categoryDate` (STRING) |
| Stream | NEW_IMAGE |

**GSIs (4 active, 5 commented out):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | DeliveryNoteNumberIndex | delivery_note_number | - | Global lookup by DN number |
| 2 | UserSupplierDeliveryNoteIndex | userSupplierCombination (`userId#supplierCIF`) | delivery_note_number | Lookup by location+supplier+DN number |
| 3 | DeliveryNotesByProcessingStatusIndex | processing_status | categoryDate | Filter by processing state |
| 4 | ProviderCIFReconciledIndex | supplier_cif | reconciled_date (`{TRUE|FALSE}#{date}`) | Supplier's reconciliation status |

**Key Fields:**
- `delivery_note_number` (S) - DN number
- `delivery_note_date` (S) - Date of delivery
- `supplier` (S) - Supplier name
- `supplier_cif` (S) - Supplier CIF
- `reconciled` (S) - "TRUE" / "FALSE"
- `reconciled_date` (S) - Composite: `{TRUE|FALSE}#{YYYY-MM-DD}`
- `userSupplierCombination` (S) - `{userId}#{supplier_cif}`
- `products` (L) - Product line items (same structure as invoice products)
- `total` (N) - Total amount
- `processing_status` (S) - Pipeline state

---

### 4. Payroll_Slips

**Purpose:** Payroll documents (nominas). Stores OCR-extracted payroll data with employee info, salary breakdown, accounting entries, and verification status.

| Property | Value |
|---|---|
| Table Name | `Payroll_Slips` |
| PK | `locationId` (STRING) |
| SK | `categoryDate` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (14 total):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | LocationEmployeeDateIndex | locationId | employee_date_key (`EMP#{nif}#DATE#{date}`) | Employee payrolls by date |
| 2 | OrgCifPeriodIndex | org_cif | period_key (`PERIOD#{yyyy-mm}#EMP#{nif}`) | Company period queries |
| 3 | LocationNeedsReviewIndex | needsReviewPK (`{loc}#PENDING_REVIEW`) | categoryDate | Review queue |
| 4 | LocationNeedsExportIndex | needsExportPK (`{loc}#PENDING_EXPORT`) | categoryDate | Export queue |
| 4b | LocationWorkflowStateIndex | workflowStatePK (`{loc}#{state}`) | categoryDate | Workflow state |
| 5 | LocationDisplayStateIndex | displayStatePK (`{loc}#{state}`) | categoryDate | Display state |
| 5b | OrgEmployeeIndex | org_employee_key (`{cif}#EMP#{nif}`) | payroll_date | Employee history per org |
| 6 | NeedsReviewIndex | needsReview ("true"/"false") | categoryDate | Global review flag |
| 7 | LocationPendingReconciliationVerificationIndex | reconciliationVerifiedPK | categoryDate | Pending recon verification |
| 8 | LocationNeedsSuenlaceExportIndex | needsSuenlaceExportPK | categoryDate | Suenlace export |
| 9 | LocationConciliationNeedsExportIndex | conciliationNeedsExportPK | categoryDate | Conciliation export |
| 10 | LocationReconciliationNeedsA3ExportIndex | reconciliationNeedsA3ExportPK | categoryDate | A3 recon export |
| 11 | LocationA3ExportQueueIndex | queuedForA3ExportPK | categoryDate | A3 export queue |

**Key Fields:**
- `locationId` (S) - Location identifier
- `org_cif` (S) - Company CIF
- `employee_nif` (S) - Employee NIF
- `payroll_date` (S) - Payroll period date (YYYY-MM-DD)
- `payroll_number` (S) - Payroll sequence number
- `netAmount` (N) - Net amount (liquido a percibir)
- `employee_info` (M) - Employee details map: name, nif, social_security, category, seniority_date, contract_type
- `payroll_info` (M) - Payroll details map: earnings, deductions, company_contributions, bases
- `accountingEntries` (L) - Accounting journal entries (GASTO_BRUTO, SS_EMPRESA, IRPF_RETENCION, LIQUIDO_A_PAGAR)
- `verification` (M) - Golden rules verification: is_balanced, total_debit, total_credit, rules_passed
- `content_hash` (S) - SHA256 for duplicate detection
- `duplicate_status` (S) - ORIGINAL / DUPLICATE_EXACT / DUPLICATE_CONTENT
- `documentClassification` (M) - Document type classification
- `documentKind` (S) - "payroll", "settlement", "certificate"
- `multiPayrollDetected` (BOOL) - Multi-payroll in single document
- Same workflow/export flags as User_Expenses

---

## Bank Reconciliation

### 5. Bank_Reconciliations

**Purpose:** Core bank reconciliation table. Stores bank transaction matches (Hungarian algorithm results), reconciliation status, and match metadata. Single-table design with composite SK.

| Property | Value |
|---|---|
| Table Name | `Bank_Reconciliations` |
| PK | `locationId` (STRING) |
| SK | `SK` (STRING) - format: `TXN#{transactionId}` or `MATCH#{matchId}` |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (12 total):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | PendingByDate | GSI1PK | GSI1SK | Pending matches by date |
| 2 | ByMatchedExpense | GSI2PK | GSI2SK | Lookup by matched invoice |
| 3 | TransactionsByCanonicalId | SK | locationId | Multi-location transaction lookup |
| 4 | LocationByStatusDate | locationId | status_date | Filter by status + date |
| 5 | LocationDisplayStateIndex | displayStatePK (`{loc}#{state}`) | displayStateUpdatedAt | Display state (read model) |
| 6 | ByVendorCif | vendor_cif | - | Lookup by vendor CIF |
| 7 | LocationByPayrollDate | locationId | payroll_date | Payroll reconciliation queries |
| 8 | LocationByVendorAiId | locationId | vendor_ai_id | Group by AI vendor |
| 9 | LocationByCustomerAiId | locationId | customer_ai_id | Group by AI customer |
| 10 | ByCustomerCif | customer_cif | - | Lookup by customer CIF |
| 11 | HungarianReviewByLocation | hungarian_review_pk (`LOC#{id}#PENDING_AI_REVIEW`) | hungarian_review_type | AI review pipeline |

**Key Fields (bank transaction items):**
- `transactionId` (S) - GoCardless transaction ID
- `internalTransactionId` (S) - Internal canonical ID
- `bookingDate` (S) - Transaction booking date
- `valueDate` (S) - Value date
- `transactionAmount` (N) - Amount (negative = debit, positive = credit)
- `currency` (S) - ISO currency code
- `remittanceInformationUnstructured` (S) - Bank description text
- `creditorName` / `debtorName` (S) - Counterparty name
- `creditorAccount` / `debtorAccount` (S) - Counterparty IBAN
- `vendor_ai_id` (S) - Matched Vendors_AI group ID
- `customer_ai_id` (S) - Matched Clients_AI group ID
- `vendor_cif` (S) - Resolved vendor CIF
- `customer_cif` (S) - Resolved customer CIF
- `status` (S) - PENDING, MATCHED, CONFIRMED, REJECTED
- `status_date` (S) - `{status}#{date}`
- `displayState` (S) - Human-readable display state
- `displayStatePK` (S) - `{locationId}#{displayState}`
- `confidence` (N) - Match confidence 0-1
- `matched_expense_categoryDate` (S) - Matched invoice categoryDate
- `matched_expense_userId` (S) - Matched invoice userId
- `match_type` (S) - 1_1, N_1, 1_N
- `hungarian_score` (N) - Hungarian algorithm composite score
- `payroll_date` (S) - For payroll reconciliation

---

### 6. Vendors_AI

**Purpose:** AI-grouped vendor names from bank transactions. Tracks vendors before official provider creation. Enables fuzzy vendor name resolution for bank reconciliation.

| Property | Value |
|---|---|
| Table Name | `Vendors_AI` |
| PK | `locationId` (STRING) |
| SK | `vendor_ai_id` (STRING) - format: `VAI-{hash}` |
| Stream | NEW_AND_OLD_IMAGES |
| PITR | Enabled |

**GSIs (3):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByNormalizedName | locationId | normalized_name | Fuzzy name lookup |
| 2 | ByMatchStatus | locationId | match_status | Find unmatched vendors |
| 3 | ByProviderCif | locationId | matched_provider_cif | Lookup by resolved CIF |

**Key Fields:**
- `vendor_ai_id` (S) - Unique vendor group ID
- `normalized_name` (S) - Normalized vendor name (lowercased, stripped)
- `display_name` (S) - Human-readable name
- `aliases` (L) - List of bank description variations seen
- `match_status` (S) - UNMATCHED, MATCHED, CONFIRMED
- `matched_provider_cif` (S) - Linked provider CIF (when matched)
- `transaction_count` (N) - How many transactions matched this vendor
- `last_seen` (S) - Last transaction date

---

### 7. Clients_AI

**Purpose:** Same as Vendors_AI but for INCOME transactions. AI-grouped client names from bank credits.

| Property | Value |
|---|---|
| Table Name | `Clients_AI` |
| PK | `locationId` (STRING) |
| SK | `client_ai_id` (STRING) - format: `CAI-{hash}` or `CCR-{hash}` |
| Stream | NEW_AND_OLD_IMAGES |
| PITR | Enabled |

**GSIs (3):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByNormalizedName | locationId | normalized_name | Fuzzy name lookup |
| 2 | ByMatchStatus | locationId | match_status | Find unmatched clients |
| 3 | ByCustomerCif | locationId | matched_customer_cif | Lookup by resolved CIF |

---

### 8. Reconciliation_Suggestions

**Purpose:** Complex reconciliation suggestions (1-N, N-1 matches). Auto-resolves when involved transactions/invoices are reconciled by other paths.

| Property | Value |
|---|---|
| Table Name | `Reconciliation_Suggestions` |
| PK | `locationId` (STRING) |
| SK | `suggestionId` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |
| PITR | Enabled |

**GSIs (3):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByTransaction | locationId_transactionSK | createdAt | Find suggestions for a transaction |
| 2 | ByInvoice | locationId_invoiceCategoryDate | createdAt | Find suggestions for an invoice |
| 3 | ByStatus | locationId | status_createdAt | List pending suggestions |

---

### 9. Supplier_Payment_Patterns

**Purpose:** Historical payment timing patterns per supplier. Used by temporal guardrails to detect anomalies in reconciliation timing.

| Property | Value |
|---|---|
| Table Name | `Supplier_Payment_Patterns` |
| PK | `locationId` (STRING) |
| SK | `supplierKey` (STRING) - format: `CIF#{cif}#PATTERN` |
| TTL | `ttl` attribute (auto-cleanup after 1 year) |
| PITR | Enabled |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByCif | normalizedCif | locationId | Cross-location CIF lookup |

---

## Banking (GoCardless)

### 10. Gocardless_Connections

**Purpose:** Bank account connections via GoCardless/Nordigen open banking. Stores connection metadata, IBAN, status.

| Property | Value |
|---|---|
| Table Name | `Gocardless_Connections` |
| PK | `locationId` (STRING) |
| SK | `connectionId` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (2):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ActiveConnectionsByLocation | locationId | isActive | Filter active connections |
| 2 | ConnectionsByAccountKey | accountKey | connectionId | Lookup by canonical account |

**Key Fields:**
- `connectionId` (S) - GoCardless requisition ID
- `accountKey` (S) - Canonical account identifier
- `isActive` (S) - "true" / "false"
- `iban` (S) - Bank IBAN
- `institutionId` (S) - Bank institution code
- `institutionName` (S) - Bank name
- `status` (S) - Connection status

---

### 11. Gocardless_User_Agreements

**Purpose:** User consent agreements for GoCardless bank access.

| Property | Value |
|---|---|
| Table Name | `Gocardless_User_Agreements` |
| PK | `locationId` (STRING) |
| SK | `agreementId` (STRING) |

---

### 12. GC_Transactions_By_Account

**Purpose:** Cached bank transactions from GoCardless, keyed by bank account. Idempotency layer to avoid re-fetching.

| Property | Value |
|---|---|
| Table Name | `GC_Transactions_By_Account` |
| PK | `accountId` (STRING) |
| SK | `SK` (STRING) - composite key |
| Stream | None |

**No GSIs.**

---

### 13. GC_Balances_By_Account

**Purpose:** Cached bank balance snapshots. Auto-expired via TTL.

| Property | Value |
|---|---|
| Table Name | `GC_Balances_By_Account` |
| PK | `accountId` (STRING) |
| SK | `SK` (STRING) |
| Stream | None |
| TTL | `expiresAt` |

---

## Providers & Products

### 14. Providers

**Purpose:** Provider (supplier) master data per location. One item per supplier CIF per location. Created/updated during invoice OCR.

| Property | Value |
|---|---|
| Table Name | `Providers` |
| PK | `locationId` (STRING) |
| SK | `cif` (STRING) - Supplier CIF/NIF |
| Stream | NEW_AND_OLD_IMAGES |

**No GSIs.**

**Key Fields:**
- `name` (S) - Provider name
- `cif` (S) - CIF/NIF
- `address` (S) - Address
- `categories` (L) - Historical categories assigned
- `last_invoice_date` (S) - Last invoice date seen
- `invoice_count` (N) - Total invoices from this provider

---

### 15. Customers

**Purpose:** Customer master data per location. Similar to Providers but for income invoice clients.

| Property | Value |
|---|---|
| Table Name | `Customers` |
| PK | `locationId` (STRING) |
| SK | `cif` (STRING) |
| Stream | NEW_AND_OLD_IMAGES (for Client_AI_Matcher trigger) |

**No GSIs.**

---

### 16. Provider_Products

**Purpose:** Products catalog per provider. Tracks all products supplied by each vendor, with pricing history, categories, and stock metadata.

| Property | Value |
|---|---|
| Table Name | `Provider_Products` |
| PK | `providerId` (STRING) - format: `{locationId}#{cif}` |
| SK | `productId` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (4):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | LocationProductsIndex | locationId | productName | Search products by location |
| 2 | ProviderCifIndex | providerCif | locationId | Search by provider CIF |
| 3 | ProviderNameIndex | providerName | locationId | Search by provider name |
| 4 | CategoryIndex | locationId | category | Filter products by category |

**Key Fields:**
- `productName` (S) - Product name
- `providerCif` (S) - Supplier CIF
- `providerName` (S) - Supplier name
- `locationId` (S) - Location
- `category` (S) - Product category
- `unit` (S) - Unit of measure
- `last_price` (N) - Last known price
- `price_history` (L) - Historical prices with dates
- `iva_rate` (N) - VAT rate for this product

---

### 17. Suppliers

**Purpose:** Legacy suppliers table. Being replaced by Providers.

| Property | Value |
|---|---|
| Table Name | `Suppliers` |
| PK | `locationId` (STRING) |
| SK | `CIF` (STRING) |

---

### 18. Providers_General

**Purpose:** General provider catalog (cross-location).

| Property | Value |
|---|---|
| Table Name | `Providers_General` |
| PK/SK | Varies per construct |

---

### 19. Providers_Global

**Purpose:** National Spanish provider search (all public companies by CIF).

| Property | Value |
|---|---|
| Table Name | `Providers_Global` |
| PK/SK | Varies per construct |

---

## Accounting & P&L

### 20. Location_Accounting_Accounts

**Purpose:** Chart of accounts (plan contable) per location. Maps account codes to descriptions and search terms.

| Property | Value |
|---|---|
| Table Name | `Location_Accounting_Accounts` |
| PK | `locationId` (STRING) |
| SK | `accountCode` (STRING) - format: `ACC#0628000010000` |
| Stream | None |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | LocationSearchTermIndex | locationId | searchTerm | Name/NIF search for autocomplete |

**Key Fields:**
- `accountCode` (S) - Accounting code (PGC format)
- `accountName` (S) - Account description
- `searchTerm` (S) - Normalized search text
- `accountType` (S) - GASTO, INGRESO, PROVEEDOR, CLIENTE, IVA

---

### 21. Company_Accounting_Accounts

**Purpose:** Chart of accounts at company (CIF) level rather than location level. For multi-location companies sharing accounting.

| Property | Value |
|---|---|
| Table Name | `Company_Accounting_Accounts` |
| PK | `companyCif` (STRING) |
| SK | `accountCode` (STRING) |

---

### 22. Location_Custom_PnL

**Purpose:** Custom P&L entries per location. Manual income/expense entries for P&L that don't come from invoices (e.g., cash payments, adjustments).

| Property | Value |
|---|---|
| Table Name | `Location_Custom_PnL` |
| PK | `locationId` (STRING) |
| SK | `pnl_date_entry_id` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (2):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | LocationKindDateIndex | locationKindKey (`{loc}#{INGRESO|GASTO}`) | pnl_date | Filter by type + date |
| 2 | EntryIdIndex | entryId | locationId | Direct entry lookup |

**Key Fields:**
- `pnl_date` (S) - P&L date (YYYY-MM-DD)
- `entryId` (S) - Unique entry identifier
- `kind` (S) - "INGRESO" or "GASTO"
- `amount` (N) - Entry amount
- `category` (S) - Category label
- `description` (S) - Free-text description

---

### 23. Location_Budgets

**Purpose:** Budget targets per category, date, and location. For budget vs actual comparisons.

| Property | Value |
|---|---|
| Table Name | `Location_Budgets` |
| PK | `locationId` (STRING) |
| SK | `dateCategoryKey` (STRING) - format: `{YYYY-MM}#{category}` |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | CategoryByDateIndex | locationCategoryKey (`{loc}#{category}`) | dateKey | Query category budget over time |

**Key Fields:**
- `dateCategoryKey` (S) - Period + category composite
- `dateKey` (S) - Period key (YYYY-MM)
- `locationCategoryKey` (S) - `{locationId}#{category}`
- `budgetAmount` (N) - Target budget amount
- `category` (S) - Budget category

---

### 24. Location_Transaction_Tags

**Purpose:** Custom transaction tags per location for user-defined labeling.

| Property | Value |
|---|---|
| Table Name | `Location_Transaction_Tags` |
| PK/SK | Per construct definition |

---

### 25. User_Invoice_Category_Configs

**Purpose:** Invoice category configuration per location. Defines expense categories and subcategories for invoice classification.

| Property | Value |
|---|---|
| Table Name | `User_Invoice_Category_Configs` |
| PK | `pk` (STRING) - format: `L#{locationId}` |
| SK | `sk` (STRING) - format: `CFG#ACTIVE` or `CFG#V#{version}` |
| Stream | NEW_AND_OLD_IMAGES |

**No GSIs.**

**Key Fields:**
- `categories` (L) - List of `{name, subcategories: [{name}]}` objects
- `version` (N) - Configuration version
- `updatedAt` (S) - Last update timestamp

---

## Delivery Note Reconciliation

### 26. Delivery_Notes_and_Invoices_tracker

**Purpose:** Tracks which delivery notes have been matched to which invoices. Central table for DN-Invoice reconciliation.

| Property | Value |
|---|---|
| Table Name | `Delivery_Notes_and_Invoices_tracker` |
| PK | `locationId` (STRING) |
| SK | `delivery_note_number` (STRING) |
| Stream | NEW_IMAGE |

**GSIs (4):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | invoiceIdIndex | invoice_id | delivery_note_number | Find DNs for an invoice |
| 2 | supplierCifDnFullIndex | supplier_cif | dn_search_full | Search by CIF + full DN number |
| 3 | supplierCifDnTail6Index | supplier_cif | dn_search_tail6 | Search by CIF + last 6 digits |
| 4 | supplierCifDnAlphanumericIndex | supplier_cif | dn_normalized_alphanumeric | Search by CIF + normalized alphanumeric |

---

### 27. Delivery_Notes_Reconciliation_Tracking

**Purpose:** Event-level tracking of DN reconciliation process. Stores reconciliation events, email status, and error flags.

| Property | Value |
|---|---|
| Table Name | `Delivery_Notes_Reconciliation_Tracking` |
| PK | `locationId` (STRING) |
| SK | `categoryDate` (STRING) |
| Stream | None |

**GSIs (6):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | InvoiceNumberIndex | invoice_number | createdAt | Events by invoice number |
| 2 | DeliveryNoteNumberIndex | delivery_note_number | createdAt | Events by DN number |
| 3 | ReconciliationStatusIndex | locationId | reconciliation_status_date | Filter by recon status |
| 4 | SupplierCifIndex | supplier_cif | categoryDate | Events by supplier |
| 5 | EmailStatusIndex | locationId | email_status_date | Email sending status |
| 6 | ErrorFlagIndex | locationId | has_errors_date (`{1|0}#{date}`) | Find error events |

---

### 28. Delivery_Notes_Processing_Status

**Purpose:** Processing pipeline status for delivery notes.

| Property | Value |
|---|---|
| Table Name | `Delivery_Notes_Processing_Status` |
| PK | `userId` (STRING) - **actually locationId** |
| SK | `docId` (STRING) |
| PITR | Enabled |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | status-index | status | updatedAt | Filter by pipeline status |

---

### 29. Invoice_Delivery_Stats

**Purpose:** Aggregated statistics for invoice-delivery note reconciliation.

---

### 30. Invoice_Delivery_Incidences

**Purpose:** Incidents and discrepancies found during DN-invoice reconciliation.

---

## Companies & Organizations

### 31. Companies

**Purpose:** Spanish company registry. Contains public company data (CIF, name, revenue, CNAE, city, province). Used for company lookup and supplier enrichment.

| Property | Value |
|---|---|
| Table Name | `Companies` |
| PK | `PK` (STRING) - format: `COMPANY#{company_id}` |
| SK | `SK` (STRING) - format: `METADATA` |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (10):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByNamePrefixIndex | company_name_prefix_4 | revenue (N) | Autocomplete by name prefix |
| 2 | ByNameWordIndex | name_word_1 | revenue (N) | Search by first word |
| 3 | ByNameWord2Index | name_word_2 | revenue (N) | Search by second word |
| 4 | ByFullNameIndex | company_name_normalized | revenue (N) | Exact name lookup |
| 5 | ByCityIndex | city | revenue (N) | Companies by city |
| 6 | ByProvinceIndex | province | revenue (N) | Companies by province |
| 7 | ByRevenueTierIndex | revenue_tier (MICRO/PEQUENA/MEDIANA/GRANDE) | revenue (N) | Filter by size |
| 8 | ByCnaeCodeIndex | cnae_code | revenue (N) | Filter by industry |
| 9 | ByCityAndNameIndex | city | company_name_normalized | City + name combo |
| 10 | ByCifIndex | cif | revenue (N) | Direct CIF lookup |

**Key Fields:**
- `cif` (S) - Company CIF/NIF
- `company_name_normalized` (S) - Normalized name
- `revenue` (N) - Annual revenue
- `revenue_tier` (S) - MICRO, PEQUENA, MEDIANA, GRANDE
- `cnae_code` (S) - CNAE industry code
- `city` (S) - Fiscal domicile city
- `province` (S) - Province

---

### 32. Company_Gestors

**Purpose:** Gestor (accountant/bookkeeper) to company relationships.

| Property | Value |
|---|---|
| Table Name | `Company_Gestors` |
| PK | `gestorId` (STRING) |
| SK | `companyCif` (STRING) |

---

### 33. Gestor_Providers

**Purpose:** Gestor to provider CIF relationships.

| Property | Value |
|---|---|
| Table Name | `Gestor_Providers` |
| PK | `gestorId` (STRING) |
| SK | `providerCif` (STRING) |

---

### 34. A3_Files_Queue

**Purpose:** Queue for A3 accounting software export files per gestor.

| Property | Value |
|---|---|
| Table Name | `A3_Files_Queue` |
| PK/SK | Per construct |

---

### 35. Organizations

**Purpose:** Multi-location tenant root entity.

| Property | Value |
|---|---|
| Table Name | `Organizations` |
| PK/SK | Per construct |

---

### 36. Organization_Members

**Purpose:** Organization-user membership relationships.

---

### 37. Organization_Locations

**Purpose:** Organization-location assignments.

---

## Employees

### 38. Employees

**Purpose:** Employee master data with payroll tracking aggregates. One item per employee NIF per location.

| Property | Value |
|---|---|
| Table Name | `Employees` |
| PK | `locationId` (STRING) |
| SK | `employeeNif` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (4):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | OrgCifEmployeeIndex | org_cif | employeeNif | Employees by company CIF |
| 2 | EmployeeNifIndex | employeeNif | locationId | Multi-location employee lookup |
| 3 | LocationStatusIndex | location_status_key (`{loc}#{status}`) | lastPayrollDate | Active employees sorted by last payroll |
| 4 | SocialSecurityIndex | socialSecurityNumber | locationId | Lookup by SS number |

**Key Fields:**
- `employeeNif` (S) - Employee NIF
- `org_cif` (S) - Company CIF
- `name` (S) - Employee name
- `socialSecurityNumber` (S) - SS number
- `status` (S) - ACTIVE / INACTIVE
- `lastPayrollDate` (S) - Most recent payroll date
- `totalGrossPaid` (N) - Cumulative gross salary
- `totalNetPaid` (N) - Cumulative net salary
- `payrollCount` (N) - Number of payrolls processed
- `averageNetSalary` (N) - Average net salary

---

### 39. Payroll_OCR_Tracking

**Purpose:** Payroll OCR processing pipeline status tracking.

---

## Analytics & Stats

### 40. Daily_Stats

**Purpose:** Daily POS (point of sale) statistics per location. Sales, covers, average ticket.

| Property | Value |
|---|---|
| Table Name | `Daily_Stats` |
| PK | `locationId` (STRING) |
| SK | `dayKey` (STRING) - format: `YYYY-MM-DD` |
| Stream | None |

**No GSIs.**

**Key Fields:**
- `totalSales` (N) - Total sales amount
- `totalCovers` (N) - Number of covers
- `averageTicket` (N) - Average ticket amount
- `paymentMethods` (M) - Sales breakdown by payment method

---

### 41. Monthly_Stats

**Purpose:** Monthly POS statistics (aggregated from daily).

| Property | Value |
|---|---|
| Table Name | `Monthly_Stats` |
| PK | `locationId` (STRING) |
| SK | `monthKey` (STRING) - format: `YYYY-MM` |

---

### 42. Cierre_Caja

**Purpose:** Cash register closing (cierre de caja) data. Daily Z report equivalent.

| Property | Value |
|---|---|
| Table Name | `Cierre_Caja` |
| PK | `locationId` (STRING) |
| SK | `fecha` (STRING) - format: `YYYY-MM-DD` |
| Stream | None |

**No GSIs.**

---

### 43. Daily_TPV_Reports

**Purpose:** Daily payment terminal (TPV) reports.

| Property | Value |
|---|---|
| Table Name | `Daily_TPV_Reports` |
| PK/SK | Per construct |

---

## Pipeline & OCR Tracking

### 44. Pipeline_Document_Tracking

**Purpose:** End-to-end document pipeline tracking. Follows each document from upload through OCR, normalization, to reconciliation with timestamps, costs, and metrics per stage.

| Property | Value |
|---|---|
| Table Name | `Pipeline_Document_Tracking` |
| PK | `locationId` (STRING) |
| SK | `docTrackKey` (STRING) - format: `{yyyy-mm-dd}#{createdAt}#{docId}` |
| Stream | NEW_AND_OLD_IMAGES |
| TTL | `ttl` (6 months auto-cleanup) |

**GSIs (8):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | StatusIndex | locationId | statusDate (`{status}#{date}`) | Filter by pipeline status |
| 2 | SupplierIndex | locationId | supplierDate (`{cif}#{date}`) | Filter by supplier |
| 3 | ErrorIndex | locationId | errorFlag_date (`{1|0}#{date}`) | Find errors |
| 4 | GlobalRecentIndex | globalKey ("GLOBAL") | createdAt | Admin dashboard - recent docs |
| 5 | DocIdIndex | docId | locationId | Lookup by document ID |
| 6 | OcrRequestIdIndex | ocr_awsRequestId | createdAt | Debug by AWS request ID |
| 7 | TransactionIndex | reconcile_transactionSK | createdAt | Find by reconciled transaction |
| 8 | InvoiceNumberIndex | invoiceNumberKey (`LOC#{loc}#INV#{num}`) | createdAt | Find by invoice number |

**Key Fields:**
- `docId` (S) - Document identifier
- `pipelineStatus` (S) - Current stage: UPLOADED, OCR_PROCESSING, NORMALIZED, RECONCILED, ERROR
- `ocr_duration_ms` (N) - OCR processing duration
- `normalize_duration_ms` (N) - Normalization duration
- `reconcile_duration_ms` (N) - Reconciliation duration
- `ai_cost_usd` (N) - AI processing cost
- `ocr_awsRequestId` (S) - AWS Lambda request ID for debugging

---

### 45. Pipeline_Stats

**Purpose:** Aggregated pipeline statistics (daily/weekly/monthly volumes, error rates, costs).

---

### 46-52. Other Tracking Tables

- **Invoices_OCR_Tracking** - Per-document OCR processing status
- **Invoices_OCR_Daily_Stats** - Daily OCR volume/success stats
- **Invoices_OCR_Dedup_Locks** - Idempotency locks for concurrent OCR
- **Document_Hashes** - SHA256 file-level duplicate detection (pre-OCR)
- **Invoice_Processing_Status** - Invoice processing pipeline state
- **Invoice_Learning** - Pattern learning from corrections
- **Delivery_Note_Learning** - DN pattern learning

---

## Document Indexes

### 53. Document_IBANs

**Purpose:** Inverted index of IBANs to documents. Enables efficient lookup of all documents containing a specific IBAN.

---

### 54. Document_Search_Index

**Purpose:** Universal inverted index for document search by tags, names, and other attributes.

---

## Testing & Quality

### 55-61. Test Platform Tables

- **Reconciliation_Datasets** - Saved test datasets for reconciliation algorithm
- **Reconciliation_Test_Runs** - Test execution results and metrics
- **OCR_Golden_Annotations** - Human-annotated ground truth for OCR
- **OCR_Test_Runs** - OCR accuracy test results
- **Stock_Golden_Annotations** - Ground truth for stock/product pipeline
- **Stock_Test_Runs** - Stock pipeline test results
- **Bank_Reconciliation_Debug_Index** - S3 key index for debug artifacts per transaction/invoice

---

## Stock & Inventory

### 62. Stock_Inventory

**Purpose:** Ingredient stock per location. Tracks current quantities, delivery note associations, and ingredient metadata.

| Property | Value |
|---|---|
| Table Name | `Stock_Inventory` |
| PK | `locationId` (STRING) |
| SK | `ingredientId` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (2 active, 1 commented out):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | DeliveryNoteNumberIndex | delivery_note_number | - | Find ingredients by DN number |
| 2 | NameIndex | name | - | Search by ingredient name |

**Key Fields:**
- `ingredientId` (S) - Unique ingredient identifier
- `name` (S) - Ingredient name
- `quantity` (N) - Current stock quantity
- `unit` (S) - Unit of measure (kg, l, units)
- `delivery_note_number` (S) - Last DN that modified stock
- `lastPrice` (N) - Last purchase price
- `averagePrice` (N) - Weighted average price
- `category` (S) - Ingredient category

---

### 63. Stock_Transfers

**Purpose:** Inter-location stock transfers with idempotency. Tracks transfer lifecycle from creation to completion.

| Property | Value |
|---|---|
| Table Name | `Stock_Transfers` |
| PK | `transferId` (STRING) |
| SK | None (simple PK table) |
| Stream | None |

**GSIs (3):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByStatusUpdatedAt | status | updatedAt | Filter by transfer status |
| 2 | ByFromLocTimestamp | fromLocationId | sentAt | Outgoing transfers by location |
| 3 | ByToLocTimestamp | toLocationId | sentAt | Incoming transfers by location |

**Key Fields:**
- `status` (S) - PENDING / APPLIED / COMPLETED / FAILED
- `fromLocationId` (S) - Origin location
- `toLocationId` (S) - Destination location
- `sentAt` (S) - Timestamp of transfer initiation
- `items` (L) - List of transferred products with quantities

---

### 64. Stock_Transfer_Product_Locks

**Purpose:** Locks for safe auto-creation of destination ingredients during stock transfers.

| Property | Value |
|---|---|
| Table Name | `Stock_Transfer_Product_Locks` |
| PK | `lockId` (STRING) - format: `{toLocationId}#{providerCif}#{productId}` |
| SK | None |
| TTL | `expiresAt` (epoch seconds) |

**No GSIs.**

---

## Escandallos (Recipes)

### 65. Escandallos

**Purpose:** Recipe definitions. Maps dishes (products) to their ingredient lists with quantities and costs.

| Property | Value |
|---|---|
| Table Name | `Escandallos` |
| PK | `locationId` (STRING) |
| SK | `productId` (STRING) |
| Stream | None |

**No GSIs.**

---

### 66. Escandallo_Pending_Ingredients

**Purpose:** Ingredients pending resolution during escandallo creation. Tracks unresolved ingredient references.

| Property | Value |
|---|---|
| Table Name | `Escandallo_Pending_Ingredients` |
| PK | `locationId` (STRING) |
| SK | `pendingId` (STRING) - format: `n#{name_normalized}#p#{uuid}` |
| Stream | None |

**No GSIs.**

---

### 67. Escandallo_Ingredient_Usage_Suggestions

**Purpose:** AI suggestions for which ingredients to use in which dishes, with accept/reject workflow.

| Property | Value |
|---|---|
| Table Name | `Escandallo_Ingredient_Usage_Suggestions` |
| PK | `locationId` (STRING) |
| SK | `suggestionId` (STRING) - format: `ING#{ingredientId}#DISH#{productId}` |
| TTL | `ttl` (epoch seconds) |

**GSIs (2):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByIngredientIdUpdatedAt | ingredientId | updatedAt | Suggestions for an ingredient |
| 2 | ByProductIdUpdatedAt | productId | updatedAt | Suggestions for a dish |

**Key Fields:**
- `status` (S) - suggested / accepted / rejected
- `ingredientId` (S) - Referenced ingredient
- `productId` (S) - Referenced dish/product

---

### 68. Escandallo_Ingredient_Debug_Index

**Purpose:** Debug trace index for escandallos AI. Maps S3 prefixes and metadata by ingredient for debugging.

| Property | Value |
|---|---|
| Table Name | `Escandallo_Ingredient_Debug_Index` |
| PK | `locationId` (STRING) |
| SK | `traceKey` (STRING) - format: `ING#{id}#TS#{iso}#K#{kind}#RID#{request_id}#{uuid}` |
| TTL | `ttl` (epoch seconds) |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | AwsRequestIdIndex | aws_request_id | createdAt | Debug by Lambda request ID |

---

### 69. Escandallos_AI_Executions

**Purpose:** End-to-end tracking of escandallos AI execution runs. Monitors processing state, timing, and results.

| Property | Value |
|---|---|
| Table Name | `Escandallos_AI_Executions` |
| PK | `pk` (STRING) - format: `LOC#{locationId}` |
| SK | `sk` (STRING) - format: `EXEC#{executionId}` |
| TTL | `ttl` (epoch seconds) |

**GSIs (3):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByExecutionIdStartedAt | executionId | startedAt | Lookup by execution ID |
| 2 | ByLocationStartedAt | pk | startedAt | Location execution history |
| 3 | ByStateUpdatedAt | state | updatedAt | Filter by state |

**Key Fields:**
- `state` (S) - PROCESSING / COMPLETED / COMPLETED_WITH_WARNINGS / ERROR
- `startedAt` (S) - Execution start time
- `duration_ms` (N) - Total duration

---

## POS & Products

### 70. Tabs

**Purpose:** POS tickets/tabs (individual sales transactions).

| Property | Value |
|---|---|
| Table Name | `Tabs` |
| PK | `id` (STRING) |
| SK | `locationId` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | DateLocationIndex | #datekey | id | Query tabs by date per location |

---

### 71. Location_Tabs

**Purpose:** Location-scoped tabs view (same data, different access pattern).

| Property | Value |
|---|---|
| Table Name | `Location_Tabs` |
| PK | `locationId` (STRING) |
| SK | `id` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | DateLocationIndex | #datekey | id | Query by date |

---

### 72. Product_Catalog

**Purpose:** POS product catalog (menu items) per location.

| Property | Value |
|---|---|
| Table Name | `Product_Catalog` |
| PK | `locationId` (STRING) |
| SK | `productId` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**No GSIs.**

---

### 73. Product_Data

**Purpose:** Product sales data with time-series capability. Tracks products sold within date ranges.

| Property | Value |
|---|---|
| Table Name | `Product_Data` |
| PK | `locationId` (STRING) |
| SK | `id` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByLocationDateTime | locationId | dateTime (YYYY-MM-DD) | Sales data by date range |

---

### 74. Product_Count

**Purpose:** Product stock counts per location and date.

| Property | Value |
|---|---|
| Table Name | `Product_Count` |
| PK | `locationId` (STRING) |
| SK | `date` (STRING) |
| Stream | NEW_AND_OLD_IMAGES |

**No GSIs.**

---

### 75. Products_Normalizer_Product_Errors

**Purpose:** Tracks product normalization errors during OCR. Rich indexing for debugging by document, supplier, and global frequency.

| Property | Value |
|---|---|
| Table Name | `Products_Normalizer_Product_Errors` |
| PK | `ingredientId` (STRING) |
| SK | `occurrenceKey` (STRING) |

**GSIs (4):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | DocIndex | docKey (`LOC#{userId}#DOC#{docId}`) | occurrenceKey | Errors by document |
| 2 | SupplierIndex | supplierKey (`LOC#{userId}#SUPP#{cif}`) | supplierSk | Errors by supplier |
| 3 | GlobalTopIndex | globalTopKey ("GLOBAL") | globalTopSk (padded count) | Most frequent errors |
| 4 | GlobalRecentIndex | globalRecentKey ("GLOBAL") | globalRecentSk | Recent errors |

---

## Other Tables

### 76. Users_Locations

**Purpose:** User-to-location associations. Maps which users have access to which locations.

| Property | Value |
|---|---|
| Table Name | `Users_Locations` |
| PK | `userId` (STRING) |
| SK | None |

**No GSIs.** Cross-account IAM role for Cognito trigger Lambda.

---

### 77. User_Locations_Data

**Purpose:** Location metadata (name, address, POS integration settings, organization link).

| Property | Value |
|---|---|
| Table Name | `User_Locations_Data` |
| PK | `locationId` (STRING) |
| SK | None |
| Stream | NEW_AND_OLD_IMAGES |

**GSIs (1):**

| # | GSI Name | PK | SK | Purpose |
|---|---|---|---|---|
| 1 | ByOrganizationId | organizationId | locationId | Locations by organization |

---

### 78. FX_Rates

**Purpose:** Cached currency exchange rates from Frankfurter API.

---

### 79. Licenses

**Purpose:** Client license management.

---

### 80. Langfuse_Prompts

**Purpose:** AI prompt versioning and metadata. Tracks prompt versions used across the platform.

---

### 81. User_Subscriptions

**Purpose:** Recurring payment subscriptions per user (SaaS billing).

---

### 82. Workers

**Purpose:** Restaurant staff/workers per location.

| Property | Value |
|---|---|
| Table Name | `Workers` |
| PK | `locationId` (STRING) |
| SK | `workerId` (STRING) |

**No GSIs.**

---

### 83. Workers_Shifts

**Purpose:** Staff shift records.

| Property | Value |
|---|---|
| Table Name | `Workers_Shifts` |
| PK | `workerId` (STRING) |
| SK | `shiftId` (STRING) |

**No GSIs.**

---

### 84. Talkier_AI_User_Chats

**Purpose:** AI assistant (Talkier) chat history per user.

| Property | Value |
|---|---|
| Table Name | `Talkier_AI_User_Chats` |
| PK | `userId` (STRING) |
| SK | `dateChatId` (STRING) |

**No GSIs.**

---

### 85. Talkiers_Reports

**Purpose:** AI-generated daily reports per location.

| Property | Value |
|---|---|
| Table Name | `Talkiers_Reports` |
| PK | `locationId` (STRING) |
| SK | `dayKey` (STRING) |

**No GSIs.**

---

## Summary: GSI Counts by Table

| Table | GSI Count | Stream | Notes |
|---|---|---|---|
| User_Expenses | 19 | Yes | Most GSIs - main financial table |
| User_Invoice_Incomes | 20 | Yes | Same as Expenses + ClientCif GSI |
| Payroll_Slips | 14 | Yes | Employee/org/workflow GSIs |
| Bank_Reconciliations | 12 | Yes | Multi-purpose reconciliation |
| Companies | 10 | Yes | Search-heavy (name, city, CNAE) |
| Pipeline_Document_Tracking | 8 | Yes | Debug and monitoring |
| Delivery_Notes_Reconciliation_Tracking | 6 | No | Event tracking |
| Delivery_Notes | 4 (+5 commented) | Yes | DN lifecycle |
| Delivery_Notes_and_Invoices_tracker | 4 | Yes | DN-Invoice matching |
| Provider_Products | 4 | Yes | Product catalog |
| Employees | 4 | Yes | Employee master |
| Vendors_AI | 3 | Yes | AI vendor resolution |
| Clients_AI | 3 | Yes | AI client resolution |
| Reconciliation_Suggestions | 3 | Yes | Complex matching |
| Location_Custom_PnL | 2 | Yes | Manual P&L entries |
| Gocardless_Connections | 2 | Yes | Bank connections |
| Location_Budgets | 1 | Yes | Budget targets |
| Location_Accounting_Accounts | 1 | No | Chart of accounts |
| Supplier_Payment_Patterns | 1 | No | Temporal patterns |
| Delivery_Notes_Processing_Status | 1 | No | DN processing |
| Providers | 0 | Yes | Simple key-value |
| Customers | 0 | Yes | Simple key-value |
| Daily_Stats | 0 | No | Simple analytics |
| Cierre_Caja | 0 | No | Simple closing data |

**Total: ~130+ tables, ~120+ GSIs across the platform.**
