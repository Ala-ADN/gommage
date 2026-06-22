"""Deterministic email tool double."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from uuid import uuid4


@dataclass
class EmailTool:
    sent: list[dict[str, str]] = field(default_factory=list)
    outbox_root: str | Path = ".gommage/outbox"

    def send_email(self, to: str, subject: str, body: str) -> dict[str, str | bool]:
        message_id = f"demo-{uuid4().hex[:8]}"
        message = {"message_id": message_id, "to": to, "subject": subject, "body": body}
        self.sent.append(message)
        outbox_root = Path(self.outbox_root)
        outbox_root.mkdir(parents=True, exist_ok=True)
        outbox_path = outbox_root / f"{message_id}.json"
        outbox_path.write_text(json.dumps(message, indent=2, sort_keys=True), encoding="utf-8")
        return {"ok": True, "outbox_path": str(outbox_path), **message}
