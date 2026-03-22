# Financial AI Agent - Deep Study per Task

Complete analysis of data sources, optimal queries, essential fields, and external data requirements for each of the 12 financial agent tasks.

---

## TABLE OF CONTENTS

1. [General Data Model Overview](#1-general-data-model-overview)
2. [Task 1: Cierre Contable Mensual](#task-1-cierre-contable-mensual)
3. [Task 2: Conciliacion Inteligente con Explicacion](#task-2-conciliacion-inteligente-con-explicacion)
4. [Task 3: Reportes Financieros (P&L, Balance, Cashflow)](#task-3-reportes-financieros)
5. [Task 4: Deteccion de Errores Contables](#task-4-deteccion-de-errores-contables)
6. [Task 5: Auditoria Automatica de IVA](#task-5-auditoria-automatica-de-iva)
7. [Task 6: Control de Fraude / Anomalias](#task-6-control-de-fraude--anomalias)
8. [Task 7: Analisis de Gastos Accionable](#task-7-analisis-de-gastos-accionable)
9. [Task 8: Optimizacion de Proveedores](#task-8-optimizacion-de-proveedores)
10. [Task 9: Analisis de Rentabilidad Real](#task-9-analisis-de-rentabilidad-real)
11. [Task 10: Prediccion de Cashflow](#task-10-prediccion-de-cashflow)
12. [Task 11: Simulacion de Decisiones](#task-11-simulacion-de-decisiones)
13. [Task 12: Explicacion Tipo Humano](#task-12-explicacion-tipo-humano)

---

## 1. General Data Model Overview

### 1.1 Core Tables & Schemas

#### User_Expenses (Invoices - EXPENSE)
- **Table**: `User_Expenses`
- **PK**: `userId` (actually locationId - legacy naming)
- **SK**: `categoryDate` (format: `YYYY-MM-DD#invoiceid`)
- **Stream**: YES (NEW_IMAGE)
- **GSIs** (19 total):
  | GSI Name | PK | SK | Use Case |
  |----------|----|----|----------|
  | InvoiceNumberSupplierIndex | userId | invoice_supplier_id | Deduplicate by invoice+supplier |
  | UserIdInvoiceDateIndex | userId | invoice_date | Range queries by invoice date |
  | UserIdSupplierCifIndex | userId | supplier_cif | Filter by supplier |
  | UserIdPnlDateIndex | userId | pnl_date | P&L date range queries |
  | UserByReconStateDate | userId | recon_state_date | Filter by reconciliation state |
  | UserSupplierDateIndex | userSupplierKey | charge_date | Supplier+date queries |
  | UserIdInvoiceIdIndex | userId | invoiceid | Direct lookup by invoiceid |
  | UserNeedsReviewIndex | needsReviewPK | categoryDate | Pending review items |
  | UserByProcessingStatusIndex | processing_status | categoryDate | Filter by processing status |
  | UserWorkflowStateIndex | workflowStatePK | categoryDate | Workflow state filtering |
  | UserDisplayStateIndex | displayStatePK | categoryDate | Display state filtering |
  | UserNeedsExportIndex | needsExportPK | categoryDate | Pending export |
  | UserHasChangesIndex | hasChangesPK | categoryDate | Changed items |
  | UserPendingReconciliationVerificationIndex | reconciliationVerifiedPK | categoryDate | Pending recon verification |
  | UserNeedsSuenlaceExportIndex | needsSuenlaceExportPK | categoryDate | Suenlace export queue |
  | UserConciliationNeedsExportIndex | conciliationNeedsExportPK | categoryDate | Conciliation export |
  | UserReconciliationNeedsA3ExportIndex | reconciliationNeedsA3ExportPK | categoryDate | A3 reconciliation export |
  | UserA3ExportQueueIndex | queuedForA3ExportPK | categoryDate | A3 export queue |

#### User_Invoice_Incomes (Invoices - INCOME)
- Same structure as User_Expenses but for income invoices (facturas emitidas)
- **PK**: `userId` (locationId), **SK**: `categoryDate`

#### Delivery_Notes (Albaranes)
- **PK**: `userId` (locationId), **SK**: `categoryDate`
- **Stream**: YES (NEW_IMAGE)
- **GSIs**:
  | GSI Name | PK | SK |
  |----------|----|----|
  | DeliveryNoteNumberIndex | delivery_note_number | - |
  | UserSupplierDeliveryNoteIndex | userSupplierCombination (userId#supplierCIF) | delivery_note_number |
  | DeliveryNotesByProcessingStatusIndex | processing_status | categoryDate |
  | ProviderCIFReconciledIndex | supplier_cif | reconciled_date (reconciled#date) |

#### Payroll_Slips (Nominas)
- **PK**: `locationId`, **SK**: `categoryDate` (format: `YYYY-MM-DD#employee_nif`)
- **Fields**: locationId, categoryDate, org_cif, employee_nif, employee_date_key, period_key, org_employee_key, payroll_date, payroll_number, netAmount, employee_info, payroll_info, accountingEntries, verification, content_hash, etc.

#### Providers
- **PK**: `locationId`, **SK**: `cif`
- No GSIs
- **Fields**: nombre, provincia, facturas[], albaranes[], creditNotes[], trade_name, autoCreated, isCifFromAI, requiresReview

#### Provider_Products
- **PK**: `providerId` (locationId#cif), **SK**: `productId`
- **GSIs**: LocationProductsIndex, ProviderCifIndex, ProviderNameIndex, CategoryIndex
- **Fields**: productName, category, locationId, providerCif, providerName, price history

#### Bank_Reconciliations
- **PK**: `locationId`, **SK**: `SK`
- **Stream**: YES (NEW_AND_OLD_IMAGES on related tables)
- **GSIs** (11 total):
  | GSI Name | PK | SK | Use Case |
  |----------|----|----|----------|
  | PendingByDate | GSI1PK | GSI1SK | Pending reconciliations by date |
  | ByMatchedExpense | GSI2PK | GSI2SK | Find by matched expense |
  | TransactionsByCanonicalId | SK | locationId | Reverse lookup by txn ID |
  | LocationByStatusDate | locationId | status_date | Filter by status+date |
  | LocationDisplayStateIndex | displayStatePK ({locationId}#{displayState}) | displayStateUpdatedAt | Display state filtering |
  | ByVendorCif | vendor_cif | - | Find by vendor CIF |
  | LocationByPayrollDate | locationId | payroll_date | Payroll reconciliation |
  | LocationByVendorAiId | locationId | vendor_ai_id | Vendor AI mapping |
  | LocationByCustomerAiId | locationId | customer_ai_id | Customer AI mapping |
  | ByCustomerCif | customer_cif | - | Find by customer CIF |
  | HungarianReviewByLocation | hungarian_review_pk (LOC#PENDING_AI_REVIEW) | hungarian_review_type | Hungarian algo review items |

#### Vendors_AI (vendor name resolution)
- **PK**: `locationId`, **SK**: `vendor_ai_id`
- **Stream**: NEW_AND_OLD_IMAGES
- **GSIs**: ByNormalizedName (locationId, normalized_name), ByMatchStatus (locationId, match_status), ByProviderCif (locationId, matched_provider_cif)

#### Clients_AI (customer name resolution)
- **PK**: `locationId`, **SK**: `client_ai_id` (CAI-{hash} | CCR-{hash})
- **Stream**: NEW_AND_OLD_IMAGES
- **GSIs**: ByNormalizedName, ByMatchStatus, ByCustomerCif

#### Reconciliation_Suggestions
- **PK**: `locationId`, **SK**: `suggestionId`
- **Stream**: NEW_AND_OLD_IMAGES
- **GSIs**: ByTransaction (locationId_transactionSK, createdAt), ByInvoice (locationId_invoiceCategoryDate, createdAt), ByStatus (locationId, status_createdAt)

#### Supplier_Payment_Patterns
- **PK**: `locationId`, **SK**: `supplierKey` (CIF#{cif}#PATTERN)
- **TTL**: `ttl` attribute
- **GSI**: ByCif (normalizedCif, locationId)

#### GoCardless_Transactions_By_Account
- **PK**: `accountId`, **SK**: `SK`
- No GSIs, No stream
- Bank transaction data (amounts, dates, descriptions, counterparty info)

#### GoCardless_Balances_By_Account
- **PK**: `accountId`, **SK**: `SK`
- **TTL**: `expiresAt` attribute
- No GSIs

#### Location_Accounting_Accounts
- **PK**: `locationId`, **SK**: `accountCode` (e.g., ACC#0628000010000)
- **GSI**: LocationSearchTermIndex (locationId, searchTerm)

#### Location_Budgets
- **PK**: `locationId`, **SK**: `dateCategoryKey`
- **GSI**: CategoryByDateIndex (locationCategoryKey, dateKey)

#### Location_Custom_PnL
- **PK**: `locationId`, **SK**: `pnl_date_entry_id`
- **GSIs**: LocationKindDateIndex (locationKindKey, pnl_date), EntryIdIndex

#### Delivery_Notes_and_Invoices_tracker
- **PK**: `locationId`, **SK**: `delivery_note_number`
- Links delivery notes to invoices
- **GSIs**: invoiceIdIndex, supplierCifDnFullIndex, supplierCifDnTail6Index, supplierCifDnAlphanumericIndex

#### Delivery_Notes_Reconciliation_Tracking
- **PK**: `locationId`, **SK**: `categoryDate`
- **GSIs**: InvoiceNumberIndex, DeliveryNoteNumberIndex, ReconciliationStatusIndex, SupplierCifIndex, EmailStatusIndex, ErrorFlagIndex

#### Employees
- **PK**: `locationId`, **SK**: `employeeNif`
- **Stream**: YES
- **GSIs**:
  | GSI Name | PK | SK |
  |----------|----|----|
  | OrgCifEmployeeIndex | org_cif | employeeNif |
  | EmployeeNifIndex | employeeNif | locationId |
  | LocationStatusIndex | location_status_key ({locationId}#{status}) | lastPayrollDate |
  | SocialSecurityIndex | socialSecurityNumber | locationId |

#### Payroll_Slips
- **PK**: `locationId`, **SK**: `categoryDate` (YYYY-MM-DD#employee_nif)
- **Stream**: YES
- **GSIs** (13 total):
  | GSI Name | PK | SK |
  |----------|----|----|
  | LocationEmployeeDateIndex | locationId | employee_date_key (EMP#{nif}#DATE#{date}) |
  | OrgCifPeriodIndex | org_cif | period_key (PERIOD#{yyyy-mm}#EMP#{nif}) |
  | OrgEmployeeIndex | org_employee_key ({cif}#EMP#{nif}) | payroll_date |
  | LocationNeedsReviewIndex | needsReviewPK | categoryDate |
  | LocationNeedsExportIndex | needsExportPK | categoryDate |
  | LocationWorkflowStateIndex | workflowStatePK | categoryDate |
  | LocationDisplayStateIndex | displayStatePK | categoryDate |
  | NeedsReviewIndex | needsReview ("true"/"false") | categoryDate |
  | LocationPendingReconciliationVerificationIndex | reconciliationVerifiedPK | categoryDate |
  | LocationNeedsSuenlaceExportIndex | needsSuenlaceExportPK | categoryDate |
  | LocationConciliationNeedsExportIndex | conciliationNeedsExportPK | categoryDate |
  | LocationReconciliationNeedsA3ExportIndex | reconciliationNeedsA3ExportPK | categoryDate |
  | LocationA3ExportQueueIndex | queuedForA3ExportPK | categoryDate |

#### Payroll_OCR_Tracking
- **PK**: `locationId`, **SK**: `categoryDate` ({payroll_date}#{employee_nif})
- **GSIs**: LocationIdCreatedAtIndex, LocationIdEmployeeNifIndex (employee_nif_date), LocationIdDocKindIndex (documentKind_date), LocationIdTimeoutIndex (timeout_date), LocationIdNeedsReviewIndex (needsReview_date)

#### Companies
- **PK**: `PK` (COMPANY#{company_id}), **SK**: `SK` (METADATA)
- **Stream**: YES
- **GSIs** (10 total): ByNamePrefixIndex, ByNameWordIndex, ByNameWord2Index, ByFullNameIndex, ByCityIndex, ByProvinceIndex, ByRevenueTierIndex, ByCnaeCodeIndex, ByCityAndNameIndex, ByCifIndex
- All GSIs use revenue (NUMBER) as SK for sorting by company size

#### Invoice_Delivery_Stats
- **PK**: `locationId`, **SK**: `statsPeriod`
- **GSI**: ProviderStatsIndex (providerId, statsPeriod)

#### Invoice_Delivery_Incidences
- **PK**: `locationId`, **SK**: `incidenceId`
- **GSIs**: InvoiceIdIndex (invoiceId, createdAt), StatusLocationIndex (status, locationId#createdAt)

#### Invoices_OCR_Daily_Stats
- **PK**: `scopeKey` (GLOBAL | LOC#{locationId}), **SK**: `date`
- **GSI**: DateIndex (date, scopeKey)
- Tracks doc_counts, page_counts (invoices + payroll)

#### Daily_Stats / Monthly_Stats
- **PK**: `locationId`, **SK**: `dayKey` / `monthKey`
- No GSIs, No stream
- Pre-aggregated metrics

#### Cierre_Caja (cash register closing)
- **PK**: `locationId`, **SK**: `fecha`
- No GSIs, No stream

#### Stock_Inventory
- **PK**: `locationId`, **SK**: `ingredientId`
- **Stream**: YES
- **GSIs**: DeliveryNoteNumberIndex (delivery_note_number), NameIndex (name)

#### Inventory_Stats / Inventory_Current
- **PK**: `locationId`, **SK**: `sk`
- No GSIs, No stream

#### Suppliers (legacy)
- **PK**: `locationId`, **SK**: `CIF`
- No GSIs, No stream

---

### 1.2 Bank Transaction Item (Bank_Reconciliations table)

Each bank transaction is stored in Bank_Reconciliations with SK=`MTXN#{bookingDate}#{transactionId}`:

**Identity:**
- `transactionId` - Unique ID from GoCardless (or hash)
- `bookingDate` - Transaction booking date (YYYY-MM-DD)
- `valueDate` - Fecha valor (preferred for FX)
- `accountId` - GoCardless account ID

**Financial:**
- `amount` - Decimal (negative=expense, positive=income)
- `currency` - ISO currency code
- `merchant` - Merchant name from bank
- `description` - Transaction description
- `category` - Bank-assigned category

**Reconciliation Result (when matched):**
- `reconciled` - Boolean
- `status` - "PENDING" | "MATCHED" | "REVIEW_NEEDED"
- `matched_invoice_id` - Full invoice ID (locationId#categoryDate)
- `matched_invoice_categoryDate`
- `matched_vendor_name` / `matched_vendor_cif` (expenses)
- `matched_customer_name` / `matched_customer_cif` (income)
- `matched_document_source` - "User_Expenses" | "User_Invoice_Incomes"
- `reconciliation_date` - ISO timestamp
- `reconciliation_confidence` - Composite score (0-1)
- `reconciliation_explanation`
- `reconciled_document_amount`
- `status_date` - "MATCHED#{bookingDate}" for GSI

**Score Breakdown:**
- `reconciliation_score_breakdown`:
  - `amount_score` (0-1)
  - `temporal_score` (0-1)
  - `counterparty_score` (0-1)
  - `composite_score` (weighted geometric mean)
  - `amount_diff_pct`

**Quality Flags:**
- `reconciliation_amount_mismatch` - Boolean
- `reconciliation_amount_diff_pct`
- `reconciliation_low_counterparty_score`

**FX (cross-currency):**
- `matched_invoice_fx`: {invoice_currency, transaction_currency, fx_date, market_rate, implied_rate}

**Accounting on reconciliation:**
- `conciliationLineEntries` - Complete accounting entries
- `conciliationSource` - "HUNGARIAN_BATCH_RECONCILIATION"
- `bank_account_type`, `bank_account_concept`

**Hungarian Algorithm Scoring (for reference):**
- Amount weight: 45%, Temporal: 35%, Vendor: 20%
- Min composite eligibility: 0.50
- Auto-reconcile confidence threshold: 0.80
- Amount diff auto-recon max: 0.5%
- Guards: G1 (amount>0.5%), G2 (vendor<0.40), G3 (ambiguity gap<=3%), G4 (invoice ref contradiction)

---

### 1.3 Invoice Item - Complete Field Reference

Fields stored in User_Expenses / User_Invoice_Incomes (facturas de gasto e ingreso):

**Identity & Keys:**
- `userId` (PK - actually locationId)
- `categoryDate` (SK - YYYY-MM-DD#invoiceid)
- `invoiceid` (UUID)
- `kind` ("EXPENSE" | "INCOME")
- `invoice_supplier_id` (invoice_number#supplier_cif)

**Supplier/Client Info:**
- `supplier` (name)
- `supplier_cif` (normalized CIF/VAT)
- `supplier_province`
- `client_name`, `client_cif`, `client_address` (for income invoices)

**Dates:**
- `invoice_date` (YYYY-MM-DD)
- `due_date` (YYYY-MM-DD)
- `pnl_date` (YYYY-MM-DD - for P&L queries)
- `charge_date` (due_date or invoice_date fallback)
- `period`

**Financial Amounts:**
- `importe` (base amount before tax)
- `total` (total with tax)
- `ivas` (list of VAT breakdowns: [{rate, base, amount}])
- `retencion` (withholding tax amount)
- `retencion_type` (type of withholding)
- `descuentos_generales` (general discounts list)
- `descuentos_generales_total` (total discounts)
- `currency` ({code, symbol, source, confidence})

**Accounting:**
- `category` (accounting category/type)
- `concept` (accounting concept)
- `gestorId` (accountant ID)
- `companyCif` (company CIF)
- `providerAccountCode`, `providerAccountName`
- `expenseAccountCode`, `expenseAccountName`
- `incomeAccountCode`, `incomeAccountName` (for INCOME)
- `vatAccountCode`, `vatAccountName`
- `customerAccountCode`, `customerAccountName`
- `accountingEntries` (list of journal entries: [{account, debit, credit, description}])
- `accountConceptModeUsed`, `accountConceptDecision`, `accountConceptSource`

**VAT Details:**
- `vatDeductibilityBucket` (FULL/PARTIAL/NONE)
- `vatTotalAmount`, `vatDeductibleAmount`, `vatNonDeductibleAmount`
- `vatOperationType` (NORMAL/INTRACOMUNITARIA/ISP/EXENTA)
- `reverseChargeVatRate`, `reverseChargeBaseAmount`, `reverseChargeVatAmount`
- `vatOperationAiType`, `vatOperationAiConfidence`

**Reconciliation:**
- `reconciled` (bool)
- `reconciliation_status` ("PENDING_PAYMENT"|"RECONCILED"|etc.)
- `reconciliation_state` ("UNRECONCILED"|"RECONCILED"|"PARTIAL")
- `matched_transaction_id`, `matched_transaction_ids`
- `reconciliation_confidence`, `reconciliation_explanation`
- `amount_due`, `amount_paid`, `amount_available`
- `fx_rate`, `fx_adjustment`
- `recon_state_date` (composite for GSI)

**Workflow State:**
- `processing_status` ("PENDING_PRODUCTS"|"COMPLETED"|"pending_user_review")
- `workflowState` ("INVOICE_PENDING_EXPORT"|"INVOICE_SENT"|"INVOICE_ERROR")
- `reconciliationState`, `reconciliationReviewState`
- `settlementMode`
- `a3InvoiceVerificationStatus`, `a3SettlementVerificationStatus`
- `needsReview` ("true"|"false")
- `needsReviewReason`, `needsReviewReasons[]`

**Products (in delivery notes/invoices):**
- `products` (list: [{name, quantity, unit, unit_price, total, category, vat_rate, ...}])
- `delivery_notes` (list of referenced delivery note numbers)

**Products & Line Items:**
- `all_products` (full product list with details: name, quantity, unit, unit_price, total, category, vat_rate)
- `products_total_base`, `products_total_with_discounts`
- `products_presence_classification` (AI classification of product presence)
- `delivery_notes` (referenced delivery note numbers)
- `delivery_notes_amounts`, `delivery_notes_amounts_summary`
- `delivery_notes_extraction_stats`, `delivery_notes_format_classification`
- `tables_markdown` (markdown representation of tables)
- `table_analysis_result`, `missing_fields_on_table`

**Documents:**
- `invoice_url` (S3 URI)
- `generated_images` (page images for frontend)
- `field_images` (cropped images of important fields)
- `originalFileName`
- `textract_text_url`, `textract_metadata`

**Credit Notes:**
- `isProviderCredit`, `providerCreditId`, `providerCreditAmount`, `providerCreditCurrency`
- `providerCreditConsumed`
- `rectifiedInvoiceReference` (for rectification invoices)

**Multi-Invoice Detection:**
- `multiInvoiceDetected`, `multiInvoiceSplitPlan`
- `multiInvoiceInvoicesCount`, `multiInvoiceUnassignedPages`

**Multi-Delivery Note (for DN docType):**
- `multiDeliveryNoteDetected`, `multiDeliveryNoteSplitPlan`
- `pendingUserReviewReason`, `pendingUserReviewDetails`
- `wrongClientGuardrail`

**Assets:**
- `hasAssets`, `assetsDetection`, `isMixedInvoice`
- `assetsTotalAmount`, `expenseTotalAmount`
- `assetsConfirmedByUser`, `assetsConfirmationStatus`

**IBANs:**
- `ibans` (list of detected IBANs with owner/role/confidence)

**Document Classification:**
- `documentClassification`, `documentKind` (invoice/credit_note/delivery_note)
- `documentKindConfidence`, `documentKindReasoning`

**Validation:**
- `mathTotalsCoherent` (bool), `mathTotalsDiff`
- `total_verification`, `validation_metadata`, `discounts_validation`

**AI/LLM Costs (useful for agent cost tracking):**
- `cost_ai_usd`, `cost_textract_usd`, `cost_total_usd`, `cost_per_page_usd`
- `llm_prompt_tokens`, `llm_completion_tokens`, `llm_total_tokens`

**Search & Indexing:**
- `search_tags` (deterministic + AI merged)
- `search_description` (AI-generated)

**Backup Systems (product extraction fallbacks):**
- `backup_option_a` / `backup_option_b` / `backup_option_c` (alternative extraction results)
- Each has: result, is_viable, artifacts, summary

**Deskew:**
- `deskew` (bool), `deskew_summary`

**Total: 200+ fields** (many conditional based on docType, flowKind, AI detections)

---

### 1.4 Payroll Item - Complete Field Reference

Fields stored in Payroll_Slips:

**Identity:**
- `locationId` (PK)
- `categoryDate` (SK - YYYY-MM-DD#employee_nif)
- `docId` (UUID)
- `org_cif` (company CIF)
- `employee_nif`
- `employee_date_key` (EMP#nif#DATE#date)
- `period_key` (PERIOD#YYYY-MM#EMP#nif)
- `org_employee_key` (cif#EMP#nif)

**Employee Info (employee_info map):**
- `name` (full name)
- `nif` (DNI/NIE)
- `ss_number` (social security number)
- `category` (professional category)
- `seniority_date`
- `contract_type`

**Payroll Financial Info (payroll_info map):**
- `gross_amount` (salario bruto)
- `net_amount` (liquido a percibir)
- `company_total_cost` (coste empresa total)
- `employee_ss_contribution` (SS trabajador)
- `company_ss_contribution` (SS empresa)
- `irpf_percentage` (% IRPF)
- `irpf_amount` (importe IRPF)
- `contribution_base` (base cotizacion)
- `pension_plan_amount`
- `in_kind_deduction_amount`
- `employee_ss_breakdown` ({contingencias_comunes, desempleo, formacion_profesional, mei})
- `company_name`, `company_nif`, `company_social_security_registration`
- `employee_category`, `employee_seniority`
- `period_label`, `period_start`, `period_end`, `period_days`
- `issue_date`, `issue_city`
- `payroll_number`

**Accounting:**
- `accountingEntries` (journal entries matching invoices format)
- `verification` (accounting verification result with golden rules)
  - `total_debit`, `total_credit`, `is_balanced`
  - `line_summaries` by type (GASTO_BRUTO, GASTO_SS_EMPRESA, RETENCION_IRPF, DEUDA_SS_TOTAL, LIQUIDO_A_PAGAR, etc.)
  - `errors` (list of {code, message, severity})
  - `golden_rules_passed`

**Accounting Entries (accountingEntries array):**
Each entry: `{accountCode, accountName, side ("DEBE"|"HABER"), amount, kind ("expense"|"withholding"|"liability"|"payment"|"other"), isNewAccount}`

**Accounting Line Types (from AI, used to build entries):**
GASTO_BRUTO, GASTO_SS_EMPRESA, RETENCION_IRPF, DEUDA_SS_TOTAL, LIQUIDO_A_PAGAR, GASTO_INDEMNIZACION, GASTO_DIETAS, DEDUCCION_EMBARGO, DEDUCCION_ANTICIPO, BONIFICACION_SS, DEDUCCION_PLAN_PENSIONES, COMPENSACION_ESPECIE, OTRO

**Verification (verification object):**
- `total_debit`, `total_credit`, `is_balanced`
- `line_summaries[]` - Aggregated by type
- `errors[]` - {code, message, severity (ERROR|WARNING|INFO)}
- `golden_rules_passed` - Boolean

**Duplicate Detection:**
- `content_hash` - SHA256 of (employee_nif + period + gross + net)
- `duplicate_status` - "ORIGINAL" | "DUPLICATE_EXACT" | "DUPLICATE_UPDATED" | "DUPLICATE_POSSIBLE"
- `duplicate_of_category_date`, `duplicate_reason`

**Workflow (same pattern as invoices):**
- `workflowState`, `workflowStatePK`
- `reconciliationState`, `reconciled`, `reconciliation_status`
- `needsReview`, `needsReviewPK`, `needsReviewReasons[]`
- Export flags (needsExportPK, needsSuenlaceExportPK, etc.)

---

## TASK 1: Cierre Contable Mensual

### Description
Automated monthly accounting close: verify all is reconciled, detect errors, generate adjustment journal entries, flag pending items.

### Data Sources Required

| Table | Query | GSI | Essential Fields |
|-------|-------|-----|-----------------|
| User_Expenses | All invoices for locationId in month | UserIdPnlDateIndex (userId, pnl_date BETWEEN start AND end) | invoiceid, supplier, supplier_cif, invoice_date, pnl_date, importe, total, ivas, retencion, category, concept, accountingEntries, reconciled, reconciliation_status, vatOperationType, vatDeductibilityBucket, workflowState, processing_status |
| User_Invoice_Incomes | All income invoices for month | Same pattern as expenses | Same fields as expenses (income side) |
| Payroll_Slips | All payrolls for month | Query PK=locationId, SK begins_with("YYYY-MM") | locationId, categoryDate, employee_nif, payroll_info.gross_amount, payroll_info.net_amount, payroll_info.company_total_cost, payroll_info.irpf_amount, payroll_info.employee_ss_contribution, payroll_info.company_ss_contribution, accountingEntries, verification, reconciled |
| Delivery_Notes | Unreconciled delivery notes | ProviderCIFReconciledIndex or main table query | userId, categoryDate, supplier, supplier_cif, total, reconciled, delivery_note_date |
| Bank_Reconciliations | All reconciliations for month | By locationId + date range | Match status, amounts, transaction details |
| GoCardless_Transactions | Bank transactions for month | By accountId + date range | amount, booking_date, remittance_info, counterparty, status |
| Location_Accounting_Accounts | Account plan | PK=locationId | accountCode, description, searchTerm |
| Location_Custom_PnL | Manual P&L entries | LocationKindDateIndex | pnl_date, kind (INGRESO/GASTO), amount, description |
| Location_Budgets | Monthly budgets | CategoryByDateIndex | Budget by category for comparison |
| Companies | Company data | PK=companyCif | Legal name, CIF, fiscal info |

### Optimal Query Strategy

```python
# 1. Get all expense invoices for the month
expenses = expenses_table.query(
    IndexName="UserIdPnlDateIndex",
    KeyConditionExpression=Key('userId').eq(location_id) &
                          Key('pnl_date').between('2026-03-01', '2026-03-31')
)

# 2. Get all income invoices
incomes = incomes_table.query(
    IndexName="UserIdPnlDateIndex",  # Same GSI pattern
    KeyConditionExpression=Key('userId').eq(location_id) &
                          Key('pnl_date').between('2026-03-01', '2026-03-31')
)

# 3. Get payrolls for the month
payrolls = payroll_table.query(
    KeyConditionExpression=Key('locationId').eq(location_id) &
                          Key('categoryDate').begins_with('2026-03')
)

# 4. Get unreconciled items
unreconciled = expenses_table.query(
    IndexName="UserByReconStateDate",
    KeyConditionExpression=Key('userId').eq(location_id) &
                          Key('recon_state_date').begins_with('UNRECONCILED#2026-03')
)

# 5. Get bank transactions
bank_txns = gc_transactions_table.query(
    KeyConditionExpression=Key('accountId').eq(account_id) &
                          Key('bookingDate').between('2026-03-01', '2026-03-31')
)

# 6. Get accounting plan
accounts = accounting_table.query(
    KeyConditionExpression=Key('locationId').eq(location_id)
)
```

### Fields to Send to AI (Minimized)

For each expense invoice, send ONLY:
```json
{
  "id": "categoryDate",
  "invoice_number": "F-2026-001",
  "supplier": "PROVEEDOR SA",
  "supplier_cif": "B12345678",
  "invoice_date": "2026-03-15",
  "pnl_date": "2026-03-15",
  "importe": 1000.00,
  "total": 1210.00,
  "ivas": [{"rate": 21, "base": 1000, "amount": 210}],
  "retencion": 0,
  "category": "Compras materias primas",
  "concept": "Alimentacion",
  "reconciled": true,
  "reconciliation_status": "RECONCILED",
  "vatOperationType": "NORMAL",
  "vatDeductibilityBucket": "FULL",
  "accountingEntries": [{"account": "600", "debit": 1000, "description": "Compras"}],
  "workflowState": "INVOICE_SENT",
  "processing_status": "COMPLETED"
}
```

For payrolls, send:
```json
{
  "employee_nif": "12345678A",
  "employee_name": "Juan Garcia",
  "payroll_date": "2026-03-31",
  "gross_amount": 2500.00,
  "net_amount": 1950.00,
  "company_total_cost": 3200.00,
  "irpf_amount": 350.00,
  "ss_employee": 158.75,
  "ss_company": 700.00,
  "is_balanced": true,
  "reconciled": false,
  "accountingEntries": [...]
}
```

### External Data Required from User

| Data | Why Needed | Priority |
|------|-----------|----------|
| Bank statements (if no GoCardless) | Reconcile transactions not in system | CRITICAL if no GC |
| Amortization schedule | Fixed asset depreciation entries | HIGH |
| Loan payments schedule | Interest vs principal split | HIGH |
| Prepaid expenses / accruals list | Period-end adjustments | MEDIUM |
| Inventory valuation (if not using Stock_Inventory) | COGS adjustments | MEDIUM |
| Tax calendar (modelo 303/111/115 dates) | Verify tax provisions | MEDIUM |
| Intercompany transactions | Eliminate if multi-entity | LOW |

### Agent Logic Flow
1. **Gather**: Query all 6 data sources in parallel
2. **Validate completeness**: Check for gaps (missing invoices, unprocessed docs)
3. **Check reconciliation**: Identify all unreconciled items
4. **Verify VAT**: Sum IVA soportado/repercutido, check deductibility
5. **Verify payroll**: Check all payrolls balanced, SS/IRPF totals match
6. **Check consistency**: Expenses vs bank, income vs bank
7. **Generate journal entries**: Adjustments, accruals, provisions
8. **Report**: Summary with pending items list

---

## TASK 2: Conciliacion Inteligente con Explicacion

### Description
Explain all unreconciled transactions and propose how to reconcile them, grouped by match probability.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| User_Expenses | Unreconciled invoices | UserByReconStateDate GSI: begins_with("UNRECONCILED") -> invoiceid, supplier, supplier_cif, total, invoice_date, due_date, charge_date |
| User_Invoice_Incomes | Unreconciled income | Same pattern |
| GoCardless_Transactions | Unmatched bank txns | accountId + date range -> amount, booking_date, value_date, remittance_info, counterparty_name, counterparty_iban |
| Vendors_AI | Vendor name mappings | locationId -> vendor_name, normalized_names, matched_cifs |
| Reconciliation_Suggestions | Existing suggestions | locationId -> suggestion pairs, confidence, status |
| Supplier_Payment_Patterns | Historical payment patterns | supplierCif -> avg_days_to_pay, typical_amounts, frequency |
| Bank_Reconciliations | Existing reconciliations | For reference of past matches |
| Providers | Supplier master data | locationId -> all providers with CIF, name, facturas[], albaranes[] |

### Optimal Query Strategy

```python
# 1. Unreconciled expenses
unreconciled_expenses = expenses_table.query(
    IndexName="UserByReconStateDate",
    KeyConditionExpression=Key('userId').eq(location_id) &
                          Key('recon_state_date').begins_with('UNRECONCILED#')
)

# 2. Unmatched bank transactions (filter: not yet matched)
bank_txns = gc_transactions_table.query(
    KeyConditionExpression=Key('accountId').eq(account_id) &
                          Key('bookingDate').between(start, end),
    FilterExpression=Attr('reconciled').ne(True)
)

# 3. Vendor mappings for name resolution
vendors = vendors_ai_table.query(
    KeyConditionExpression=Key('locationId').eq(location_id)
)

# 4. Payment patterns for temporal scoring
patterns = payment_patterns_table.query(
    KeyConditionExpression=Key('locationId').eq(location_id)
)
```

### Fields to Send to AI (Minimized)

For invoices:
```json
{
  "id": "categoryDate",
  "type": "expense",
  "invoice_number": "F-001",
  "supplier": "PROVEEDOR SA",
  "supplier_cif": "B12345678",
  "total": 1210.00,
  "invoice_date": "2026-03-01",
  "due_date": "2026-04-01",
  "category": "Alimentacion"
}
```

For bank transactions:
```json
{
  "txn_id": "abc123",
  "amount": -1210.00,
  "booking_date": "2026-03-28",
  "description": "TRANSFER PROVEEDOR SA",
  "counterparty": "PROVEEDOR SA",
  "counterparty_iban": "ES12..."
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Explanation of custom/recurring payments | Unknown periodic charges | HIGH |
| Credit card statements | Payments not via bank transfer | HIGH |
| Cash register closing data (Cierre_Caja) | Cash payments | MEDIUM |
| Petty cash records | Small untracked expenses | LOW |

### Agent Logic Flow
1. Fetch all unreconciled invoices + unmatched bank transactions
2. Run matching algorithm (amount, date proximity, vendor name)
3. Group results: HIGH confidence (>80%), MEDIUM (50-80%), LOW (<50%)
4. For each group, explain WHY the match is suggested
5. Detect duplicates (same amount, close dates, same vendor)
6. Flag anomalies (invoice without bank movement, bank movement without invoice)

---

## TASK 3: Reportes Financieros

### Description
Generate P&L, Balance Sheet, and Cash Flow reports for last 3 months with explanations.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| User_Expenses | Expenses by pnl_date | UserIdPnlDateIndex -> category, concept, importe, total, ivas, retencion, pnl_date, accountingEntries |
| User_Invoice_Incomes | Income by pnl_date | Same -> category, importe, total, ivas, pnl_date |
| Payroll_Slips | Payroll costs by month | PK=locationId, SK begins_with month -> payroll_info.gross_amount, .company_total_cost, .irpf_amount, .ss_company |
| Location_Custom_PnL | Manual P&L adjustments | LocationKindDateIndex -> kind, amount, description, pnl_date |
| Daily_Stats / Monthly_Stats | Pre-aggregated metrics | scopeKey, date -> revenue, costs |
| GoCardless_Balances | Bank balances | accountId -> balance, date |
| GoCardless_Transactions | Cash movements | For cashflow statement |
| Location_Budgets | Budgets for variance | CategoryByDateIndex -> budgeted vs actual |
| Cierre_Caja | Daily cash register | locationId + date -> cash_sales, card_sales, total |
| Stock_Inventory | Inventory valuation | locationId -> current stock value |

### Optimal Query Strategy

```python
# P&L: 3 parallel queries per month (expenses, income, payroll)
for month in ['2026-01', '2026-02', '2026-03']:
    start = f'{month}-01'
    end = f'{month}-31'

    expenses = expenses_table.query(
        IndexName="UserIdPnlDateIndex",
        KeyConditionExpression=Key('userId').eq(loc) & Key('pnl_date').between(start, end),
        ProjectionExpression='category,concept,importe,total,ivas,retencion,pnl_date'
    )

    incomes = incomes_table.query(
        IndexName="UserIdPnlDateIndex",
        KeyConditionExpression=Key('userId').eq(loc) & Key('pnl_date').between(start, end),
        ProjectionExpression='category,concept,importe,total,ivas,pnl_date'
    )

    payrolls = payroll_table.query(
        KeyConditionExpression=Key('locationId').eq(loc) & Key('categoryDate').begins_with(month),
        ProjectionExpression='payroll_info,employee_nif,payroll_date'
    )
```

### Fields to Send to AI (Aggregated, NOT raw items)

Pre-aggregate before sending to AI:
```json
{
  "period": "2026-03",
  "revenue": {
    "total": 45000.00,
    "by_category": {"Ventas": 40000, "Servicios": 5000}
  },
  "expenses": {
    "total": 35000.00,
    "by_category": {
      "Compras materias primas": 15000,
      "Nominas": 12000,
      "Alquileres": 3000,
      "Suministros": 2500,
      "Otros": 2500
    }
  },
  "payroll_summary": {
    "total_gross": 10000,
    "total_ss_company": 2000,
    "total_net": 7500,
    "employee_count": 5
  },
  "vat_summary": {
    "vat_collected": 9450,
    "vat_paid": 7350,
    "vat_balance": 2100
  },
  "bank_balance_start": 25000,
  "bank_balance_end": 32000,
  "previous_months": [{...}, {...}]
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Fixed asset register | Depreciation for P&L | HIGH |
| Loan schedules | Interest expense + debt for balance | HIGH |
| Equity/capital changes | Balance sheet accuracy | MEDIUM |
| Inventory count (if manual) | COGS adjustment | MEDIUM |
| Deferred revenue/expenses | Accrual adjustments | MEDIUM |

---

## TASK 4: Deteccion de Errores Contables

### Description
Detect accounting errors in last 6 months: wrong VAT, miscategorized expenses, assets treated as expenses, inconsistencies.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| User_Expenses | 6 months expenses | UserIdPnlDateIndex -> ALL accounting fields: category, concept, accountingEntries, ivas, vatOperationType, vatDeductibilityBucket, importe, total, retencion, supplier_cif, hasAssets, isMixedInvoice |
| User_Invoice_Incomes | 6 months income | Same pattern |
| Payroll_Slips | 6 months payrolls | verification.errors, verification.is_balanced, accountingEntries |
| Location_Accounting_Accounts | Account plan | accountCode, description -> validate codes used exist |
| User_Invoice_Category_Configs | Category config | Current category/account mappings |
| Delivery_Notes_Reconciliation_Tracking | DN reconciliation | ErrorFlagIndex -> errors in DN-invoice matching |
| Products_Normalizer_Product_Errors | Product errors | Errors in product normalization |
| Invoice_Delivery_Incidences | Incidences | Reconciliation problems between invoices and delivery notes |

### Optimal Query Strategy

```python
# Broad scan: 6 months of expenses with full accounting detail
expenses = []
for month in months_range:
    batch = expenses_table.query(
        IndexName="UserIdPnlDateIndex",
        KeyConditionExpression=Key('userId').eq(loc) & Key('pnl_date').between(start, end),
        ProjectionExpression='categoryDate,supplier,supplier_cif,category,concept,importe,total,ivas,retencion,retencion_type,vatOperationType,vatDeductibilityBucket,accountingEntries,hasAssets,isMixedInvoice,invoice_date,processing_status'
    )
    expenses.extend(batch['Items'])

# Payroll verification errors
payrolls = payroll_table.query(
    KeyConditionExpression=Key('locationId').eq(loc) & Key('categoryDate').between(start_6m, end),
    ProjectionExpression='categoryDate,employee_nif,verification,payroll_info.gross_amount,payroll_info.net_amount,accountingEntries'
)
```

### Fields to Send to AI (Summarized)

Send a structured error-detection checklist:
```json
{
  "invoices": [
    {
      "id": "cd1",
      "supplier": "X",
      "total": 5000,
      "category": "Suministros",
      "vat_rate": 21,
      "vat_type": "NORMAL",
      "vat_deductibility": "FULL",
      "has_assets": true,
      "retencion": 0,
      "accounting_entries_balanced": true
    }
  ],
  "common_errors_to_check": [
    "assets_as_expense",
    "wrong_vat_rate",
    "missing_withholding_on_professional_services",
    "vat_on_exempt_items",
    "duplicate_invoices",
    "category_inconsistency",
    "intercompany_not_eliminated"
  ]
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Chart of accounts (PGC) | Validate account codes | HIGH |
| Tax obligations calendar | Verify provisions timing | MEDIUM |
| Fixed assets policy (threshold) | Asset vs expense threshold | HIGH |
| Withholding tax rules per service type | Verify IRPF applied correctly | HIGH |

---

## TASK 5: Auditoria Automatica de IVA

### Description
Review VAT application: deductibility, intra-community, wrong rates, inspection risk.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| User_Expenses | All invoices with VAT detail | UserIdPnlDateIndex -> ivas, vatOperationType, vatDeductibilityBucket, vatTotalAmount, vatDeductibleAmount, vatNonDeductibleAmount, vatOperationAiType, reverseChargeVatRate, supplier_cif, supplier, category, importe, total |
| User_Invoice_Incomes | Income invoices with IVA repercutido | Same fields |
| Providers | Supplier origin (national/EU/non-EU) | locationId, cif -> provincia, trade_name (to detect foreign) |
| Companies | Company's own CIF | For self-supply detection |
| Invoices_OCR_Daily_Stats | Volume stats | Processed counts (to detect missing invoices) |

### Optimal Query Strategy

```python
# All expenses with VAT focus - last fiscal year
expenses_vat = expenses_table.query(
    IndexName="UserIdPnlDateIndex",
    KeyConditionExpression=Key('userId').eq(loc) & Key('pnl_date').between('2025-01-01', '2025-12-31'),
    ProjectionExpression='categoryDate,supplier,supplier_cif,ivas,vatOperationType,vatDeductibilityBucket,vatTotalAmount,vatDeductibleAmount,vatNonDeductibleAmount,reverseChargeVatRate,reverseChargeBaseAmount,importe,total,category,invoice_date'
)

# All income with IVA repercutido
incomes_vat = incomes_table.query(
    IndexName="UserIdPnlDateIndex",
    KeyConditionExpression=Key('userId').eq(loc) & Key('pnl_date').between('2025-01-01', '2025-12-31'),
    ProjectionExpression='categoryDate,client_cif,ivas,vatOperationType,importe,total,invoice_date'
)
```

### Fields to Send to AI

```json
{
  "period": "2025 fiscal year",
  "company_cif": "B12345678",
  "expenses_vat_summary": {
    "total_vat_soportado": 45000,
    "total_deductible": 42000,
    "total_non_deductible": 3000,
    "by_rate": {"21%": 38000, "10%": 5000, "4%": 2000},
    "by_operation_type": {
      "NORMAL": {"count": 450, "base": 180000, "vat": 37800},
      "INTRACOMUNITARIA": {"count": 5, "base": 10000, "reverse_charge_vat": 2100},
      "ISP": {"count": 2, "base": 5000},
      "EXENTA": {"count": 10, "base": 8000}
    },
    "flagged_items": [
      {"id": "cd1", "reason": "21% VAT on food (should be 10%)", "amount": 500},
      {"id": "cd2", "reason": "Deductible VAT on restaurant meal (non-deductible)", "amount": 42}
    ]
  },
  "income_vat_summary": {
    "total_vat_repercutido": 60000,
    "by_rate": {"21%": 50000, "10%": 10000}
  },
  "vat_balance": {
    "Q1": {"soportado": 11000, "repercutido": 15000, "to_pay": 4000},
    "Q2": {"soportado": 12000, "repercutido": 14000, "to_pay": 2000}
  }
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Modelo 303 filed amounts | Compare with system calculations | CRITICAL |
| Modelo 349 (intracomunitarias) | Verify intra-EU declarations | HIGH |
| Pro-rata percentage (if applicable) | Partial deductibility | HIGH |
| List of exempt activities | Validate exempt operations | MEDIUM |
| Vehicle usage % for business | Validate vehicle VAT deduction | MEDIUM |

---

## TASK 6: Control de Fraude / Anomalias

### Description
Detect anomalous behavior in expenses or suppliers: unusual price increases, duplicate invoices, out-of-pattern payments.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| User_Expenses | Historical expenses (12 months) | UserIdSupplierCifIndex per supplier -> total, importe, invoice_date, supplier_cif, invoice_number, products |
| Provider_Products | Product price history | providerId (loc#cif) -> productName, price, lastPrice, priceHistory |
| Providers | Supplier master | locationId -> all suppliers, creation date, auto-created flag |
| Delivery_Notes | DN for cross-reference | supplier_cif, total, delivery_note_date |
| Supplier_Payment_Patterns | Payment patterns | Typical amounts, frequency, days_to_pay |
| Document_Hashes | Duplicate files | file_hash -> detect same PDF uploaded twice |
| Invoices_OCR_Dedup_Locks | Dedup locks | Idempotency tracking |

### Optimal Query Strategy

```python
# Per supplier: all invoices for trending
for supplier_cif in active_suppliers:
    supplier_invoices = expenses_table.query(
        IndexName="UserIdSupplierCifIndex",
        KeyConditionExpression=Key('userId').eq(loc) & Key('supplier_cif').eq(supplier_cif),
        ProjectionExpression='categoryDate,total,importe,invoice_date,invoice_number,products'
    )

# Product prices by supplier
for provider_id in provider_ids:
    products = provider_products_table.query(
        KeyConditionExpression=Key('providerId').eq(provider_id),
        ProjectionExpression='productName,price,lastPrice,category'
    )

# Payment patterns
patterns = payment_patterns_table.query(
    KeyConditionExpression=Key('locationId').eq(loc)
)
```

### Fields to Send to AI

Pre-process anomaly detection BEFORE sending to AI. Send ONLY flagged items:
```json
{
  "anomalies_detected": [
    {
      "type": "PRICE_SPIKE",
      "supplier": "PROVEEDOR X",
      "product": "Aceite de oliva 5L",
      "old_price": 15.50,
      "new_price": 22.00,
      "increase_pct": 41.9,
      "invoice_date": "2026-03-15"
    },
    {
      "type": "DUPLICATE_INVOICE",
      "invoice_number": "F-2026-045",
      "supplier": "PROVEEDOR Y",
      "amount": 1500,
      "dates": ["2026-03-01", "2026-03-15"]
    },
    {
      "type": "UNUSUAL_AMOUNT",
      "supplier": "PROVEEDOR Z",
      "amount": 15000,
      "avg_amount": 2000,
      "std_dev": 500,
      "z_score": 26
    },
    {
      "type": "NEW_SUPPLIER_HIGH_AMOUNT",
      "supplier": "DESCONOCIDO SL",
      "first_invoice_date": "2026-03-10",
      "amount": 8000
    }
  ],
  "supplier_trends": [
    {
      "supplier": "PROVEEDOR X",
      "monthly_totals": [2000, 2100, 2050, 2200, 5000],
      "trend": "SPIKE_LAST_MONTH"
    }
  ]
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Approved supplier list | Detect unauthorized suppliers | HIGH |
| Spending approval thresholds | Flag over-limit purchases | MEDIUM |
| Employee expense policies | Validate expense claims | MEDIUM |

---

## TASK 7: Analisis de Gastos Accionable

### Description
Analyze expenses and identify concrete savings opportunities.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| User_Expenses | 12 months expenses | UserIdPnlDateIndex -> category, concept, supplier, supplier_cif, importe, total, invoice_date, products |
| Provider_Products | Product prices by supplier | ProductName, price, category, locationId |
| Providers | Supplier list | locationId, cif, nombre |
| Location_Budgets | Budgets | Compare actual vs budget per category |
| Monthly_Stats | Aggregated monthly | Pre-calculated totals |
| Delivery_Notes | Product-level costs | products list with quantities and prices |

### Optimal Query Strategy

```python
# 12 months aggregated by category
for month in last_12_months:
    expenses = expenses_table.query(
        IndexName="UserIdPnlDateIndex",
        KeyConditionExpression=Key('userId').eq(loc) & Key('pnl_date').between(start, end),
        ProjectionExpression='category,concept,supplier_cif,importe,total'
    )

# Product price comparison across suppliers
products = provider_products_table.query(
    IndexName="LocationProductsIndex",
    KeyConditionExpression=Key('locationId').eq(loc)
)

# Budgets
budgets = budgets_table.query(
    KeyConditionExpression=Key('locationId').eq(loc)
)
```

### Fields to Send to AI

```json
{
  "monthly_expenses_by_category": {
    "Alimentacion": [15000, 14500, 16000, 15200, ...],
    "Bebidas": [5000, 4800, 5200, ...],
    "Personal": [12000, 12000, 12500, ...],
    "Alquiler": [3000, 3000, 3000, ...],
    "Suministros": [2500, 2800, 2200, ...]
  },
  "top_suppliers_by_spend": [
    {"supplier": "MAKRO", "cif": "A28...", "12m_total": 85000, "category": "Alimentacion"},
    {"supplier": "MERCADONA", "cif": "A46...", "12m_total": 25000, "category": "Alimentacion"}
  ],
  "products_with_cheaper_alternatives": [
    {
      "product": "Aceite oliva 5L",
      "current_supplier": "PROVEEDOR A",
      "current_price": 22.00,
      "cheapest_supplier": "PROVEEDOR B",
      "cheapest_price": 18.50,
      "monthly_qty": 20,
      "monthly_saving": 70.00
    }
  ],
  "budget_vs_actual": [
    {"category": "Alimentacion", "budget": 14000, "actual": 16000, "variance": 2000, "pct": 14.3}
  ],
  "yoy_comparison": {
    "total_expenses_this_year": 250000,
    "total_expenses_last_year": 230000,
    "increase_pct": 8.7
  }
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Competitor/market price benchmarks | Compare supplier pricing | HIGH |
| Contract renewal dates | Timing for renegotiation | HIGH |
| Volume commitments/discounts | Evaluate consolidation opportunities | MEDIUM |

---

## TASK 8: Optimizacion de Proveedores

### Description
Identify which suppliers have raised prices and quantify impact.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| Provider_Products | Price history per product | providerId, productName, price, lastPrice, category |
| User_Expenses | Invoice history by supplier | UserIdSupplierCifIndex -> importe, total, invoice_date, products |
| Delivery_Notes | Delivery note products with prices | supplier_cif, products (name, qty, unit_price) |
| Providers | Supplier master data | locationId, cif, nombre, provincia |
| Makro_Products | Market reference prices | Product name, price (as benchmark) |
| Providers_General | General provider database | Reference pricing |

### Optimal Query Strategy

```python
# All products from all suppliers for this location
all_products = provider_products_table.query(
    IndexName="LocationProductsIndex",
    KeyConditionExpression=Key('locationId').eq(loc)
)

# Group by supplier, then compare prices over time
for supplier_cif in top_suppliers:
    invoices = expenses_table.query(
        IndexName="UserSupplierDateIndex",
        KeyConditionExpression=Key('userSupplierKey').eq(f'{loc}#{supplier_cif}') &
                              Key('charge_date').between(start, end)
    )

    delivery_notes = delivery_notes_table.query(
        IndexName="UserSupplierDeliveryNoteIndex",
        KeyConditionExpression=Key('userSupplierCombination').eq(f'{loc}#{supplier_cif}')
    )
```

### Fields to Send to AI

```json
{
  "suppliers_with_price_changes": [
    {
      "supplier": "DISTRIBUIDORA X",
      "cif": "B12345678",
      "products_changed": [
        {
          "product": "Tomate frito 2kg",
          "price_6m_ago": 3.50,
          "price_now": 4.20,
          "change_pct": 20.0,
          "monthly_qty": 50,
          "monthly_impact": 35.00,
          "annual_impact": 420.00
        }
      ],
      "total_monthly_impact": 120.00,
      "total_annual_impact": 1440.00
    }
  ],
  "total_price_increase_impact_monthly": 350.00,
  "total_price_increase_impact_annual": 4200.00,
  "alternative_suppliers_available": [...]
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Supplier contracts (terms, volumes) | Negotiate from position of knowledge | HIGH |
| Alternative supplier quotes | Compare pricing | HIGH |
| Purchase order history | Volume-based pricing optimization | MEDIUM |

---

## TASK 9: Analisis de Rentabilidad Real

### Description
Calculate true profitability including ALL costs (stock, payroll, expenses).

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| User_Invoice_Incomes | Revenue | Total income invoices |
| Cierre_Caja | Daily sales (POS) | Cash, card, total sales |
| User_Expenses | All costs | By category |
| Payroll_Slips | Staff costs | company_total_cost per employee |
| Stock_Inventory | Current stock value | Product quantities and values |
| Inventory_Stats | Inventory snapshots | Historical stock values |
| Inventory_Current | Current inventory state | Current stock by location |
| Delivery_Notes | COGS from delivery notes | Products received with costs |
| Location_Custom_PnL | Manual adjustments | Additional income/expense entries |
| Monthly_Stats | Pre-aggregated | Monthly summaries |

### Fields to Send to AI (Pre-aggregated)

```json
{
  "period": "2026-03",
  "revenue": {
    "pos_sales": 65000,
    "invoiced_income": 5000,
    "other_income": 1000,
    "total": 71000
  },
  "cogs": {
    "food_cost": 22000,
    "beverage_cost": 8000,
    "total": 30000,
    "food_cost_pct": 33.8,
    "stock_change": -500
  },
  "gross_margin": 41000,
  "gross_margin_pct": 57.7,
  "operating_expenses": {
    "payroll_total_cost": 18000,
    "rent": 3000,
    "utilities": 2500,
    "insurance": 500,
    "marketing": 800,
    "maintenance": 600,
    "other": 1500,
    "total": 26900
  },
  "ebitda": 14100,
  "ebitda_pct": 19.9,
  "financial": {
    "interest_expense": 200,
    "bank_fees": 150
  },
  "net_profit_before_tax": 13750,
  "net_margin_pct": 19.4,
  "employee_count": 8,
  "revenue_per_employee": 8875
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Rent/lease amount (if not invoiced) | Major fixed cost | CRITICAL |
| Loan interest rates | Financial cost | HIGH |
| Owner salary/drawings | True profitability | HIGH |
| Depreciation schedule | Non-cash cost | MEDIUM |
| Insurance premiums | Fixed cost | MEDIUM |

---

## TASK 10: Prediccion de Cashflow

### Description
Predict cash position for next 3 months.

### Data Sources Required

| Table | Query | Essential Fields |
|-------|-------|-----------------|
| GoCardless_Balances | Current bank balance | balance, date |
| GoCardless_Transactions | Historical cash flows (12m) | amount, booking_date, direction |
| User_Expenses | Pending invoices (not yet paid) | reconciled=false, total, due_date, supplier |
| User_Invoice_Incomes | Pending receivables | reconciled=false, total, due_date |
| Payroll_Slips | Future payroll commitments | net_amount per employee |
| Supplier_Payment_Patterns | Payment timing | avg_days, frequency |
| Location_Budgets | Future expected expenses | Budget by category/month |
| Cierre_Caja | Historical daily revenue | Daily sales pattern |

### Fields to Send to AI

```json
{
  "current_bank_balance": 45000,
  "balance_date": "2026-03-22",
  "pending_outflows": {
    "unpaid_invoices": [
      {"supplier": "X", "amount": 5000, "due_date": "2026-04-01"},
      {"supplier": "Y", "amount": 3000, "due_date": "2026-04-15"}
    ],
    "estimated_payroll_next_month": 18000,
    "estimated_ss_payment": 5000,
    "estimated_vat_payment": 4000,
    "recurring_fixed": {"rent": 3000, "utilities": 2500, "insurance": 500}
  },
  "pending_inflows": {
    "unpaid_receivables": [
      {"client": "A", "amount": 8000, "due_date": "2026-04-10"}
    ],
    "estimated_daily_revenue": 2200
  },
  "historical_monthly_cashflow": [
    {"month": "2025-10", "inflow": 68000, "outflow": 62000, "net": 6000},
    {"month": "2025-11", "inflow": 72000, "outflow": 65000, "net": 7000}
  ],
  "seasonality_factors": {
    "april": 0.95,
    "may": 1.05,
    "june": 1.15
  }
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Known future commitments | Capex, special payments | CRITICAL |
| Tax payment calendar | Quarterly VAT, IRPF | CRITICAL |
| Loan repayment schedule | Fixed outflows | HIGH |
| Expected seasonal events | Revenue adjustments | MEDIUM |
| Credit line availability | Liquidity buffer | MEDIUM |

---

## TASK 11: Simulacion de Decisiones

### Description
Simulate financial impact of decisions (price changes, cost reductions, etc.).

### Data Sources Required

Same as Task 9 (Rentabilidad Real) + Task 10 (Cashflow):
- Current P&L structure (revenues, costs by category)
- Product-level margins (from Provider_Products + menu pricing)
- Payroll structure (per employee costs)
- Fixed vs variable cost breakdown

### Fields to Send to AI

```json
{
  "current_state": {
    "monthly_revenue": 71000,
    "cogs_pct": 42.3,
    "payroll_pct": 25.4,
    "fixed_costs": 6000,
    "ebitda": 14100,
    "ebitda_pct": 19.9
  },
  "scenario": {
    "action": "increase_prices_5pct",
    "parameters": {
      "price_increase": 5,
      "expected_demand_elasticity": -0.1,
      "supplier_cost_reduction_target": "PROVEEDOR X",
      "cost_reduction_pct": 3
    }
  },
  "sensitivity_data": {
    "revenue_per_1pct_price_change": 710,
    "margin_per_1pct_cogs_change": 300,
    "breakeven_point": 38000
  }
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Menu/price list | Base for simulation | CRITICAL |
| Demand elasticity estimate | Revenue impact of price change | HIGH |
| Competitor pricing | Market context | MEDIUM |
| Capacity constraints | Max revenue potential | MEDIUM |

---

## TASK 12: Explicacion Tipo Humano

### Description
Narrative explanation of financial performance ("why did I earn less this month?").

### Data Sources Required

Combination of Tasks 3 + 7:
- P&L data for current and previous periods
- Category-level expense breakdown
- Revenue breakdown
- Key changes (new suppliers, price changes, headcount changes)

### Fields to Send to AI

```json
{
  "question": "Why did I earn less money this month?",
  "current_month": {
    "revenue": 65000,
    "total_expenses": 58000,
    "net_profit": 7000
  },
  "previous_month": {
    "revenue": 71000,
    "total_expenses": 55000,
    "net_profit": 16000
  },
  "key_changes": [
    {"type": "revenue_drop", "amount": -6000, "reason": "3 fewer working days (holidays)"},
    {"type": "expense_increase", "category": "Alimentacion", "amount": 2000, "reason": "Supplier X raised prices 15%"},
    {"type": "expense_increase", "category": "Personal", "amount": 1000, "reason": "New part-time hire"}
  ],
  "trend_3m": {
    "revenue": [68000, 71000, 65000],
    "expenses": [52000, 55000, 58000],
    "profit": [16000, 16000, 7000]
  }
}
```

### External Data Required from User
| Data | Why | Priority |
|------|-----|----------|
| Business events (renovations, holidays, events) | Context for anomalies | HIGH |
| Staff changes | Explain payroll variations | MEDIUM |
| Known market changes | Context for price changes | LOW |

---

## Summary: Query Patterns by Priority

### Most Reused Queries (Implement First)

1. **Expenses by date range**: `UserIdPnlDateIndex` (userId, pnl_date BETWEEN) -- Used by Tasks 1,3,4,5,7,9,12
2. **Expenses by supplier**: `UserIdSupplierCifIndex` (userId, supplier_cif) -- Used by Tasks 6,7,8
3. **Unreconciled items**: `UserByReconStateDate` (userId, recon_state_date begins_with "UNRECONCILED") -- Used by Tasks 1,2
4. **Payrolls by month**: Main table (locationId, categoryDate begins_with YYYY-MM) -- Used by Tasks 1,3,4,9,10
5. **Bank transactions**: GoCardless table (accountId, bookingDate BETWEEN) -- Used by Tasks 1,2,10
6. **Provider products**: LocationProductsIndex (locationId) -- Used by Tasks 6,7,8

### Projection Optimization

Always use ProjectionExpression to minimize read capacity:

| Use Case | Minimal ProjectionExpression |
|----------|------------------------------|
| Financial summary | `categoryDate,category,importe,total,ivas,retencion,pnl_date` |
| Reconciliation check | `categoryDate,supplier_cif,total,reconciled,reconciliation_status,invoice_date,due_date` |
| VAT audit | `categoryDate,ivas,vatOperationType,vatDeductibilityBucket,supplier_cif,total,importe` |
| Supplier analysis | `categoryDate,supplier_cif,supplier,total,importe,products,invoice_date` |
| Error detection | `categoryDate,category,concept,accountingEntries,ivas,total,importe,retencion,processing_status` |

### Data Volume Estimates

For a typical restaurant with ~50 invoices/month:
- 6 months: ~300 invoices, ~30 payrolls, ~200 delivery notes
- 12 months: ~600 invoices, ~60 payrolls, ~400 delivery notes
- Each invoice item: ~2-5 KB (without products), ~5-15 KB (with products)
- Total for 12 months: ~3-9 MB raw data

**Recommendation**: Pre-aggregate before sending to AI. Never send raw items for more than 1 month. For multi-month analysis, send aggregated summaries.
