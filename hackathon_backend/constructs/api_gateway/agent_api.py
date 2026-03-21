"""
Agent API Gateway constructs — REST (HTTP API) + WebSocket API.

REST API:
    - HTTP API Gateway v2 (cheaper, simpler than REST API v1)
    - Proxies all requests to the Agent Lambda via Mangum
    - CORS configured for frontend origins

WebSocket API:
    - WebSocket API Gateway v2
    - Routes: $connect, $disconnect, $default → all to Agent Lambda
    - Client sends JSON messages, Lambda sends responses via post_to_connection
"""
from aws_cdk import (
    Tags,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as integrations,
    aws_lambda as _lambda,
    CfnOutput,
)
from constructs import Construct
from hackathon_backend.config.environments import Config


class AgentRestApi(Construct):
    """HTTP API Gateway v2 for REST endpoints."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        handler: _lambda.IFunction,
    ):
        super().__init__(scope, id)

        self.config = config

        # Lambda integration
        integration = integrations.HttpLambdaIntegration(
            "AgentRestIntegration",
            handler=handler,
        )

        # HTTP API
        self.api = apigwv2.HttpApi(
            self, "AgentHttpApi",
            api_name=config.resource_name("Agent-REST-API"),
            description="AI CFO Agent REST API",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=config.get_allowed_origins() or ["*"],
                allow_methods=[
                    apigwv2.CorsHttpMethod.GET,
                    apigwv2.CorsHttpMethod.POST,
                    apigwv2.CorsHttpMethod.PUT,
                    apigwv2.CorsHttpMethod.PATCH,
                    apigwv2.CorsHttpMethod.DELETE,
                    apigwv2.CorsHttpMethod.OPTIONS,
                ],
                allow_headers=["*"],
            ),
        )

        # Default route — proxy everything to Lambda
        self.api.add_routes(
            path="/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integration,
        )
        # Root path too
        self.api.add_routes(
            path="/",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integration,
        )

        CfnOutput(self, "AgentRestApiUrl",
                   value=self.api.url or "",
                   description="Agent REST API URL")

        for k, v in config.get_tags().items():
            Tags.of(self.api).add(k, v)


class AgentWebSocketApi(Construct):
    """WebSocket API Gateway v2 for real-time chat."""

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        handler: _lambda.IFunction,
    ):
        super().__init__(scope, id)

        self.config = config

        # WebSocket API
        self.api = apigwv2.WebSocketApi(
            self, "AgentWsApi",
            api_name=config.resource_name("Agent-WS-API"),
            description="AI CFO Agent WebSocket API",
            connect_route_options=apigwv2.WebSocketRouteOptions(
                integration=integrations.WebSocketLambdaIntegration(
                    "ConnectIntegration", handler=handler,
                ),
            ),
            disconnect_route_options=apigwv2.WebSocketRouteOptions(
                integration=integrations.WebSocketLambdaIntegration(
                    "DisconnectIntegration", handler=handler,
                ),
            ),
            default_route_options=apigwv2.WebSocketRouteOptions(
                integration=integrations.WebSocketLambdaIntegration(
                    "DefaultIntegration", handler=handler,
                ),
            ),
        )

        # Deploy to a stage
        self.stage = apigwv2.WebSocketStage(
            self, "AgentWsStage",
            web_socket_api=self.api,
            stage_name=config.stage,
            auto_deploy=True,
        )

        CfnOutput(self, "AgentWsApiUrl",
                   value=self.stage.url,
                   description="Agent WebSocket API URL")

        # Store the callback URL for Lambda env var
        self.callback_url = self.stage.callback_url

        for k, v in config.get_tags().items():
            Tags.of(self.api).add(k, v)
