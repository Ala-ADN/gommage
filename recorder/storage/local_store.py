"""Local JSON trace storage."""

from __future__ import annotations

from pathlib import Path

from recorder.serializer.aer_schema import AgentExecutionRecord


class LocalTraceStore:
    def __init__(self, root: str | Path = ".gommage/traces") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"

    def save(self, record: AgentExecutionRecord) -> Path:
        record.validate()
        path = self.path_for(record.run_id)
        path.write_text(record.to_json(), encoding="utf-8")
        return path

    def load(self, run_id: str) -> AgentExecutionRecord:
        return AgentExecutionRecord.from_json(
            self.path_for(run_id).read_text(encoding="utf-8")
        )

    def list_run_ids(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("*.json"))
