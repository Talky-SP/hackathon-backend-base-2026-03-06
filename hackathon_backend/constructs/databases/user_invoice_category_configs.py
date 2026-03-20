from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB
from hackathon_backend.config.environments import Config


class UserInvoiceCategoryConfigsTable(BaseDynamoDB):
    def __init__(self, scope: Construct, id: str, config: Config):
        self.config = config
        table_name = f"{config.stage.capitalize()}_User_Invoice_Category_Configs"

        super().__init__(
            scope=scope,
            id=id,
            table_name=table_name,
            partition_key="pk",
            sort_key="sk",
            stream_type="NEW_AND_OLD_IMAGES",
            billing_mode=self.config.dynamodb_billing_mode,
            removal_policy=self.config.removal_policy,
        )

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.table).add(tag_key, tag_value)
