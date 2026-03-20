# DynamoDB Tables Replication Guide

Complete specification to recreate Talky DynamoDB tables in another AWS account.
All tables use **PAY_PER_REQUEST** billing and all attribute types are **String (S)** unless noted otherwise.

---

## Global Settings (apply to ALL tables)

| Setting | Value |
|---|---|
| Billing Mode | PAY_PER_REQUEST (On-Demand) |
| All PK/SK types | String (S) unless explicitly marked as Number (N) |
| Table name prefix | Use your environment prefix (e.g. `Dev_`, `Pre_`, `Prod_`) |
| Removal Policy | RETAIN |

---

## 1. User_Expenses (Expense Invoices)

**Table Name:** `{env}_User_Expenses`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `userId` | S |
| Sort Key (SK) | `categoryDate` | S |

**Stream:** NEW_AND_OLD_IMAGES

### GSIs (19 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `InvoiceNumberSupplierIndex` | `userId` | S | `invoice_supplier_id` | S | ALL |
| 2 | `UserIdInvoiceDateIndex` | `userId` | S | `invoice_date` | S | ALL |
| 3 | `UserIdSupplierCifIndex` | `userId` | S | `supplier_cif` | S | ALL |
| 4 | `UserIdPnlDateIndex` | `userId` | S | `pnl_date` | S | ALL |
| 5 | `UserByReconStateDate` | `userId` | S | `recon_state_date` | S | ALL |
| 6 | `UserSupplierDateIndex` | `userSupplierKey` | S | `charge_date` | S | ALL |
| 7 | `UserIdInvoiceIdIndex` | `userId` | S | `invoiceid` | S | ALL |
| 8 | `UserNeedsReviewIndex` | `needsReviewPK` | S | `categoryDate` | S | ALL |
| 9 | `UserByProcessingStatusIndex` | `processing_status` | S | `categoryDate` | S | ALL |
| 10 | `UserWorkflowStateIndex` | `workflowStatePK` | S | `categoryDate` | S | ALL |
| 11 | `UserDisplayStateIndex` | `displayStatePK` | S | `categoryDate` | S | ALL |
| 12 | `UserNeedsExportIndex` | `needsExportPK` | S | `categoryDate` | S | ALL |
| 13 | `UserHasChangesIndex` | `hasChangesPK` | S | `categoryDate` | S | ALL |
| 14 | `UserPendingReconciliationVerificationIndex` | `reconciliationVerifiedPK` | S | `categoryDate` | S | ALL |
| 15 | `UserNeedsSuenlaceExportIndex` | `needsSuenlaceExportPK` | S | `categoryDate` | S | ALL |
| 16 | `UserConciliationNeedsExportIndex` | `conciliationNeedsExportPK` | S | `categoryDate` | S | ALL |
| 17 | `UserReconciliationNeedsA3ExportIndex` | `reconciliationNeedsA3ExportPK` | S | `categoryDate` | S | ALL |
| 18 | `UserA3ExportQueueIndex` | `queuedForA3ExportPK` | S | `categoryDate` | S | ALL |

**Notes on composite key patterns:**
- `userSupplierKey` = `"{userId}#{supplier_cif}"`
- `needsReviewPK` = `"{userId}#PENDING_REVIEW"`
- `workflowStatePK` = `"{userId}#{workflowState}"`
- `displayStatePK` = `"{userId}#{displayState}"`
- `needsExportPK` = `"{userId}#PENDING_EXPORT"`
- `hasChangesPK` = `"{userId}#HAS_CHANGES"`
- `reconciliationVerifiedPK` = `"{userId}#PENDING_RECONCILIATION_VERIFICATION"`
- `needsSuenlaceExportPK` = `"{userId}#PENDING_SUENLACE_EXPORT"`
- `conciliationNeedsExportPK` = `"{userId}#PENDING_CONCILIATION_EXPORT"`
- `reconciliationNeedsA3ExportPK` = `"{userId}#PENDING_RECONCILIATION_A3_EXPORT"`
- `queuedForA3ExportPK` = `"{userId}#IN_A3_EXPORT_QUEUE"`

