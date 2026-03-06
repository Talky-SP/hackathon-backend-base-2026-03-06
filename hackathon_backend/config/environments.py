import os
from enum import Enum
from aws_cdk import RemovalPolicy


class Environment(Enum):
    DEV = "dev"
    PRE = "pre"
    PROD = "prod"
    LOCAL = "local"


class Config:
    def __init__(self, env: Environment):
        self.env = env

        # ---------- Common defaults ----------
        self.app_name = "hackathon"  # CHANGE THIS: prefix for all resource names

        if env == Environment.DEV:
            self.account_id = "131880217295"       # CHANGE: target AWS account ID
            self.region = "eu-west-3"              # CHANGE: target region
            self.stage = "dev"
            self.log_level = "INFO"
            self.lambda_memory = 512
            self.dynamodb_billing_mode = "PAY_PER_REQUEST"
            self.lambda_timeout_seconds = 900
            self.removal_policy = RemovalPolicy.DESTROY

        elif env == Environment.PRE:
            self.account_id = "222222222222"       # CHANGE: target AWS account ID
            self.region = "eu-west-3"
            self.stage = "pre"
            self.log_level = "INFO"
            self.lambda_memory = 1024
            self.dynamodb_billing_mode = "PAY_PER_REQUEST"
            self.lambda_timeout_seconds = 900
            self.removal_policy = RemovalPolicy.DESTROY

        elif env == Environment.PROD:
            self.account_id = "333333333333"       # CHANGE: target AWS account ID
            self.region = "eu-west-3"
            self.stage = "prod"
            self.log_level = "WARN"
            self.lambda_memory = 1024
            self.dynamodb_billing_mode = "PAY_PER_REQUEST"
            self.lambda_timeout_seconds = 900
            self.removal_policy = RemovalPolicy.RETAIN

        elif env == Environment.LOCAL:
            self.account_id = "000000000000"
            self.region = "eu-west-3"
            self.stage = "local"
            self.log_level = "DEBUG"
            self.lambda_memory = 512
            self.dynamodb_billing_mode = "PAY_PER_REQUEST"
            self.lambda_timeout_seconds = 900
            self.removal_policy = RemovalPolicy.DESTROY

        # ---------- Cross-account Cognito ----------
        stage_key = self.stage.upper()
        self.cognito_user_pool_id = (
            os.getenv(f"TALKY_COGNITO_USER_POOL_ID_{stage_key}")
            or os.getenv("TALKY_COGNITO_USER_POOL_ID")
        )
        self.cognito_user_pool_arn = (
            os.getenv(f"TALKY_COGNITO_USER_POOL_ARN_{stage_key}")
            or os.getenv("TALKY_COGNITO_USER_POOL_ARN")
        )

    def resource_name(self, base_name: str) -> str:
        """Generate environment-specific resource name."""
        account_suffix = f"-{self.account_id[-4:]}"
        return f"{self.app_name}-{base_name}-{self.stage}{account_suffix}"

    def get_tags(self) -> dict[str, str]:
        """Get standard tags for resources."""
        return {
            "Environment": self.stage,
            "Application": self.app_name,
            "ManagedBy": "CDK",
        }

    def get_allowed_origins(self) -> list[str]:
        """Get allowed CORS origins based on environment."""
        if self.env == Environment.DEV:
            return [
                "http://localhost:3000",
                "http://localhost:5173",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:5173",
            ]
        elif self.env == Environment.PRE:
            return ["https://pre.example.com"]  # CHANGE: your pre domain
        elif self.env == Environment.PROD:
            return ["https://example.com"]      # CHANGE: your prod domain
        elif self.env == Environment.LOCAL:
            return ["http://localhost:3000", "http://localhost:5173"]
        return []


# ---------- Singleton instances ----------
dev_config = Config(Environment.DEV)
pre_config = Config(Environment.PRE)
prod_config = Config(Environment.PROD)
local_config = Config(Environment.LOCAL)


def get_config_for_env(env_name: str) -> Config:
    name = env_name.lower()
    if name in ("dev", "development"):
        return dev_config
    elif name in ("pre", "preproduction"):
        return pre_config
    elif name in ("prod", "production"):
        return prod_config
    elif name in ("local",):
        return local_config
    else:
        raise ValueError(f"Unknown environment: {env_name}")
