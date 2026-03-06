from aws_cdk import Tags, Stage
from constructs import Construct
from hackathon_backend.config.environments import get_config_for_env
from hackathon_backend.stacks.dynamodb_stack import DynamoDBStack


class LocalDevStage(Stage):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        self.config = get_config_for_env("local")
        for k, v in self.config.get_tags().items():
            Tags.of(self).add(k, v)

        # Only DynamoDB for local testing
        DynamoDBStack(self, "DynamoDBStack", config=self.config)