---

## 2. User_Invoice_Incomes (Income Invoices)

**Table Name:** `{env}_User_Invoice_Incomes`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `userId` | S |
| Sort Key (SK) | `categoryDate` | S |

**Stream:** NEW_AND_OLD_IMAGES

### GSIs (19 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `InvoiceNumberSupplierIndex` | `userId` | S | `invoice_supplier_id` | S | ALL |
| 2 | `UserIdInvoiceDateIndex` | `userId` | S | `invoice_date` | S | ALL |
| 3 | `UserIdSupplierCifIndex` | `userId` | S | `supplier_cif` | S | ALL |
| 4 | `UserIdPnlDateIndex` | `userId` | S | `pnl_date` | S | ALL |
| 5 | `UserByReconStateDate` | `userId` | S | `recon_state_date` | S | ALL |
| 6 | `UserSupplierDateIndex` | `userSupplierKey` | S | `charge_date` | S | ALL |
| 7 | `UserIdClientCifIndex` | `userId` | S | `client_cif` | S | ALL |
| 8 | `UserIdInvoiceIdIndex` | `userId` | S | `invoiceid` | S | ALL |
| 9 | `UserNeedsReviewIndex` | `needsReviewPK` | S | `categoryDate` | S | ALL |
| 10 | `UserByProcessingStatusIndex` | `processing_status` | S | `categoryDate` | S | ALL |
| 11 | `UserWorkflowStateIndex` | `workflowStatePK` | S | `categoryDate` | S | ALL |
| 12 | `UserDisplayStateIndex` | `displayStatePK` | S | `categoryDate` | S | ALL |
| 13 | `UserNeedsExportIndex` | `needsExportPK` | S | `categoryDate` | S | ALL |
| 14 | `UserHasChangesIndex` | `hasChangesPK` | S | `categoryDate` | S | ALL |
| 15 | `UserPendingReconciliationVerificationIndex` | `reconciliationVerifiedPK` | S | `categoryDate` | S | ALL |
| 16 | `UserNeedsSuenlaceExportIndex` | `needsSuenlaceExportPK` | S | `categoryDate` | S | ALL |
| 17 | `UserConciliationNeedsExportIndex` | `conciliationNeedsExportPK` | S | `categoryDate` | S | ALL |
| 18 | `UserReconciliationNeedsA3ExportIndex` | `reconciliationNeedsA3ExportPK` | S | `categoryDate` | S | ALL |
| 19 | `UserA3ExportQueueIndex` | `queuedForA3ExportPK` | S | `categoryDate` | S | ALL |

**Differences vs User_Expenses:** Has extra GSI `UserIdClientCifIndex` (PK=userId, SK=client_cif). Same composite key patterns as User_Expenses.

---

## 3. Bank_Reconciliations (Bank Transactions)

**Table Name:** `{env}_Bank_Reconciliations`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `SK` | S |

**Stream:** NEW_AND_OLD_IMAGES

### GSIs (12 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `PendingByDate` | `GSI1PK` | S | `GSI1SK` | S | ALL |
| 2 | `ByMatchedExpense` | `GSI2PK` | S | `GSI2SK` | S | ALL |
| 3 | `TransactionsByCanonicalId` | `SK` | S | `locationId` | S | ALL |
| 4 | `LocationByStatusDate` | `locationId` | S | `status_date` | S | ALL |
| 5 | `LocationDisplayStateIndex` | `displayStatePK` | S | `displayStateUpdatedAt` | S | ALL |
| 6 | `ByVendorCif` | `vendor_cif` | S | _(none)_ | - | ALL |
| 7 | `LocationByPayrollDate` | `locationId` | S | `payroll_date` | S | ALL |
| 8 | `LocationByVendorAiId` | `locationId` | S | `vendor_ai_id` | S | ALL |
| 9 | `LocationByCustomerAiId` | `locationId` | S | `customer_ai_id` | S | ALL |
| 10 | `ByCustomerCif` | `customer_cif` | S | _(none)_ | - | ALL |
| 11 | `HungarianReviewByLocation` | `hungarian_review_pk` | S | `hungarian_review_type` | S | ALL |

