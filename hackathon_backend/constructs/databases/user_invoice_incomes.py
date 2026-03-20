from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB
from hackathon_backend.config.environments import Config


class UserInvoiceIncomesTable(BaseDynamoDB):
    def __init__(self, scope: Construct, id: str, config: Config):
        self.config = config
        table_name = f"{config.stage.capitalize()}_User_Invoice_Incomes"

        super().__init__(
            scope=scope,
            id=id,
            table_name=table_name,
            partition_key="userId",
            sort_key="categoryDate",
            stream_type="NEW_AND_OLD_IMAGES",
            billing_mode=self.config.dynamodb_billing_mode,
            removal_policy=self.config.removal_policy,
        )

        # GSI 1
        self.add_gsi("InvoiceNumberSupplierIndex", "userId", "invoice_supplier_id")
        # GSI 2
        self.add_gsi("UserIdInvoiceDateIndex", "userId", "invoice_date")
        # GSI 3
        self.add_gsi("UserIdSupplierCifIndex", "userId", "supplier_cif")
        # GSI 4
        self.add_gsi("UserIdPnlDateIndex", "userId", "pnl_date")
        # GSI 5
        self.add_gsi("UserByReconStateDate", "userId", "recon_state_date")
        # GSI 6
        self.add_gsi("UserSupplierDateIndex", "userSupplierKey", "charge_date")
        # GSI 7
        self.add_gsi("UserIdClientCifIndex", "userId", "client_cif")
        # GSI 8
        self.add_gsi("UserIdInvoiceIdIndex", "userId", "invoiceid")
        # GSI 9
        self.add_gsi("UserNeedsReviewIndex", "needsReviewPK", "categoryDate")
        # GSI 10
        self.add_gsi("UserByProcessingStatusIndex", "processing_status", "categoryDate")
        # GSI 11
        self.add_gsi("UserWorkflowStateIndex", "workflowStatePK", "categoryDate")
        # GSI 12
        self.add_gsi("UserDisplayStateIndex", "displayStatePK", "categoryDate")
        # GSI 13
        self.add_gsi("UserNeedsExportIndex", "needsExportPK", "categoryDate")
        # GSI 14
        self.add_gsi("UserHasChangesIndex", "hasChangesPK", "categoryDate")
        # GSI 15
        self.add_gsi("UserPendingReconciliationVerificationIndex", "reconciliationVerifiedPK", "categoryDate")
        # GSI 16
        self.add_gsi("UserNeedsSuenlaceExportIndex", "needsSuenlaceExportPK", "categoryDate")
        # GSI 17
        self.add_gsi("UserConciliationNeedsExportIndex", "conciliationNeedsExportPK", "categoryDate")
        # GSI 18
        self.add_gsi("UserReconciliationNeedsA3ExportIndex", "reconciliationNeedsA3ExportPK", "categoryDate")
        # GSI 19
        self.add_gsi("UserA3ExportQueueIndex", "queuedForA3ExportPK", "categoryDate")

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.table).add(tag_key, tag_value)
