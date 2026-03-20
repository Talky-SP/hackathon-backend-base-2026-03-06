from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB
from hackathon_backend.config.environments import Config


class BankReconciliationsTable(BaseDynamoDB):
    def __init__(self, scope: Construct, id: str, config: Config):
        self.config = config
        table_name = f"{config.stage.capitalize()}_Bank_Reconciliations"

        super().__init__(
            scope=scope,
            id=id,
            table_name=table_name,
            partition_key="locationId",
            sort_key="SK",
            stream_type="NEW_AND_OLD_IMAGES",
            billing_mode=self.config.dynamodb_billing_mode,
            removal_policy=self.config.removal_policy,
        )

        # GSI 1
        self.add_gsi("PendingByDate", "GSI1PK", "GSI1SK")
        # GSI 2
        self.add_gsi("ByMatchedExpense", "GSI2PK", "GSI2SK")
        # GSI 3
        self.add_gsi("TransactionsByCanonicalId", "SK", "locationId")
        # GSI 4
        self.add_gsi("LocationByStatusDate", "locationId", "status_date")
        # GSI 5
        self.add_gsi("LocationDisplayStateIndex", "displayStatePK", "displayStateUpdatedAt")
        # GSI 6 - no sort key
        self.add_gsi("ByVendorCif", "vendor_cif")
        # GSI 7
        self.add_gsi("LocationByPayrollDate", "locationId", "payroll_date")
        # GSI 8
        self.add_gsi("LocationByVendorAiId", "locationId", "vendor_ai_id")
        # GSI 9
        self.add_gsi("LocationByCustomerAiId", "locationId", "customer_ai_id")
        # GSI 10 - no sort key
        self.add_gsi("ByCustomerCif", "customer_cif")
        # GSI 11
        self.add_gsi("HungarianReviewByLocation", "hungarian_review_pk", "hungarian_review_type")

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.table).add(tag_key, tag_value)
