from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB
from hackathon_backend.config.environments import Config


class DeliveryNotesTable(BaseDynamoDB):
    def __init__(self, scope: Construct, id: str, config: Config):
        self.config = config
        table_name = f"{config.stage.capitalize()}_Delivery_Notes"

        super().__init__(
            scope=scope,
            id=id,
            table_name=table_name,
            partition_key="userId",
            sort_key="categoryDate",
            stream_type="NEW_IMAGE",
            billing_mode=self.config.dynamodb_billing_mode,
            removal_policy=self.config.removal_policy,
        )

        # GSI 1 - no sort key
        self.add_gsi("DeliveryNoteNumberIndex", "delivery_note_number")
        # GSI 2
        self.add_gsi("UserSupplierDeliveryNoteIndex", "userSupplierCombination", "delivery_note_number")
        # GSI 3
        self.add_gsi("DeliveryNotesByProcessingStatusIndex", "processing_status", "categoryDate")
        # GSI 4
        self.add_gsi("ProviderCIFReconciledIndex", "supplier_cif", "reconciled_date")

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.table).add(tag_key, tag_value)
