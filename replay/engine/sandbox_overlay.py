"""Virtual write overlay used by replay forks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SandboxWrite:
    step_id: int
    tool_name: str
    parameters: dict[str, Any]
    output: Any
    note: str = "captured in sandbox overlay"


@dataclass(slots=True)
class SandboxOverlay:
    """Read-through/write-capture model for Jira replay exploration."""

    writes: list[SandboxWrite] = field(default_factory=list)

    def capture_write(
        self,
        *,
        step_id: int,
        tool_name: str,
        parameters: dict[str, Any],
        output: Any,
        note: str = "captured in sandbox overlay",
    ) -> Any:
        self.writes.append(
            SandboxWrite(
                step_id=step_id,
                tool_name=tool_name,
                parameters=dict(parameters),
                output=output,
                note=note,
            )
        )
        return output

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "step_id": write.step_id,
                "tool_name": write.tool_name,
                "parameters": write.parameters,
                "output": write.output,
                "note": write.note,
            }
            for write in self.writes
        ]
