from aws_cdk import Stack
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.databases.trial_items import TrialItemsTable


class DynamoDBStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config: Config, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.config = config

        # Trial table
        trial_items = TrialItemsTable(self, "TrialItemsTable", config=config)
        self.trial_items_table = trial_items.table
