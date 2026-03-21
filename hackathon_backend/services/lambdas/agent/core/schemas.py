"""
DynamoDB table schemas for the orchestrator LLM context.

Imports from table_wiki.py (single source of truth) and provides
get_schemas_summary() for the orchestrator system prompt.
"""

from hackathon_backend.agents.table_wiki import TABLE_WIKI, get_wiki_text


def get_schemas_summary() -> str:
    """Return a compact text summary of all table schemas for LLM context."""
    return get_wiki_text()
