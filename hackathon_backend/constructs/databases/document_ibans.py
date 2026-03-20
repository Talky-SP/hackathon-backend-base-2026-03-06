from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB
from hackathon_backend.config.environments import Config


class DocumentIbansTable(BaseDynamoDB):
    def __init__(self, scope: Construct, id: str, config: Config):
        self.config = config
        table_name = f"{config.stage.capitalize()}_Document_Ibans"

        super().__init__(
            scope=scope,
            id=id,
            table_name=table_name,
            partition_key="PK",
            sort_key="SK",
            billing_mode=self.config.dynamodb_billing_mode,
            removal_policy=self.config.removal_policy,
        )

        # GSI 1
        self.add_gsi("UserIdIbanIndex", "userId", "iban_normalized")

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.table).add(tag_key, tag_value)
