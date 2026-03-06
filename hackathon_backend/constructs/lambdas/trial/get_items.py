from aws_cdk import (
    aws_lambda as _lambda,
    aws_dynamodb as dynamodb,
    Tags,
    Duration,
)
from constructs import Construct
from hackathon_backend.config.environments import Config


class GetItemsLambda(Construct):
    def __init__(self, scope: Construct, construct_id: str, config: Config):
        super().__init__(scope, construct_id)

        self.config = config

        trial_items_table = dynamodb.Table.from_table_name(
            self, "TrialItemsTableRef",
            table_name=self.config.resource_name("Trial_Items"),
        )

        self.function = _lambda.Function(
            self, "GetItemsFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="talky_get_items.lambda_handler",
            code=_lambda.Code.from_asset("hackathon_backend/services/lambdas/trial/talky_get_items"),
            function_name=self.config.resource_name("Get_Items"),
            memory_size=self.config.lambda_memory,
            timeout=Duration.seconds(self.config.lambda_timeout_seconds),
            environment={
                "TABLE_NAME": trial_items_table.table_name,
                "ENV": self.config.stage,
                "LOG_LEVEL": self.config.log_level,
            },
        )

        trial_items_table.grant_read_data(self.function)

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.function).add(tag_key, tag_value)
