import json
import os
import logging
import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["TABLE_NAME"]
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event: dict, context) -> dict:
    """GET /items - list all items or query by itemId."""
    try:
        params = event.get("queryStringParameters") or {}
        item_id = params.get("itemId")

        if item_id:
            response = table.query(
                KeyConditionExpression=Key("itemId").eq(item_id),
            )
            items = response.get("Items", [])
        else:
            response = table.scan(Limit=50)
            items = response.get("Items", [])

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"items": items}, default=str),
        }
    except Exception as e:
        logger.exception("Error getting items")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e)}),
        }
