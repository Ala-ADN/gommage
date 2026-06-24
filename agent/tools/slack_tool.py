"""Deterministic Slack tool double."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from uuid import uuid4


@dataclass
class SlackTool:
    posted: list[dict[str, str]] = field(default_factory=list)
    outbox_root: str | Path = ".gommage/outbox"

    def post_message(self, channel: str, text: str) -> dict[str, str | bool]:
        message_id = f"demo-slack-{uuid4().hex[:8]}"
        message = {"message_id": message_id, "channel": channel, "text": text}
        self.posted.append(message)
        outbox_root = Path(self.outbox_root)
        outbox_root.mkdir(parents=True, exist_ok=True)
        outbox_path = outbox_root / f"{message_id}.json"
        outbox_path.write_text(json.dumps(message, indent=2, sort_keys=True), encoding="utf-8")
        return {"ok": True, "dry_run": True, "outbox_path": str(outbox_path), **message}
