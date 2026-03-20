"""
DynamoDB query tools — the tool definitions for the orchestrator's tool_call
and the execution engine that runs the actual DynamoDB queries.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key, Attr
from langfuse import observe

# ---------------------------------------------------------------------------
# Tool definition (OpenAI function-calling schema) — passed to the LLM
# ---------------------------------------------------------------------------
QUERY_DATABASE_TOOL = {
    "type": "function",
    "function": {
        "name": "query_database",
        "description": (
            "Query a DynamoDB financial database table. Use this to retrieve "
            "real data for answering the user's financial questions. "
            "All queries MUST include locationId for multi-tenant security."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name (e.g. 'User_Expenses', 'Bank_Reconciliations')",
                    "enum": [
                        "User_Expenses", "User_Invoice_Incomes",
                        "Bank_Reconciliations", "Payroll_Slips",
                        "Delivery_Notes", "Employees", "Providers",
                        "Customers", "Daily_Stats", "Monthly_Stats",
                    ],
                },
                "index_name": {
                    "type": "string",
                    "description": "GSI name to query. Omit or null to query the primary key.",
                },
                "pk_field": {
                    "type": "string",
                    "description": "Partition key field name (e.g. 'userId', 'locationId')",
                },
                "pk_value": {
                    "type": "string",
                    "description": "Partition key value (typically the locationId)",
                },
                "sk_field": {
                    "type": "string",
                    "description": "Sort key field name (optional)",
                },
                "sk_condition": {
                    "type": "object",
                    "description": "Sort key condition (optional)",
                    "properties": {
                        "operator": {
                            "type": "string",
                            "enum": ["eq", "begins_with", "between", "gt", "gte", "lt", "lte"],
                        },
                        "value": {
                            "type": "string",
                            "description": "Value for eq/begins_with/gt/gte/lt/lte",
                        },
                        "value2": {
                            "type": "string",
                            "description": "Second value for 'between' operator",
                        },
                    },
                    "required": ["operator", "value"],
                },
                "filters": {
                    "type": "array",
                    "description": "Additional filter expressions (applied after query, not on index)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "operator": {
                                "type": "string",
                                "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists", "not_exists"],
                            },
                            "value": {"type": "string"},
                        },
                        "required": ["field", "operator"],
                    },
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of items to return (default 100, max 500)",
                },
            },
            "required": ["table", "pk_field", "pk_value"],
        },
    },
}


# All tool definitions to pass to the LLM
TOOLS = [QUERY_DATABASE_TOOL]


# ---------------------------------------------------------------------------
# DynamoDB execution engine
# ---------------------------------------------------------------------------
ENV_PREFIX = os.getenv("TABLE_ENV_PREFIX", "Dev")  # Dev, Pre, Prod


def _get_dynamodb_resource():
    session = boto3.Session(
        profile_name=os.getenv("AWS_PROFILE", "hackathon-equipo1"),
        region_name=os.getenv("AWS_REGION", "eu-west-3"),
    )
    return session.resource("dynamodb")


def _decimal_to_float(obj: Any) -> Any:
    """Recursively convert Decimal to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_float(i) for i in obj]
    return obj


def _build_sk_condition(sk_field: str, sk_cond: dict) -> Any:
    """Build a boto3 Key condition for the sort key."""
    key = Key(sk_field)
    op = sk_cond["operator"]
    val = sk_cond["value"]

    if op == "eq":
        return key.eq(val)
    if op == "begins_with":
        return key.begins_with(val)
    if op == "between":
        return key.between(val, sk_cond["value2"])
    if op == "gt":
        return key.gt(val)
    if op == "gte":
        return key.gte(val)
    if op == "lt":
        return key.lt(val)
    if op == "lte":
        return key.lte(val)
    raise ValueError(f"Unknown SK operator: {op}")


def _build_filter_expression(filters: list[dict]) -> Any:
    """Build a combined FilterExpression from a list of filter specs."""
    expr = None
    for f in filters:
        field = f["field"]
        op = f["operator"]
        val = f.get("value")

        if op == "eq":
            cond = Attr(field).eq(val)
        elif op == "ne":
            cond = Attr(field).ne(val)
        elif op == "gt":
            cond = Attr(field).gt(val)
        elif op == "gte":
            cond = Attr(field).gte(val)
        elif op == "lt":
            cond = Attr(field).lt(val)
        elif op == "lte":
            cond = Attr(field).lte(val)
        elif op == "contains":
            cond = Attr(field).contains(val)
        elif op == "exists":
            cond = Attr(field).exists()
        elif op == "not_exists":
            cond = Attr(field).not_exists()
        else:
            raise ValueError(f"Unknown filter operator: {op}")

        expr = cond if expr is None else (expr & cond)
    return expr


@observe(name="execute_db_query")
def execute_query(params: dict) -> dict[str, Any]:
    """
    Execute a DynamoDB query from the tool_call parameters.
    Returns {"items": [...], "count": int, "table": str}.
    """
    dynamodb = _get_dynamodb_resource()
    table_name = f"{ENV_PREFIX}_{params['table']}"
    table = dynamodb.Table(table_name)

    # Build key condition
    key_cond = Key(params["pk_field"]).eq(params["pk_value"])
    if params.get("sk_field") and params.get("sk_condition"):
        sk_cond = _build_sk_condition(params["sk_field"], params["sk_condition"])
        key_cond = key_cond & sk_cond

    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": key_cond,
        "Limit": min(params.get("limit", 100), 500),
    }

    if params.get("index_name"):
        query_kwargs["IndexName"] = params["index_name"]

    if params.get("filters"):
        filter_expr = _build_filter_expression(params["filters"])
        if filter_expr is not None:
            query_kwargs["FilterExpression"] = filter_expr

    try:
        response = table.query(**query_kwargs)
        items = _decimal_to_float(response.get("Items", []))
        return {
            "items": items,
            "count": len(items),
            "table": params["table"],
            "scanned_count": response.get("ScannedCount", 0),
        }
    except Exception as e:
        return {
            "error": str(e),
            "table": params["table"],
            "items": [],
            "count": 0,
        }
