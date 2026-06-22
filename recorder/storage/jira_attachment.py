"""Jira attachment adapter.

The hackathon demo should not require real Jira credentials, so this adapter
persists the would-be attachment locally with enough metadata for audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.storage.local_store import LocalTraceStore


@dataclass(slots=True)
class JiraAttachmentReceipt:
    ticket_id: str
    filename: str
    path: str


class LocalJiraAttachmentStore:
    def __init__(self, root: str | Path = ".gommage/jira_attachments") -> None:
        self.root = Path(root)
        self.trace_store = LocalTraceStore(self.root)

    def attach_trace(self, record: AgentExecutionRecord) -> JiraAttachmentReceipt:
        path = self.trace_store.save(record)
        return JiraAttachmentReceipt(
            ticket_id=record.jira_ticket_id,
            filename=path.name,
            path=str(path),
        )
