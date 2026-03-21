# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

AWS CDK (Python) backend for Talky — an AI CFO assistant that queries DynamoDB financial data via LLM-powered agents. Deployed to AWS with self-mutating CodePipeline per environment (dev/pre/prod).

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# CDK
cdk synth                          # synth all pipelines
cdk synth -c pipeline=dev          # synth only dev
cdk diff -c pipeline=dev           # diff before deploy
cdk deploy HackathonDevPipelineStack -c pipeline=dev

# Local dev with LocalStack
CDK_LOCAL=1 cdk synth

# Tests
pytest                             # all tests
pytest tests/unit/test_foo.py      # single test file
pytest tests/unit/test_foo.py::test_bar  # single test

# Run orchestrator agent CLI (interactive REPL)
python -m hackathon_backend.services.lambdas.agent.main
# Single query
python -m hackathon_backend.services.lambdas.agent.main -q "question here"

# Run AWSAgent directly (requires init_all() for model registry)
python -c "
from hackathon_backend.services.lambdas.agent.core.config import init_all; init_all()
from hackathon_backend.agents.aws_agent import AWSAgent
agent = AWSAgent(user_id='deloitte-84')
result = agent.run('Dame los 5 gastos mas recientes')
print(result.to_json())
"

# Stress test: 15 queries x 5 runs (consistency check)
python -m scripts.test_aws_agent_consistency
python -m scripts.test_aws_agent_consistency --runs 3 --query 1  # single query
```

## Architecture

### Three-Layer CDK Pattern

Each environment deploys three stacks in order (defined in `stacks/deployment_stage.py`):

1. **DynamoDBStack** — 16 DynamoDB tables (expenses, invoices, bank reconciliations, payroll, companies, etc.)
2. **LambdaStack** — Lambda functions, depends on DynamoDBStack
3. **ApiStack** — API Gateway + optional Cognito authorizer, depends on LambdaStack

### Key Separation: Constructs vs Services

- **`constructs/`** — CDK infrastructure definitions (L2/L3 constructs). Defines *what* AWS resources exist.
- **`services/`** — Business logic. Lambda handler code lives here (`services/lambdas/`), API route wiring lives here (`services/api_gateway/`).

### Configuration

`hackathon_backend/config/environments.py` is the single source of truth for all environment-specific settings (accounts, regions, memory, removal policies, CORS origins). Uses singleton `Config` instances (`dev_config`, `pre_config`, etc.).

Resource naming: `hackathon-{ResourceName}-{stage}-{last4digits_of_account}` via `Config.resource_name()`.

### Agent System

Three agent base classes in `agents/agent.py`:

1. **`Agent`** (abstract) — Plan/execute/validate/refine loop using litellm directly. Legacy base class.
2. **`ToolUseAgent`** — Tool-use conversation loop: sends messages to LLM, dispatches tool calls locally, repeats until LLM stops or max_tool_calls reached. Used by the new AWSAgent.
3. **`AWSAgent`** (`agents/aws_agent.py`) — Two-phase data analyst agent:
   - **Phase 1** (function calling): Queries DynamoDB via `query_dynamodb` tool in a tool-use loop. userId auto-injected for tenant security. Returns structured JSON handoff: `{data_for_processing, computation_plan, sources, chart}`.
   - **Phase 2** (codeExecution): If `computation_plan` is set, runs Gemini's native Python sandbox to aggregate/compute metrics. Skipped for simple lookups.
   - Post-processing: Generates Chart.js HTML via `chart_tool.py` if Phase 1 requested a chart.
   - Sanity checks: item_count match, no NaN, total_amount > 0. Retries Phase 2 once on failure.
   - Default model: `gemini-3.0-flash`, temperature=0.0, max_tool_calls=15.
   - Uses `config.completion()` from `services/lambdas/agent/core/config.py`.

4. **Orchestrator Agent** (`services/lambdas/agent/`) — Production pipeline: intent classification (classifier) -> routing (fast_chat vs complex_task) -> orchestration with tool calls. Uses LiteLLM + Langfuse for observability. Config loads secrets from AWS Secrets Manager. Models registered: GPT-5-mini, Claude Sonnet 4.5, Claude Opus 4.6, Gemini 3.0/3.1 (all via Azure or Vertex AI).

### DynamoDB Table Schema Registry

**Single source of truth**: `agents/table_wiki.py` — contains all 16 tables with descriptions, PK/SK with types/formats/examples, every GSI with query recipes, key fields, and source fields. Consolidated from CDK constructs.

Other files import from table_wiki:
- `agents/table_schema.py` — backward-compatible `TableSchema`/`GSI` dataclasses + `find_table()`, `resolve_name()`
- `services/lambdas/agent/core/schemas.py` — `get_schemas_summary()` for orchestrator context

Table names use `{Stage}_TableName` pattern (e.g., `Dev_User_Expenses`). When adding a new table, update `table_wiki.py` (not the CDK constructs — those remain the infra source of truth).

### Adding New Resources

- **DynamoDB table**: Create construct in `constructs/databases/`, inherit `BaseDynamoDB`, add to `stacks/dynamodb_stack.py`
- **Lambda**: Handler in `services/lambdas/{domain}/talky_{name}/`, construct in `constructs/lambdas/{domain}/`, add to `stacks/lambda_stack.py`
- **API route**: Service in `services/api_gateway/{domain}/`, wire in `stacks/api_stack.py`

### Pipelines

| Pipeline | Branch | Stack |
|----------|--------|-------|
| Dev | `develop` | `HackathonDevPipelineStack` |
| Pre | `pre` | `HackathonPrePipelineStack` |
| Prod | `main` | `HackathonProdPipelineStack` |

### Environment Variables

- `AWS_PROFILE` (default: `hackathon-equipo1`), `AWS_REGION` (default: `eu-west-3`)
- `TALKY_COGNITO_USER_POOL_ARN_{STAGE}` — enables Cognito auth on API Gateway
- `CDK_LOCAL=1` — switches to LocalStack stage
- `HACKATHON_PIPELINE` / `CDK_PIPELINE` — selects which pipeline to synth
