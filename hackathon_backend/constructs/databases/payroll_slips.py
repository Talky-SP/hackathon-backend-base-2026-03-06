from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB
from hackathon_backend.config.environments import Config


class PayrollSlipsTable(BaseDynamoDB):
    def __init__(self, scope: Construct, id: str, config: Config):
        self.config = config
        table_name = f"{config.stage.capitalize()}_Payroll_Slips"

        super().__init__(
            scope=scope,
            id=id,
            table_name=table_name,
            partition_key="locationId",
            sort_key="categoryDate",
            stream_type="NEW_AND_OLD_IMAGES",
            billing_mode=self.config.dynamodb_billing_mode,
            removal_policy=self.config.removal_policy,
        )

        # GSI 1
        self.add_gsi("LocationEmployeeDateIndex", "locationId", "employee_date_key")
        # GSI 2
        self.add_gsi("OrgCifPeriodIndex", "org_cif", "period_key")
        # GSI 3
        self.add_gsi("LocationNeedsReviewIndex", "needsReviewPK", "categoryDate")
        # GSI 4
        self.add_gsi("LocationNeedsExportIndex", "needsExportPK", "categoryDate")
        # GSI 5
        self.add_gsi("LocationWorkflowStateIndex", "workflowStatePK", "categoryDate")
        # GSI 6
        self.add_gsi("LocationDisplayStateIndex", "displayStatePK", "categoryDate")
        # GSI 7
        self.add_gsi("OrgEmployeeIndex", "org_employee_key", "payroll_date")
        # GSI 8
        self.add_gsi("NeedsReviewIndex", "needsReview", "categoryDate")
        # GSI 9
        self.add_gsi("LocationPendingReconciliationVerificationIndex", "reconciliationVerifiedPK", "categoryDate")
        # GSI 10
        self.add_gsi("LocationNeedsSuenlaceExportIndex", "needsSuenlaceExportPK", "categoryDate")
        # GSI 11
        self.add_gsi("LocationConciliationNeedsExportIndex", "conciliationNeedsExportPK", "categoryDate")
        # GSI 12
        self.add_gsi("LocationReconciliationNeedsA3ExportIndex", "reconciliationNeedsA3ExportPK", "categoryDate")
        # GSI 13
        self.add_gsi("LocationA3ExportQueueIndex", "queuedForA3ExportPK", "categoryDate")

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.table).add(tag_key, tag_value)
