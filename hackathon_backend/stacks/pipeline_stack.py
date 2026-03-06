from aws_cdk import (
    Stack,
    pipelines,
    Environment as CDKEnvironment,
)
from constructs import Construct
from hackathon_backend.stacks.deployment_stage import DeploymentStage
from hackathon_backend.config.environments import dev_config, pre_config, prod_config


def _pipeline_synth_commands(stack_name: str, pipeline_target: str) -> list[str]:
    return [
        "npm install -g aws-cdk",
        "pip install -r requirements.txt",
        f"cdk synth -c pipeline={pipeline_target} {stack_name}",
    ]


class DevPipelineStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        dev_env = CDKEnvironment(account=dev_config.account_id, region=dev_config.region)

        source = pipelines.CodePipelineSource.connection(
            "YourOrg/YourRepo", "develop",                      # CHANGE: your GitHub repo
            connection_arn="arn:aws:codeconnections:eu-west-3:131880217295:connection/YOUR-CONN-ID",  # CHANGE
        )

        pipeline = pipelines.CodePipeline(
            self, "DevPipeline",
            pipeline_name="HackathonDevPipeline",
            self_mutation=True,
            cross_account_keys=True,
            synth=pipelines.ShellStep(
                "Synth",
                input=source,
                install_commands=["n 22"],
                commands=_pipeline_synth_commands(stack_name=id, pipeline_target="dev"),
            ),
        )

        pipeline.add_wave(
            "Approval",
            pre=[pipelines.ManualApprovalStep("ApproveDev", comment="Approve development deploy")],
        )

        pipeline.add_stage(DeploymentStage(self, "Development", env=dev_env))


class PrePipelineStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        pre_env = CDKEnvironment(account=pre_config.account_id, region=pre_config.region)

        source = pipelines.CodePipelineSource.connection(
            "YourOrg/YourRepo", "pre",                          # CHANGE
            connection_arn="arn:aws:codeconnections:eu-west-3:222222222222:connection/YOUR-CONN-ID",  # CHANGE
        )

        pipeline = pipelines.CodePipeline(
            self, "PrePipeline",
            pipeline_name="HackathonPrePipeline",
            self_mutation=True,
            cross_account_keys=True,
            synth=pipelines.ShellStep(
                "Synth",
                input=source,
                install_commands=["n 22"],
                commands=_pipeline_synth_commands(stack_name=id, pipeline_target="pre"),
            ),
        )

        pipeline.add_stage(
            DeploymentStage(self, "Preproduction", env=pre_env),
            pre=[pipelines.ManualApprovalStep("ApprovePre", comment="Approve pre-production deploy")],
        )


class ProdPipelineStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        prod_env = CDKEnvironment(account=prod_config.account_id, region=prod_config.region)

        source = pipelines.CodePipelineSource.connection(
            "YourOrg/YourRepo", "main",                         # CHANGE
            connection_arn="arn:aws:codeconnections:eu-west-3:333333333333:connection/YOUR-CONN-ID",  # CHANGE
        )

        pipeline = pipelines.CodePipeline(
            self, "ProdPipeline",
            pipeline_name="HackathonProdPipeline",
            self_mutation=True,
            cross_account_keys=True,
            synth=pipelines.ShellStep(
                "Synth",
                input=source,
                install_commands=["n 22"],
                commands=_pipeline_synth_commands(stack_name=id, pipeline_target="prod"),
            ),
        )

        pipeline.add_stage(
            DeploymentStage(self, "Production", env=prod_env),
            pre=[pipelines.ManualApprovalStep("ApproveProd", comment="Approve production deploy")],
        )
