from aws_cdk import (
    aws_dynamodb as dynamodb,
    RemovalPolicy,
)
from constructs import Construct


STREAM_VIEW_TYPES = {
    "NEW_AND_OLD_IMAGES": dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
    "NEW_IMAGE": dynamodb.StreamViewType.NEW_IMAGE,
    "OLD_IMAGE": dynamodb.StreamViewType.OLD_IMAGE,
    "KEYS_ONLY": dynamodb.StreamViewType.KEYS_ONLY,
}


class BaseDynamoDB(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        table_name: str,
        partition_key: str,
        sort_key: str | None = None,
        stream_type: str | None = None,
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

        if stream_type and stream_type in STREAM_VIEW_TYPES:
            table_props["stream"] = STREAM_VIEW_TYPES[stream_type]

        self.table = dynamodb.Table(self, id, **table_props)

    def add_gsi(
        self,
        index_name: str,
        partition_key: str,
        sort_key: str | None = None,
        pk_type: dynamodb.AttributeType = dynamodb.AttributeType.STRING,
        sk_type: dynamodb.AttributeType = dynamodb.AttributeType.STRING,
    ):
        props: dict = {
            "index_name": index_name,
            "partition_key": dynamodb.Attribute(name=partition_key, type=pk_type),
            "projection_type": dynamodb.ProjectionType.ALL,
        }
        if sort_key:
            props["sort_key"] = dynamodb.Attribute(name=sort_key, type=sk_type)
        self.table.add_global_secondary_index(**props)
