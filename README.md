# Hackathon Backend CDK

Backend infrastructure as code using AWS CDK (Python). Follows the Talky-App architecture pattern with self-mutating pipelines per environment.

## Project Structure

```
hackathon_backend/
  config/environments.py           # Source of truth: accounts, regions, env settings
  constructs/
    base_class/base_class_dynamodb.py   # Reusable DynamoDB base construct
    databases/trial_items.py            # Trial DynamoDB table
    lambdas/trial/
      get_items.py                      # GET Lambda construct
      create_item.py                    # POST Lambda construct
    api_gateway/trial_api.py            # REST API construct
  services/
    lambdas/trial/
      talky_get_items/                  # GET /items handler code
      talky_create_item/                # POST /items handler code
    api_gateway/trial/
      trial_api_service.py              # Route wiring (Lambda <-> API Gateway)
  stacks/
    dynamodb_stack.py                   # DynamoDB resources
    lambda_stack.py                     # Lambda functions
    api_stack.py                        # API Gateway + Cognito authorizer
    deployment_stage.py                 # Groups all stacks for one environment
    local_dev_stage.py                  # Lightweight stage for LocalStack
    pipeline_stack.py                   # Dev/Pre/Prod self-mutating pipelines
app.py                                  # CDK entry point
```

## Prerequisites

1. Python 3.11+
2. Node.js (for CDK CLI)
3. AWS CLI configured with credentials for account `131880217295`
4. CDK bootstrapped:

```bash
npx cdk bootstrap aws://131880217295/eu-west-3
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate.bat
pip install -r requirements.txt
```

## CDK Commands

```bash
# Synth all pipelines
cdk synth

# Synth only dev pipeline
cdk synth -c pipeline=dev

# Check diff before deploying
cdk diff -c pipeline=dev

# Deploy the dev pipeline (self-mutating, triggers on push to develop)
cdk deploy HackathonDevPipelineStack -c pipeline=dev

# Deploy individual stacks directly (faster iteration, no pipeline)
cdk deploy -c pipeline=dev \
  HackathonDevPipelineStack/Development/DynamoDBStack \
  HackathonDevPipelineStack/Development/LambdaStack \
  HackathonDevPipelineStack/Development/ApiStack
```

## Configuration

All environment settings live in `hackathon_backend/config/environments.py`:

| Setting | Dev | Pre | Prod |
|---------|-----|-----|------|
| Account | `131880217295` | `222222222222` | `333333333333` |
| Region | eu-west-3 | eu-west-3 | eu-west-3 |
| Lambda Memory | 512 MB | 1024 MB | 1024 MB |
| Removal Policy | DESTROY | DESTROY | RETAIN |

Resource names follow the pattern: `hackathon-{ResourceName}-{stage}-{last4digits}`

Example: `hackathon-Trial_Items-dev-7295`

## Trial API (Proof of Concept)

After deploying, the Trial API exposes two endpoints under `/items`:

### GET /items

List all items (max 50) or query by `itemId`.

```bash
# List all items
curl https://{API_ID}.execute-api.eu-west-3.amazonaws.com/dev/items

# Query by itemId
curl "https://{API_ID}.execute-api.eu-west-3.amazonaws.com/dev/items?itemId=abc-123"
```

Response:
```json
{
  "items": [
    {
      "itemId": "550e8400-e29b-41d4-a716-446655440000",
      "createdAt": "2026-03-06T10:30:00+00:00",
      "name": "My Item",
      "description": "A test item"
    }
  ]
}
```

### POST /items

Create a new item.

```bash
curl -X POST https://{API_ID}.execute-api.eu-west-3.amazonaws.com/dev/items \
  -H "Content-Type: application/json" \
  -d '{"name": "My Item", "description": "A test item"}'
```

Request body:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Item name |
| `description` | string | no | Item description |

Response (201):
```json
{
  "item": {
    "itemId": "550e8400-e29b-41d4-a716-446655440000",
    "createdAt": "2026-03-06T10:30:00+00:00",
    "name": "My Item",
    "description": "A test item"
  }
}
```

### How to find the API URL

After deploying, get the API URL from:

- **AWS Console**: API Gateway > APIs > `hackathon-Trial-API-dev-7295` > Stages > `dev` > Invoke URL
- **CLI**: `aws apigateway get-rest-apis --region eu-west-3` then find the matching API ID

### Cognito Authentication (optional)

If you set the Cognito env var, all endpoints require a Bearer token:

```bash
export TALKY_COGNITO_USER_POOL_ARN_DEV="arn:aws:cognito-idp:eu-west-3:228383006136:userpool/eu-west-3_XXXXXXX"

# Then requests need the Authorization header:
curl https://{API_ID}.execute-api.eu-west-3.amazonaws.com/dev/items \
  -H "Authorization: Bearer {id_token}"
```

Without the env var, the API deploys without authentication (open access).

## Adding New Resources

### New DynamoDB Table
1. Create construct in `constructs/databases/my_table.py` (inherit `BaseDynamoDB`)
2. Add to `stacks/dynamodb_stack.py`

### New Lambda
1. Create handler in `services/lambdas/{domain}/talky_{name}/talky_{name}.py`
2. Create construct in `constructs/lambdas/{domain}/{name}.py`
3. Add to `stacks/lambda_stack.py`

### New API Routes
1. Create service in `services/api_gateway/{domain}/{name}_api_service.py`
2. Wire in `stacks/api_stack.py`

## Pipelines

| Pipeline | Branch | Stack Name |
|----------|--------|------------|
| Dev | `develop` | `HackathonDevPipelineStack` |
| Pre | `pre` | `HackathonPrePipelineStack` |
| Prod | `main` | `HackathonProdPipelineStack` |

Each pipeline is self-mutating: when CDK code changes, the pipeline updates itself before deploying the application stacks.
