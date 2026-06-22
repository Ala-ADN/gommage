"""Divergence detection between original and edited/replayed traces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from recorder.serializer.aer_schema import AgentExecutionRecord, AERStep, stable_json


@dataclass(slots=True)
class Divergence:
    step_id: int
    field: str
    original: Any
    modified: Any
    severity: str = "info"


class DivergenceTracker:
    def compare_records(
        self,
        original: AgentExecutionRecord,
        modified: AgentExecutionRecord,
    ) -> list[Divergence]:
        original_by_id = {step.step_id: step for step in original.steps}
        divergences: list[Divergence] = []
        for step in modified.steps:
            previous = original_by_id.get(step.step_id)
            if previous is None:
                divergences.append(
                    Divergence(step.step_id, "step", None, "added", "warning")
                )
                continue
            divergences.extend(self.compare_steps(previous, step))
        return divergences

    def compare_steps(self, original: AERStep, modified: AERStep) -> list[Divergence]:
        divergences: list[Divergence] = []
        if original.llm and modified.llm:
            if original.llm.prompt != modified.llm.prompt:
                divergences.append(
                    Divergence(
                        original.step_id,
                        "llm.prompt",
                        original.llm.prompt,
                        modified.llm.prompt,
                        "warning",
                    )
                )
            if original.llm.response != modified.llm.response:
                divergences.append(
                    Divergence(
                        original.step_id,
                        "llm.response",
                        original.llm.response,
                        modified.llm.response,
                    )
                )
        if original.tool and modified.tool:
            if stable_json(original.tool.parameters) != stable_json(modified.tool.parameters):
                divergences.append(
                    Divergence(
                        original.step_id,
                        "tool.parameters",
                        original.tool.parameters,
                        modified.tool.parameters,
                        "warning",
                    )
                )
            if stable_json(original.tool.result) != stable_json(modified.tool.result):
                divergences.append(
                    Divergence(
                        original.step_id,
                        "tool.result",
                        original.tool.result,
                        modified.tool.result,
                    )
                )
        return divergences
