from aws_cdk import (
    aws_dynamodb as dynamodb,
    RemovalPolicy,
)
from constructs import Construct


class BaseDynamoDB(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        table_name: str,
        partition_key: str,
        sort_key: str | None = None,
        enable_stream: bool = False,
        billing_mode: str = "PAY_PER_REQUEST",
        removal_policy: RemovalPolicy = RemovalPolicy.RETAIN,
    ):
        super().__init__(scope, id)

        partition_key_attr = dynamodb.Attribute(
            name=partition_key,
            type=dynamodb.AttributeType.STRING,
        )

        table_props: dict = {
            "table_name": table_name,
            "partition_key": partition_key_attr,
            "removal_policy": removal_policy,
            "billing_mode": (
                dynamodb.BillingMode.PAY_PER_REQUEST
                if billing_mode == "PAY_PER_REQUEST"
                else dynamodb.BillingMode.PROVISIONED
            ),
        }

        if sort_key:
            table_props["sort_key"] = dynamodb.Attribute(
                name=sort_key,
                type=dynamodb.AttributeType.STRING,
            )

        if enable_stream:
            table_props["stream"] = dynamodb.StreamViewType.NEW_AND_OLD_IMAGES

        self.table = dynamodb.Table(self, id, **table_props)
