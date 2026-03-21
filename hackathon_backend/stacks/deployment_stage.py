from aws_cdk import Stage, Tags
from constructs import Construct
from hackathon_backend.config.environments import get_config_for_env
from hackathon_backend.stacks.dynamodb_stack import DynamoDBStack
from hackathon_backend.stacks.s3_stack import S3Stack
from hackathon_backend.stacks.lambda_stack import LambdaStack
from hackathon_backend.stacks.api_stack import ApiStack


class DeploymentStage(Stage):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # Get configuration for this environment
        self.config = get_config_for_env(id)

        # Apply common tags
        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self).add(tag_key, tag_value)

        # 1. DynamoDB (data tables + agent tables + WS connections)
        dynamodb_stack = DynamoDBStack(self, "DynamoDBStack", config=self.config)

        # 2. S3 (artifacts bucket)
        s3_stack = S3Stack(self, "S3Stack", config=self.config)

        # 3. Lambda (depends on DynamoDB + S3)
        lambda_stack = LambdaStack(
            self, "LambdaStack",
            config=self.config,
            dynamodb_stack=dynamodb_stack,
            s3_stack=s3_stack,
        )
        lambda_stack.add_dependency(dynamodb_stack)
        lambda_stack.add_dependency(s3_stack)

        # 4. API Gateway (depends on Lambda)
        api_stack = ApiStack(
            self, "ApiStack",
            lambda_stack=lambda_stack,
            config=self.config,
        )
        api_stack.add_dependency(lambda_stack)
