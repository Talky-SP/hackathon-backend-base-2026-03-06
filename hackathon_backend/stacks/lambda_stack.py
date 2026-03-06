from aws_cdk import Stack
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.lambdas.trial.get_items import GetItemsLambda
from hackathon_backend.constructs.lambdas.trial.create_item import CreateItemLambda


class LambdaStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: Config,
        dynamodb_stack=None,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        self.config = config

        if dynamodb_stack:
            get_items = GetItemsLambda(self, "GetItems", config=config)
            self.get_items_function = get_items.function

            create_item = CreateItemLambda(self, "CreateItem", config=config)
            self.create_item_function = create_item.function