**Notes:**
- `displayStatePK` = `"{locationId}#{displayState}"`
- `hungarian_review_pk` = e.g. `"LOC123#PENDING_AI_REVIEW"`

---

## 4. Payroll_Slips

**Table Name:** `{env}_Payroll_Slips`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `categoryDate` | S |

**Stream:** NEW_AND_OLD_IMAGES

### GSIs (13 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `LocationEmployeeDateIndex` | `locationId` | S | `employee_date_key` | S | ALL |
| 2 | `OrgCifPeriodIndex` | `org_cif` | S | `period_key` | S | ALL |
| 3 | `LocationNeedsReviewIndex` | `needsReviewPK` | S | `categoryDate` | S | ALL |
| 4 | `LocationNeedsExportIndex` | `needsExportPK` | S | `categoryDate` | S | ALL |
| 5 | `LocationWorkflowStateIndex` | `workflowStatePK` | S | `categoryDate` | S | ALL |
| 6 | `LocationDisplayStateIndex` | `displayStatePK` | S | `categoryDate` | S | ALL |
| 7 | `OrgEmployeeIndex` | `org_employee_key` | S | `payroll_date` | S | ALL |
| 8 | `NeedsReviewIndex` | `needsReview` | S | `categoryDate` | S | ALL |
| 9 | `LocationPendingReconciliationVerificationIndex` | `reconciliationVerifiedPK` | S | `categoryDate` | S | ALL |
| 10 | `LocationNeedsSuenlaceExportIndex` | `needsSuenlaceExportPK` | S | `categoryDate` | S | ALL |
| 11 | `LocationConciliationNeedsExportIndex` | `conciliationNeedsExportPK` | S | `categoryDate` | S | ALL |
| 12 | `LocationReconciliationNeedsA3ExportIndex` | `reconciliationNeedsA3ExportPK` | S | `categoryDate` | S | ALL |
| 13 | `LocationA3ExportQueueIndex` | `queuedForA3ExportPK` | S | `categoryDate` | S | ALL |

**Notes on composite key patterns:**
- `employee_date_key` = `"EMP#{employee_nif}#DATE#{payroll_date}"`
- `period_key` = `"PERIOD#{yyyy-mm}#EMP#{employee_nif}"`
- `needsReviewPK` = `"{locationId}#PENDING_REVIEW"`
- `needsExportPK` = `"{locationId}#PENDING_EXPORT"`
- `workflowStatePK` = `"{locationId}#{workflowState}"`
- `displayStatePK` = `"{locationId}#{displayState}"`
- `org_employee_key` = `"{org_cif}#EMP#{employee_nif}"`
- `reconciliationVerifiedPK` = `"{locationId}#PENDING_RECONCILIATION_VERIFICATION"`
- `needsSuenlaceExportPK` = `"{locationId}#PENDING_SUENLACE_EXPORT"`
- `conciliationNeedsExportPK` = `"{locationId}#PENDING_CONCILIATION_EXPORT"`
- `reconciliationNeedsA3ExportPK` = `"{locationId}#PENDING_RECONCILIATION_A3_EXPORT"`
- `queuedForA3ExportPK` = `"{locationId}#IN_A3_EXPORT_QUEUE"`

---

## 5. Delivery_Notes

**Table Name:** `{env}_Delivery_Notes`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `userId` | S |
| Sort Key (SK) | `categoryDate` | S |

**Stream:** NEW_IMAGE (note: different from default NEW_AND_OLD_IMAGES)

### GSIs (4 active)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `DeliveryNoteNumberIndex` | `delivery_note_number` | S | _(none)_ | - | ALL |
| 2 | `UserSupplierDeliveryNoteIndex` | `userSupplierCombination` | S | `delivery_note_number` | S | ALL |
| 3 | `DeliveryNotesByProcessingStatusIndex` | `processing_status` | S | `categoryDate` | S | ALL |
| 4 | `ProviderCIFReconciledIndex` | `supplier_cif` | S | `reconciled_date` | S | ALL |

