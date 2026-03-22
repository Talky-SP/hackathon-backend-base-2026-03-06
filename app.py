import os
import aws_cdk as cdk
from aws_cdk import Environment
from hackathon_backend.stacks.local_dev_stage import LocalDevStage
from hackathon_backend.stacks.pipeline_stack import DevPipelineStack, PrePipelineStack, ProdPipelineStack

app = cdk.App()


def _normalize_pipeline_target(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().lower()
    aliases = {
        "dev": "dev", "development": "dev",
        "pre": "pre", "preproduction": "pre",
        "prod": "prod", "production": "prod",
        "all": "all", "*": "all",
    }
    return aliases.get(normalized)


def _get_pipeline_target() -> str | None:
    for context_key in ("pipeline", "env", "stage"):
        target = _normalize_pipeline_target(app.node.try_get_context(context_key))
        if target:
            return target

    for env_key in ("HACKATHON_PIPELINE", "CDK_PIPELINE", "HACKATHON_ENV", "HACKATHON_STAGE"):
        target = _normalize_pipeline_target(os.getenv(env_key))
        if target:
            return target

    return None


def _create_pipeline_stacks(target: str | None) -> None:
    # CHANGE: account and region to match your pipeline account
    pipeline_env = Environment(account="131880217295", region="eu-west-3")

    if target in (None, "all"):
        DevPipelineStack(app, "HackathonDevPipelineStack", env=pipeline_env)
        PrePipelineStack(app, "HackathonPrePipelineStack", env=pipeline_env)
        ProdPipelineStack(app, "HackathonProdPipelineStack", env=pipeline_env)
        return

    if target == "dev":
        DevPipelineStack(app, "HackathonDevPipelineStack", env=pipeline_env)
        return

    if target == "pre":
        PrePipelineStack(app, "HackathonPrePipelineStack", env=pipeline_env)
        return

    if target == "prod":
        ProdPipelineStack(app, "HackathonProdPipelineStack", env=pipeline_env)
        return

    raise ValueError("Unsupported pipeline target. Use dev, pre, prod, or all.")


if os.getenv("CDK_LOCAL") == "1":
    acct = os.getenv("CDK_DEFAULT_ACCOUNT", "000000000000")
    region = os.getenv("CDK_DEFAULT_REGION", "eu-west-3")
    LocalDevStage(app, "Local", env=Environment(account=acct, region=region))
else:
    _create_pipeline_stacks(_get_pipeline_target())

app.synth()
#sas .