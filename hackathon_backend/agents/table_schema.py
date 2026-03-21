"""
Registry of DynamoDB table schemas derived from table_wiki.py (single source of truth).

Keeps the original TableSchema/GSI dataclasses and utility functions for
backward compatibility with existing code (e.g. old AWSAgent).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hackathon_backend.agents.table_wiki import TABLE_WIKI, resolve_table_name


@dataclass
class GSI:
    name: str
    partition_key: str
    sort_key: str | None = None
    pk_type: str = "S"
    sk_type: str = "S"


@dataclass
class TableSchema:
    table_name_pattern: str  # e.g. "{Stage}_User_Expenses"
    partition_key: str
    sort_key: str | None = None
    gsis: list[GSI] = field(default_factory=list)

    def resolve_name(self, stage: str) -> str:
        return self.table_name_pattern.replace("{Stage}", stage.capitalize())

    def describe(self, stage: str) -> str:
        lines = [
            f"Table: {self.resolve_name(stage)}",
            f"  PK: {self.partition_key} (S)",
        ]
        if self.sort_key:
            lines.append(f"  SK: {self.sort_key} (S)")
        for g in self.gsis:
            sk_part = f", SK={g.sort_key}({g.sk_type})" if g.sort_key else ""
            lines.append(f"  GSI {g.name}: PK={g.partition_key}({g.pk_type}){sk_part}")
        return "\n".join(lines)


def _build_tables_from_wiki() -> list[TableSchema]:
    """Build TableSchema list from the canonical TABLE_WIKI."""
    tables = []
    for table_name, wiki in TABLE_WIKI.items():
        gsis = []
        for gsi_name, gsi_info in wiki.get("gsis", {}).items():
            gsis.append(GSI(
                name=gsi_name,
                partition_key=gsi_info["pk"],
                sort_key=gsi_info.get("sk"),
                pk_type="S",
                sk_type=gsi_info.get("sk_type", "S") if gsi_info.get("sk") else "S",
            ))
        sk = wiki.get("sk")
        tables.append(TableSchema(
            table_name_pattern=wiki["table_name_pattern"],
            partition_key=wiki["pk"]["name"],
            sort_key=sk["name"] if sk else None,
            gsis=gsis,
        ))
    return tables


TABLES: list[TableSchema] = _build_tables_from_wiki()


def get_all_schemas_description(stage: str) -> str:
    return "\n\n".join(t.describe(stage) for t in TABLES)


def find_table(name_fragment: str) -> TableSchema | None:
    fragment = name_fragment.lower().replace(" ", "_")
    for t in TABLES:
        if fragment in t.table_name_pattern.lower():
            return t
    return None
