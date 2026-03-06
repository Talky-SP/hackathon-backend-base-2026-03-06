from aws_cdk import (
    aws_apigateway as apigw,
    Tags,
)
from constructs import Construct
from hackathon_backend.config.environments import Config


class TrialApi(Construct):
    def __init__(self, scope: Construct, id: str, config: Config):
        super().__init__(scope, id)

        self.config = config

        self.api = apigw.RestApi(
            self, "TrialApi",
            rest_api_name=self.config.resource_name("Trial-API"),
            description="Trial API for testing CDK deployment",
            deploy_options=apigw.StageOptions(stage_name=self.config.stage),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=self.config.get_allowed_origins(),
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=[
                    "Content-Type",
                    "X-Amz-Date",
                    "Authorization",
                    "X-Api-Key",
                    "X-Amz-Security-Token",
                ],
            ),
        )

        # /items resource
        self.items = self.api.root.add_resource("items")

        for tag_key, tag_value in self.config.get_tags().items():
            Tags.of(self.api).add(tag_key, tag_value)
