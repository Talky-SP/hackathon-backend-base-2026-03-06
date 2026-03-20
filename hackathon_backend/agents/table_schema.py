"""
Registry of DynamoDB table schemas derived from the CDK constructs.

This gives the LLM enough context to write correct queries
without scanning the actual tables at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GSI:
    name: str
    partition_key: str
    sort_key: str | None = None
    pk_type: str = "S"
    sk_type: str = "S"


@dataclass
class TableSchema:
    table_name_pattern: str  # e.g. "{Stage}_User_Expenses"
    partition_key: str
    sort_key: str | None = None
    gsis: list[GSI] = field(default_factory=list)

    def resolve_name(self, stage: str) -> str:
        return self.table_name_pattern.replace("{Stage}", stage.capitalize())

    def describe(self, stage: str) -> str:
        lines = [
            f"Table: {self.resolve_name(stage)}",
            f"  PK: {self.partition_key} (S)",
        ]
        if self.sort_key:
            lines.append(f"  SK: {self.sort_key} (S)")
        for g in self.gsis:
            sk_part = f", SK={g.sort_key}({g.sk_type})" if g.sort_key else ""
            lines.append(f"  GSI {g.name}: PK={g.partition_key}({g.pk_type}){sk_part}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Table definitions — keep in sync with hackathon_backend/constructs/databases
# ---------------------------------------------------------------------------

TABLES: list[TableSchema] = [
    TableSchema(
        table_name_pattern="{Stage}_User_Expenses",
        partition_key="userId",
        sort_key="categoryDate",
        gsis=[
            GSI("InvoiceNumberSupplierIndex", "userId", "invoice_supplier_id"),
            GSI("UserIdInvoiceDateIndex", "userId", "invoice_date"),
            GSI("UserIdSupplierCifIndex", "userId", "supplier_cif"),
            GSI("UserIdPnlDateIndex", "userId", "pnl_date"),
            GSI("UserByReconStateDate", "userId", "recon_state_date"),
            GSI("UserSupplierDateIndex", "userSupplierKey", "charge_date"),
            GSI("UserIdInvoiceIdIndex", "userId", "invoiceid"),
            GSI("UserNeedsReviewIndex", "needsReviewPK", "categoryDate"),
            GSI("UserByProcessingStatusIndex", "processing_status", "categoryDate"),
            GSI("UserWorkflowStateIndex", "workflowStatePK", "categoryDate"),
            GSI("UserDisplayStateIndex", "displayStatePK", "categoryDate"),
            GSI("UserNeedsExportIndex", "needsExportPK", "categoryDate"),
            GSI("UserHasChangesIndex", "hasChangesPK", "categoryDate"),
            GSI("UserPendingReconciliationVerificationIndex", "reconciliationVerifiedPK", "categoryDate"),
            GSI("UserNeedsSuenlaceExportIndex", "needsSuenlaceExportPK", "categoryDate"),
            GSI("UserConciliationNeedsExportIndex", "conciliationNeedsExportPK", "categoryDate"),
            GSI("UserReconciliationNeedsA3ExportIndex", "reconciliationNeedsA3ExportPK", "categoryDate"),
            GSI("UserA3ExportQueueIndex", "queuedForA3ExportPK", "categoryDate"),
        ],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Companies",
        partition_key="PK",
        sort_key="SK",
        gsis=[
            GSI("ByNamePrefixIndex", "company_name_prefix_4", "revenue", sk_type="N"),
            GSI("ByNameWordIndex", "name_word_1", "revenue", sk_type="N"),
            GSI("ByNameWord2Index", "name_word_2", "revenue", sk_type="N"),
            GSI("ByFullNameIndex", "company_name_normalized", "revenue", sk_type="N"),
            GSI("ByCityIndex", "city", "revenue", sk_type="N"),
            GSI("ByProvinceIndex", "province", "revenue", sk_type="N"),
            GSI("ByRevenueTierIndex", "revenue_tier", "revenue", sk_type="N"),
            GSI("ByCnaeCodeIndex", "cnae_code", "revenue", sk_type="N"),
            GSI("ByCityAndNameIndex", "city", "company_name_normalized"),
            GSI("ByCifIndex", "cif", "revenue", sk_type="N"),
        ],
    ),
    TableSchema(
        table_name_pattern="{Stage}_User_Invoice_Incomes",
        partition_key="userId",
        sort_key="categoryDate",
        gsis=[],  # Add GSIs as needed
    ),
    TableSchema(
        table_name_pattern="{Stage}_Bank_Reconciliations",
        partition_key="userId",
        sort_key="categoryDate",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Payroll_Slips",
        partition_key="userId",
        sort_key="categoryDate",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Delivery_Notes",
        partition_key="userId",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Employees",
        partition_key="userId",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Providers",
        partition_key="userId",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Customers",
        partition_key="userId",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Suppliers",
        partition_key="userId",
        sort_key="CIF",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Organizations",
        partition_key="PK",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Organization_Locations",
        partition_key="PK",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_User_Invoice_Category_Configs",
        partition_key="userId",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Document_Ibans",
        partition_key="PK",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Daily_Stats",
        partition_key="PK",
        sort_key="SK",
        gsis=[],
    ),
    TableSchema(
        table_name_pattern="{Stage}_Monthly_Stats",
        partition_key="PK",
        sort_key="SK",
        gsis=[],
    ),
]


def get_all_schemas_description(stage: str) -> str:
    return "\n\n".join(t.describe(stage) for t in TABLES)


def find_table(name_fragment: str) -> TableSchema | None:
    fragment = name_fragment.lower().replace(" ", "_")
    for t in TABLES:
        if fragment in t.table_name_pattern.lower():
            return t
    return None
