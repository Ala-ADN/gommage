"""Deterministic database tool double."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatabaseTool:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.rows:
            self.rows.extend(
                [
                    {
                        "account_id": "acct_42",
                        "ticket_id": "DEMO-101",
                        "last_export_status": "failed",
                        "migration_state": "completed",
                    }
                ]
            )

    def query(self, sql: str) -> list[dict[str, Any]]:
        lowered = sql.lower()
        if not lowered.strip().startswith("select"):
            return [{"error": "demo database only supports SELECT"}]
        if "demo-101" in lowered or "ticket_id" in lowered:
            return [dict(row) for row in self.rows]
        return []
