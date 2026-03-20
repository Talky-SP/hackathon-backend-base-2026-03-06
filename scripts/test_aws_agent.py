"""
Test script for AWSAgent — runs 5 natural-language queries against DynamoDB
and prints results as JSON.

Usage:
    AWS_PROFILE=hackathon-equipo1 python -m scripts.test_aws_agent
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile

import boto3

# ---------------------------------------------------------------------------
# 1. Bootstrap: load .env, fetch Vertex AI credentials from Secrets Manager
# ---------------------------------------------------------------------------

def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader — no external dependency needed."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _setup_vertex_credentials(region: str = "eu-west-3") -> str:
    """Fetch the Vertex AI service-account JSON from Secrets Manager,
    write it to a temp file, and set GOOGLE_APPLICATION_CREDENTIALS.
    Returns the path to the temp credentials file.
    """
    sm = boto3.client("secretsmanager", region_name=region)
    resp = sm.get_secret_value(SecretId="talky/vertex-ai")
    creds_json = resp["SecretString"]

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="vertex_creds_", delete=False,
    )
    tmp.write(creds_json)
    tmp.close()

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name

    # liteLLM also needs the project id
    creds = json.loads(creds_json)
    os.environ.setdefault("VERTEXAI_PROJECT", creds.get("project_id", ""))
    os.environ.setdefault("VERTEXAI_LOCATION", "europe-west1")

    return tmp.name


# ---------------------------------------------------------------------------
# 2. Configure logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("test_aws_agent")

# ---------------------------------------------------------------------------
# 3. Test queries — these target the Dev_User_Expenses table (2961 items)
# ---------------------------------------------------------------------------

TEST_QUERIES = [
    # Q1: Simple PK lookup
    "Get the 5 most recent expenses for userId 'deloitte-84'",

    # Q2: Date-range filter using GSI
    "Find all expenses for userId 'deloitte-84' with invoice_date between '2024-08-01' and '2024-08-31'",

    # Q3: Filter by supplier CIF
    "Get expenses for userId 'deloitte-84' where supplier_cif is 'TEMP-2E37B4AAAE7814BD'",

    # Q4: Aggregation-style — just return a count
    "How many expenses does userId 'deloitte-84' have in the COMPRAS category? "
    "Return all matching items so I can count them.",

    # Q5: Cross-attribute filter
    "Find expenses for userId 'deloitte-84' where the gestorId is 'talky' and invoice_date starts with '2024'",
]

# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------

def main() -> None:
    _load_dotenv()
    creds_path = _setup_vertex_credentials()
    logger.info("Vertex AI credentials written to %s", creds_path)

    # Import after env is configured so liteLLM picks up credentials
    from hackathon_backend.agents.aws_agent import AWSAgent

    agent = AWSAgent(
        stage="dev",
        region="eu-west-3",
        model="vertex_ai/gemini-2.0-flash",
        max_iterations=8,
    )

    for i, query in enumerate(TEST_QUERIES, 1):
        print(f"\n{'='*80}")
        print(f"  QUERY {i}: {query}")
        print(f"{'='*80}\n")

        result = agent.run(query)

        print(f"  Success:    {result.success}")
        print(f"  Iterations: {result.iterations_used}")

        if result.success:
            items = result.data
            print(f"  Items:      {len(items) if isinstance(items, list) else 'N/A'}")
            # Print first 3 items (truncated) for readability
            preview = items[:3] if isinstance(items, list) else items
            print(json.dumps(preview, indent=2, default=str)[:2000])
        else:
            print(f"  Error: {result.error}")

        print()

    # Cleanup
    try:
        os.unlink(creds_path)
    except OSError:
        pass


if __name__ == "__main__":
    main()
