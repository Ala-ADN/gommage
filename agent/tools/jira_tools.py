"""Deterministic Jira tool doubles used by the demo agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class JiraToolset:
    tickets: dict[str, dict[str, Any]] = field(default_factory=dict)
    updates: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tickets:
            self.tickets["DEMO-101"] = {
                "ticket_id": "DEMO-101",
                "summary": "Customer cannot access billing export",
                "description": "Export job fails after the account migration.",
                "priority": "high",
                "reporter": "ops@example.com",
                "owner": "billing-team@example.com",
            }

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        if ticket_id not in self.tickets:
            self.tickets[ticket_id] = {
                "ticket_id": ticket_id,
                "summary": "Demo support issue",
                "description": "Synthetic ticket generated for local replay testing.",
                "priority": "medium",
                "reporter": "ops@example.com",
                "owner": "support-team@example.com",
            }
        return dict(self.tickets[ticket_id])

    def update_ticket(self, ticket_id: str, status: str, comment: str) -> dict[str, Any]:
        update = {"ticket_id": ticket_id, "status": status, "comment": comment}
        self.updates.append(update)
        return {"ok": True, **update}