**Notes:**
- `userSupplierCombination` = `"{userId}#{supplierCIF}"`
- `reconciled_date` = `"{TRUE|FALSE}#{delivery_note_date}"`

---

## 6. Employees

**Table Name:** `{env}_Employees`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `employeeNif` | S |

**Stream:** NEW_AND_OLD_IMAGES

### GSIs (4 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `OrgCifEmployeeIndex` | `org_cif` | S | `employeeNif` | S | ALL |
| 2 | `EmployeeNifIndex` | `employeeNif` | S | `locationId` | S | ALL |
| 3 | `LocationStatusIndex` | `location_status_key` | S | `lastPayrollDate` | S | ALL |
| 4 | `SocialSecurityIndex` | `socialSecurityNumber` | S | `locationId` | S | ALL |

**Notes:**
- `location_status_key` = `"{locationId}#{status}"`

---

## 7. Providers

**Table Name:** `{env}_Providers`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `cif` | S |

**Stream:** NEW_AND_OLD_IMAGES

**GSIs:** None

---

## 8. Customers

**Table Name:** `{env}_Customers`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `cif` | S |

**Stream:** NEW_AND_OLD_IMAGES

**GSIs:** None

---

## 9. Suppliers (Legacy Providers)

**Table Name:** `{env}_Suppliers`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `CIF` | S |

**Stream:** Disabled

**GSIs:** None

**IMPORTANT:** Note the SK is `CIF` (uppercase) vs `cif` (lowercase) in Providers/Customers.

---

## 10. Companies (Commercial Registry)

**Table Name:** `{env}_Companies`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `PK` | S |
| Sort Key (SK) | `SK` | S |

**Stream:** NEW_AND_OLD_IMAGES

**Key patterns:**
- PK: `"COMPANY#{company_id}"`
- SK: `"METADATA"`

### GSIs (10 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `ByNamePrefixIndex` | `company_name_prefix_4` | S | `revenue` | **N** | ALL |
| 2 | `ByNameWordIndex` | `name_word_1` | S | `revenue` | **N** | ALL |
| 3 | `ByNameWord2Index` | `name_word_2` | S | `revenue` | **N** | ALL |
| 4 | `ByFullNameIndex` | `company_name_normalized` | S | `revenue` | **N** | ALL |
| 5 | `ByCityIndex` | `city` | S | `revenue` | **N** | ALL |
| 6 | `ByProvinceIndex` | `province` | S | `revenue` | **N** | ALL |
| 7 | `ByRevenueTierIndex` | `revenue_tier` | S | `revenue` | **N** | ALL |
| 8 | `ByCnaeCodeIndex` | `cnae_code` | S | `revenue` | **N** | ALL |
| 9 | `ByCityAndNameIndex` | `city` | S | `company_name_normalized` | S | ALL |
| 10 | `ByCifIndex` | `cif` | S | `revenue` | **N** | ALL |

**IMPORTANT:** The `revenue` SK in GSIs 1-8 and 10 is type **Number (N)**, not String.

---

## 11. Organizations

**Table Name:** `{env}_Organizations`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `organizationId` | S |
| Sort Key (SK) | _(none)_ | - |

**Stream:** Disabled

**GSIs:** None

---

## 12. Organization_Locations

**Table Name:** `{env}_Organization_Locations`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `organizationId` | S |
| Sort Key (SK) | `locationId` | S |

**Stream:** Disabled

### GSIs (1 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `ByLocationId` | `locationId` | S | `organizationId` | S | ALL |

---

## 13. User_Invoice_Category_Configs

**Table Name:** `{env}_User_Invoice_Category_Configs`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `pk` | S |
| Sort Key (SK) | `sk` | S |

**Stream:** NEW_AND_OLD_IMAGES

**Key patterns:**
- pk: `"L#{locationId}"`
- sk: `"CFG#ACTIVE"` or `"CFG#V#{version}"`

**GSIs:** None

---

## 14. Document_Ibans (IBAN Search Index)

