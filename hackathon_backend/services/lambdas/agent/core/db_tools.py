"""
Tool definitions for the orchestrator.

The `fetch_financial_data` tool does NOT execute queries directly.
It captures what data the orchestrator needs and delegates to an external
database agent that handles the actual DynamoDB queries.
"""

# ---------------------------------------------------------------------------
# Tool definition — passed to the LLM as available tools
# ---------------------------------------------------------------------------
FETCH_FINANCIAL_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_financial_data",
        "description": (
            "Request financial data from the company's databases. "
            "A specialized database agent will execute the actual queries. "
            "Describe clearly what data you need and why."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_question": {
                    "type": "string",
                    "description": "The original user question that needs data to be answered.",
                },
                "data_needed": {
                    "type": "array",
                    "description": "List of data requests. Each describes one dataset needed.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "table": {
                                "type": "string",
                                "description": "Which table to query",
                                "enum": [
                                    "User_Expenses", "User_Invoice_Incomes",
                                    "Bank_Reconciliations", "Payroll_Slips",
                                    "Delivery_Notes", "Employees", "Providers",
                                    "Customers", "Daily_Stats", "Monthly_Stats",
                                ],
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "Natural language description of what data to fetch. "
                                    "E.g. 'All income invoices from March 2026', "
                                    "'Top suppliers by total expense amount in Q1 2026'"
                                ),
                            },
                            "fields_needed": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Specific fields needed from the table. "
                                    "E.g. ['total', 'invoice_date', 'supplier', 'category']"
                                ),
                            },
                            "date_range": {
                                "type": "object",
                                "description": "Date range filter if applicable",
                                "properties": {
                                    "from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                                    "to": {"type": "string", "description": "End date YYYY-MM-DD"},
                                },
                            },
                            "filters": {
                                "type": "object",
                                "description": (
                                    "Additional filters as key-value pairs. "
                                    "E.g. {'reconciled': false, 'supplier_cif': 'B12345678'}"
                                ),
                                "additionalProperties": True,
                            },
                        },
                        "required": ["table", "description"],
                    },
                },
                "chart_suggestion": {
                    "type": "object",
                    "description": "If the response would benefit from a chart, suggest one.",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["bar", "line", "pie", "table", "none"],
                            "description": "Type of chart to display",
                        },
                        "title": {
                            "type": "string",
                            "description": "Chart title",
                        },
                    },
                },
            },
            "required": ["user_question", "data_needed"],
        },
    },
}

# All tool definitions to pass to the orchestrator LLM
TOOLS = [FETCH_FINANCIAL_DATA_TOOL]
