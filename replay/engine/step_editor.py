"""Apply prompt and tool-result edits to a recorded trace."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from recorder.serializer.aer_schema import AgentExecutionRecord


@dataclass(slots=True)
class StepEdit:
    step_id: int
    prompt: str | None = None
    tool_parameters: dict[str, Any] | None = None
    tool_result: Any | None = None
    note: str = ""


class StepEditor:
    def __init__(self, record: AgentExecutionRecord) -> None:
        self.record = record

    def apply(self, edits: list[StepEdit]) -> AgentExecutionRecord:
        edited = deepcopy(self.record)
        edited.metadata = {
            **edited.metadata,
            "branch_from": self.record.run_id,
            "edits": [
                {
                    "step_id": edit.step_id,
                    "prompt": edit.prompt,
                    "tool_parameters": edit.tool_parameters,
                    "tool_result": edit.tool_result,
                    "note": edit.note,
                }
                for edit in edits
            ],
        }
        edits_by_step = {edit.step_id: edit for edit in edits}
        for step in edited.steps:
            edit = edits_by_step.get(step.step_id)
            if edit is None:
                continue
            if edit.prompt is not None:
                if step.llm is None:
                    raise ValueError(f"step {step.step_id} is not an LLM step")
                step.llm.prompt = edit.prompt
                step.observation = "prompt edited for replay"
            if edit.tool_result is not None:
                if step.tool is None:
                    raise ValueError(f"step {step.step_id} is not a tool step")
                step.tool.result = edit.tool_result
                step.observation = "tool result injected for replay"
            if edit.tool_parameters is not None:
                if step.tool is None:
                    raise ValueError(f"step {step.step_id} is not a tool step")
                step.tool.parameters = edit.tool_parameters
                step.observation = "tool parameters edited for replay"
        edited.validate()
        return edited
