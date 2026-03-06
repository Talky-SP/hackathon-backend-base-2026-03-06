import json
import os
import logging
import uuid
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["TABLE_NAME"]
table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event: dict, context) -> dict:
    """POST /items - create a new item."""
    try:
        body = json.loads(event.get("body") or "{}")
        name = body.get("name")
        if not name:
            return {
                "statusCode": 400,
                "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"error": "name is required"}),
            }

        item = {
            "itemId": str(uuid.uuid4()),
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "description": body.get("description", ""),
        }
        table.put_item(Item=item)

        return {
            "statusCode": 201,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"item": item}, default=str),
        }
    except Exception as e:
        logger.exception("Error creating item")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e)}),
        }