**Table Name:** `{env}_Document_Ibans`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `PK` | S |
| Sort Key (SK) | `SK` | S |

**Stream:** Disabled

**Key patterns:**
- PK: `"{userId}#IBAN#{iban_normalized}"`
- SK: `"DOC#{categoryDate}"`

### GSIs (1 total)

| # | Index Name | PK | PK Type | SK | SK Type | Projection |
|---|---|---|---|---|---|---|
| 1 | `UserIdIbanIndex` | `userId` | S | `iban_normalized` | S | ALL |

---

## 15. Daily_Stats

**Table Name:** `{env}_Daily_Stats`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `dayKey` | S |

**Stream:** Disabled

**GSIs:** None

---

## 16. Monthly_Stats

**Table Name:** `{env}_Monthly_Stats`

| Key | Attribute | Type |
|---|---|---|
| Partition Key (PK) | `locationId` | S |
| Sort Key (SK) | `monthKey` | S |

**Stream:** Disabled

**GSIs:** None

---

## Quick Reference: DynamoDB Limits to Watch

- **Max 20 GSIs per table** (User_Expenses has 18, User_Invoice_Incomes has 19 -- close to the limit)
- **Only 1 GSI can be created per deployment** (create tables first, then add GSIs incrementally if using CloudFormation)
- If creating via AWS Console or boto3 `create_table`, you can add up to 20 GSIs at creation time

---

## boto3 Creation Example (for reference)

```python
import boto3

dynamodb = boto3.client('dynamodb', region_name='eu-west-1')

# Example: Creating Daily_Stats
dynamodb.create_table(
    TableName='Dev_Daily_Stats',
    KeySchema=[
        {'AttributeName': 'locationId', 'KeyType': 'HASH'},
        {'AttributeName': 'dayKey', 'KeyType': 'RANGE'},
    ],
    AttributeDefinitions=[
        {'AttributeName': 'locationId', 'AttributeType': 'S'},
        {'AttributeName': 'dayKey', 'AttributeType': 'S'},
    ],
    BillingMode='PAY_PER_REQUEST',
)

# Example: Creating Bank_Reconciliations with GSIs
dynamodb.create_table(
    TableName='Dev_Bank_Reconciliations',
    KeySchema=[
        {'AttributeName': 'locationId', 'KeyType': 'HASH'},
        {'AttributeName': 'SK', 'KeyType': 'RANGE'},
    ],
    AttributeDefinitions=[
        {'AttributeName': 'locationId', 'AttributeType': 'S'},
        {'AttributeName': 'SK', 'AttributeType': 'S'},
        {'AttributeName': 'GSI1PK', 'AttributeType': 'S'},
        {'AttributeName': 'GSI1SK', 'AttributeType': 'S'},
        {'AttributeName': 'GSI2PK', 'AttributeType': 'S'},
        {'AttributeName': 'GSI2SK', 'AttributeType': 'S'},
        # ... add ALL attributes used in ANY GSI PK/SK
    ],
    GlobalSecondaryIndexes=[
        {
            'IndexName': 'PendingByDate',
            'KeySchema': [
                {'AttributeName': 'GSI1PK', 'KeyType': 'HASH'},
                {'AttributeName': 'GSI1SK', 'KeyType': 'RANGE'},
            ],
            'Projection': {'ProjectionType': 'ALL'},
        },
        {
            'IndexName': 'ByMatchedExpense',
            'KeySchema': [
                {'AttributeName': 'GSI2PK', 'KeyType': 'HASH'},
                {'AttributeName': 'GSI2SK', 'KeyType': 'RANGE'},
            ],
            'Projection': {'ProjectionType': 'ALL'},
        },
        # ... repeat for all GSIs
    ],
    BillingMode='PAY_PER_REQUEST',
    StreamSpecification={
        'StreamEnabled': True,
        'StreamViewType': 'NEW_AND_OLD_IMAGES',
    },
)
```

**IMPORTANT:** In `AttributeDefinitions`, you MUST declare EVERY attribute used as PK or SK in the table key schema OR in any GSI. You do NOT need to declare non-key attributes.
