"""S3 stack — artifacts bucket for agent-generated files (Excel, PDF, etc.)."""
from aws_cdk import (
    Stack,
    Tags,
    RemovalPolicy,
    Duration,
    aws_s3 as s3,
)
from constructs import Construct
from hackathon_backend.config.environments import Config


class S3Stack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config: Config, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.config = config

        self.artifacts_bucket = s3.Bucket(
            self, "ArtifactsBucket",
            removal_policy=config.removal_policy,
            auto_delete_objects=config.removal_policy == RemovalPolicy.DESTROY,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=Duration.days(30),
                    id="expire-artifacts-30d",
                ),
            ],
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET],
                    allowed_origins=config.get_allowed_origins() or ["*"],
                    allowed_headers=["*"],
                ),
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        for k, v in config.get_tags().items():
            Tags.of(self.artifacts_bucket).add(k, v)
