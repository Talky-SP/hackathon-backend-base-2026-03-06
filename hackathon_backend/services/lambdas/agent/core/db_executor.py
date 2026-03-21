"""
Direct DynamoDB query executor.

Executes queries based on the orchestrator's data_requests using
well-known GSI patterns. This is a reliable fallback that doesn't
depend on LLM-generated query plans.

Security: Every query is scoped by locationId — enforced at this layer.
"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key, Attr
from langfuse import observe

AWS_PROFILE = os.getenv("AWS_PROFILE", "hackathon-equipo1")
AWS_REGION = os.getenv("AWS_REGION", "eu-west-3")

_dynamodb = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        _dynamodb = session.resource("dynamodb")
    return _dynamodb


# ---------------------------------------------------------------------------
# Table config — maps table name to PK field, date GSI, and date SK-
# ---------------------------------------------------------------------------
TABLE_CONFIG = {
    "User_Expenses": {
        "pk": "userId",
        "date_index": "UserIdInvoiceDateIndex",
        "date_sk": "invoice_date",
    },
    "User_Invoice_Incomes": {
        "pk": "userId",
        "date_index": "UserIdInvoiceDateIndex",
        "date_sk": "invoice_date",
    },
    "Bank_Reconciliations": {
        "pk": "locationId",
        "date_index": None,  # SK is composite: MTXN#{date}#{id}
        "date_sk": None,
    },
    "Payroll_Slips": {
        "pk": "locationId",
        "date_index": None,
        "date_sk": None,
    },
    "Delivery_Notes": {
        "pk": "userId",
        "date_index": None,
        "date_sk": None,
    },
    "Employees": {"pk": "locationId"},
    "Providers": {"pk": "locationId"},
    "Customers": {"pk": "locationId"},
    "Daily_Stats": {"pk": "locationId", "date_sk": "dayKey"},
    "Monthly_Stats": {"pk": "locationId", "date_sk": "monthKey"},
}


def _sanitize(obj: Any) -> Any:
    """Convert Decimal/set to JSON-safe types."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(i) for i in obj]
    if isinstance(obj, set):
        return [_sanitize(i) for i in obj]
    return obj


@observe(name="execute_db_query")
def execute_request(
    data_request: dict,
    location_id: str,
    stage: str = "Dev",
) -> dict[str, Any]:
    """
    Execute a single data_request from the orchestrator against DynamoDB.

    Returns:
        {"table": str, "description": str, "items": list, "count": int, "success": bool}
    """
    table_name = data_request["table"]
    config = TABLE_CONFIG.get(table_name, {"pk": "userId"})
    full_table_name = f"{stage}_{table_name}"
    ddb = _get_dynamodb()
    table = ddb.Table(full_table_name)

    pk_field = config["pk"]
    date_range = data_request.get("date_range")
    date_index = config.get("date_index")
    date_sk = config.get("date_sk")

    result = {
        "table": table_name,
        "description": data_request.get("description", ""),
        "items": [],
        "count": 0,
        "success": False,
    }

    try:
        # Build query params
        query_kwargs: dict[str, Any] = {}

        if date_range and date_index and date_sk:
            # Use date GSI for range queries
            query_kwargs["IndexName"] = date_index
            query_kwargs["KeyConditionExpression"] = (
                Key(pk_field).eq(location_id)
                & Key(date_sk).between(date_range["from"], date_range["to"])
            )
        elif date_range and date_sk and not date_index:
            # Table with date as sort key but no GSI (e.g. Daily_Stats, Monthly_Stats)
            query_kwargs["KeyConditionExpression"] = (
                Key(pk_field).eq(location_id)
                & Key(date_sk).between(date_range["from"], date_range["to"])
            )
        else:
            # Simple PK query
            query_kwargs["KeyConditionExpression"] = Key(pk_field).eq(location_id)

        # Add filter expressions for extra filters
        filters = data_request.get("filters")
        if filters:
            filter_expr = None
            for field, value in filters.items():
                cond = Attr(field).eq(value)
                filter_expr = cond if filter_expr is None else (filter_expr & cond)
            if filter_expr:
                query_kwargs["FilterExpression"] = filter_expr

        # Execute with pagination
        items = []
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))

        while "LastEvaluatedKey" in response:
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = table.query(**query_kwargs)
            items.extend(response.get("Items", []))

        result["items"] = _sanitize(items)
        result["count"] = len(items)
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


@observe(name="fetch_all_data")
def fetch_all(
    data_requests: list[dict],
    location_id: str,
    stage: str = "Dev",
) -> list[dict]:
    """Execute all data_requests and return results."""
    return [
        execute_request(req, location_id, stage)
        for req in data_requests
    ]
