from constructs import Construct
from aws_cdk import (
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
)


class TrialApiService(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        api: apigateway.RestApi,
        get_items_function: lambda_.Function | None = None,
        create_item_function: lambda_.Function | None = None,
        authorizer: apigateway.IAuthorizer | None = None,
    ):
        super().__init__(scope, construct_id)

        items_resource = api.root.get_resource("items")

        # Method options with optional Cognito authorizer
        method_opts: dict = {}
        if authorizer:
            method_opts["authorization_type"] = apigateway.AuthorizationType.COGNITO
            method_opts["authorizer"] = authorizer

        # GET /items
        if get_items_function:
            get_integration = apigateway.LambdaIntegration(
                get_items_function, proxy=True,
            )
            items_resource.add_method("GET", get_integration, **method_opts)

        # POST /items
        if create_item_function:
            create_integration = apigateway.LambdaIntegration(
                create_item_function, proxy=True,
            )
            items_resource.add_method("POST", create_integration, **method_opts)
