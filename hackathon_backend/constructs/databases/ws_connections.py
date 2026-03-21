"""WebSocket connections table — tracks active API Gateway WebSocket connections."""
from aws_cdk import Tags
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.base_class.base_class_dynamodb import BaseDynamoDB


class WsConnectionsTable(Construct):
    def __init__(self, scope: Construct, id: str, config: Config):
        super().__init__(scope, id)

        self._db = BaseDynamoDB(
            self, "WsConnections",
            table_name=config.resource_name("WS_Connections"),
            partition_key="connectionId",
            billing_mode=config.dynamodb_billing_mode,
            removal_policy=config.removal_policy,
        )
        self.table = self._db.table

        for k, v in config.get_tags().items():
            Tags.of(self.table).add(k, v)
