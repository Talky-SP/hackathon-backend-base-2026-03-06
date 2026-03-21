from aws_cdk import Stack
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.lambdas.trial.get_items import GetItemsLambda
from hackathon_backend.constructs.lambdas.trial.create_item import CreateItemLambda
from hackathon_backend.constructs.lambdas.agent.agent_lambda import AgentLambda


class LambdaStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: Config,
        dynamodb_stack=None,
        s3_stack=None,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        self.config = config

        # Trial lambdas
        if dynamodb_stack:
            get_items = GetItemsLambda(self, "GetItems", config=config)
            self.get_items_function = get_items.function

            create_item = CreateItemLambda(self, "CreateItem", config=config)
            self.create_item_function = create_item.function

        # Agent Lambda (Docker-based)
        if dynamodb_stack and s3_stack:
            # Build data table name map for the agent
            data_table_names = {
                "user_expenses": dynamodb_stack.user_expenses_table.table_name,
                "user_invoice_incomes": dynamodb_stack.user_invoice_incomes_table.table_name,
                "bank_reconciliations": dynamodb_stack.bank_reconciliations_table.table_name,
                "payroll_slips": dynamodb_stack.payroll_slips_table.table_name,
                "delivery_notes": dynamodb_stack.delivery_notes_table.table_name,
                "employees": dynamodb_stack.employees_table.table_name,
                "providers": dynamodb_stack.providers_table.table_name,
                "customers": dynamodb_stack.customers_table.table_name,
                "suppliers": dynamodb_stack.suppliers_table.table_name,
                "companies": dynamodb_stack.companies_table.table_name,
                "organizations": dynamodb_stack.organizations_table.table_name,
                "organization_locations": dynamodb_stack.organization_locations_table.table_name,
            }

            agent = AgentLambda(
                self, "Agent",
                config=config,
                agent_table=dynamodb_stack.agent_store_table,
                ws_connections_table=dynamodb_stack.ws_connections_table,
                artifacts_bucket=s3_stack.artifacts_bucket,
                data_table_names=data_table_names,
            )
            self.agent_function = agent.function
