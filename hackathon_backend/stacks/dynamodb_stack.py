from aws_cdk import Stack
from constructs import Construct
from hackathon_backend.config.environments import Config
from hackathon_backend.constructs.databases.trial_items import TrialItemsTable
from hackathon_backend.constructs.databases.user_expenses import UserExpensesTable
from hackathon_backend.constructs.databases.user_invoice_incomes import UserInvoiceIncomesTable
from hackathon_backend.constructs.databases.bank_reconciliations import BankReconciliationsTable
from hackathon_backend.constructs.databases.payroll_slips import PayrollSlipsTable
from hackathon_backend.constructs.databases.delivery_notes import DeliveryNotesTable
from hackathon_backend.constructs.databases.employees import EmployeesTable
from hackathon_backend.constructs.databases.providers import ProvidersTable
from hackathon_backend.constructs.databases.customers import CustomersTable
from hackathon_backend.constructs.databases.suppliers import SuppliersTable
from hackathon_backend.constructs.databases.companies import CompaniesTable
from hackathon_backend.constructs.databases.organizations import OrganizationsTable
from hackathon_backend.constructs.databases.organization_locations import OrganizationLocationsTable
from hackathon_backend.constructs.databases.user_invoice_category_configs import UserInvoiceCategoryConfigsTable
from hackathon_backend.constructs.databases.document_ibans import DocumentIbansTable
from hackathon_backend.constructs.databases.daily_stats import DailyStatsTable
from hackathon_backend.constructs.databases.monthly_stats import MonthlyStatsTable
from hackathon_backend.constructs.databases.agent_store import AgentStoreTable
from hackathon_backend.constructs.databases.ws_connections import WsConnectionsTable


class DynamoDBStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config: Config, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.config = config

        # Trial table
        trial_items = TrialItemsTable(self, "TrialItemsTable", config=config)
        self.trial_items_table = trial_items.table

        # 1. User_Expenses (Expense Invoices) - 18 GSIs
        user_expenses = UserExpensesTable(self, "UserExpensesTable", config=config)
        self.user_expenses_table = user_expenses.table

        # 2. User_Invoice_Incomes (Income Invoices) - 19 GSIs
        user_invoice_incomes = UserInvoiceIncomesTable(self, "UserInvoiceIncomesTable", config=config)
        self.user_invoice_incomes_table = user_invoice_incomes.table

        # 3. Bank_Reconciliations (Bank Transactions) - 11 GSIs
        bank_reconciliations = BankReconciliationsTable(self, "BankReconciliationsTable", config=config)
        self.bank_reconciliations_table = bank_reconciliations.table

        # 4. Payroll_Slips - 13 GSIs
        payroll_slips = PayrollSlipsTable(self, "PayrollSlipsTable", config=config)
        self.payroll_slips_table = payroll_slips.table

        # 5. Delivery_Notes - 4 GSIs
        delivery_notes = DeliveryNotesTable(self, "DeliveryNotesTable", config=config)
        self.delivery_notes_table = delivery_notes.table

        # 6. Employees - 4 GSIs
        employees = EmployeesTable(self, "EmployeesTable", config=config)
        self.employees_table = employees.table

        # 7. Providers
        providers = ProvidersTable(self, "ProvidersTable", config=config)
        self.providers_table = providers.table

        # 8. Customers
        customers = CustomersTable(self, "CustomersTable", config=config)
        self.customers_table = customers.table

        # 9. Suppliers (Legacy Providers) - SK is 'CIF' uppercase
        suppliers = SuppliersTable(self, "SuppliersTable", config=config)
        self.suppliers_table = suppliers.table

        # 10. Companies (Commercial Registry) - 10 GSIs
        companies = CompaniesTable(self, "CompaniesTable", config=config)
        self.companies_table = companies.table

        # 11. Organizations
        organizations = OrganizationsTable(self, "OrganizationsTable", config=config)
        self.organizations_table = organizations.table

        # 12. Organization_Locations - 1 GSI
        organization_locations = OrganizationLocationsTable(self, "OrganizationLocationsTable", config=config)
        self.organization_locations_table = organization_locations.table

        # 13. User_Invoice_Category_Configs
        category_configs = UserInvoiceCategoryConfigsTable(self, "UserInvoiceCategoryConfigsTable", config=config)
        self.user_invoice_category_configs_table = category_configs.table

        # 14. Document_Ibans - 1 GSI
        document_ibans = DocumentIbansTable(self, "DocumentIbansTable", config=config)
        self.document_ibans_table = document_ibans.table

        # 15. Daily_Stats
        daily_stats = DailyStatsTable(self, "DailyStatsTable", config=config)
        self.daily_stats_table = daily_stats.table

        # 16. Monthly_Stats
        monthly_stats = MonthlyStatsTable(self, "MonthlyStatsTable", config=config)
        self.monthly_stats_table = monthly_stats.table

        # ---------------------------------------------------------------------------
        # Agent tables
        # ---------------------------------------------------------------------------
        # 17. Agent_Store — single-table for chats, messages, costs, traces, tasks
        agent_store = AgentStoreTable(self, "AgentStoreTable", config=config)
        self.agent_store_table = agent_store.table

        # 18. WS_Connections — WebSocket connection tracking
        ws_connections = WsConnectionsTable(self, "WsConnectionsTable", config=config)
        self.ws_connections_table = ws_connections.table
