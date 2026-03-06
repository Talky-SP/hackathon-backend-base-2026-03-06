from aws_cdk import (
    Stack,
    aws_apigateway as apigw,
    aws_cognito as cognito,
)
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.api_gateway.trial_api import TrialApi
from hackathon_backend.services.api_gateway.trial.trial_api_service import TrialApiService


class ApiStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        lambda_stack,
        config: Config,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        self.config = config

        # Create API
        trial_api = TrialApi(self, "TrialApi", config=config)

        # Cognito authorizer (cross-account import)
        authorizer = None
        if config.cognito_user_pool_arn:
            imported_pool = cognito.UserPool.from_user_pool_arn(
                self, "ImportedCognitoPool", config.cognito_user_pool_arn,
            )
            authorizer = apigw.CognitoUserPoolsAuthorizer(
                self, "CognitoAuth",
                cognito_user_pools=[imported_pool],
                authorizer_name=config.resource_name("CognitoAuth"),
            )

        # Wire routes
        get_fn = getattr(lambda_stack, "get_items_function", None)
        create_fn = getattr(lambda_stack, "create_item_function", None)

        TrialApiService(
            self, "TrialApiService",
            api=trial_api.api,
            get_items_function=get_fn,
            create_item_function=create_fn,
            authorizer=authorizer,
        )

        self.api = trial_api.api
