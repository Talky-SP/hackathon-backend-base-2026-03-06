from aws_cdk import Stack, aws_dynamodb as dynamodb
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.databases.agent_store import AgentStoreTable
from hackathon_backend.constructs.databases.ws_connections import WsConnectionsTable


class DynamoDBStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config: Config, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.config = config
        stage = config.stage.capitalize()  # "Dev", "Pre", "Prod"

        # -------------------------------------------------------------------
        # Import existing data tables (owned by the other pipeline/stack)
        # These tables already exist and contain business data.
        # -------------------------------------------------------------------
        def _import(name: str) -> dynamodb.ITable:
            return dynamodb.Table.from_table_name(self, name.replace("_", ""), f"{stage}_{name}")

        self.trial_items_table = dynamodb.Table.from_table_name(self, "TrialItems", "Trial_Items")
        self.user_expenses_table = _import("User_Expenses")
        self.user_invoice_incomes_table = _import("User_Invoice_Incomes")
        self.bank_reconciliations_table = _import("Bank_Reconciliations")
        self.payroll_slips_table = _import("Payroll_Slips")
        self.delivery_notes_table = _import("Delivery_Notes")
        self.employees_table = _import("Employees")
        self.providers_table = _import("Providers")
        self.customers_table = _import("Customers")
        self.suppliers_table = _import("Suppliers")
        self.companies_table = _import("Companies")
        self.organizations_table = _import("Organizations")
        self.organization_locations_table = _import("Organization_Locations")
        self.user_invoice_category_configs_table = _import("User_Invoice_Category_Configs")
        self.document_ibans_table = _import("Document_Ibans")
        self.daily_stats_table = _import("Daily_Stats")
        self.monthly_stats_table = _import("Monthly_Stats")

        # -------------------------------------------------------------------
        # Create NEW agent-specific tables (not in the other pipeline)
        # -------------------------------------------------------------------
        # Agent_Store — single-table for chats, messages, costs, traces, tasks
        agent_store = AgentStoreTable(self, "AgentStoreTable", config=config)
        self.agent_store_table = agent_store.table

        # WS_Connections — WebSocket connection tracking
        ws_connections = WsConnectionsTable(self, "WsConnectionsTable", config=config)
        self.ws_connections_table = ws_connections.table
