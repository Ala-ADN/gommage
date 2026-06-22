"""Deterministic replay engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from recorder.proxy.mock_registry import MockRegistry
from recorder.serializer.aer_schema import AgentExecutionRecord
from replay.engine.divergence_tracker import Divergence, DivergenceTracker
from replay.engine.step_editor import StepEdit, StepEditor


@dataclass(slots=True)
class ReplayStepResult:
    step_id: int
    kind: str
    output: Any
    input_matches_original: bool
    mocked: bool = False
    side_effect_blocked: bool = False


@dataclass(slots=True)
class ReplayResult:
    run_id: str
    replayed_steps: list[ReplayStepResult] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)

    @property
    def side_effects_blocked(self) -> int:
        return sum(1 for step in self.replayed_steps if step.side_effect_blocked)


class ReplayRunner:
    def __init__(
        self,
        record: AgentExecutionRecord,
        *,
        registry: MockRegistry | None = None,
    ) -> None:
        self.record = record
        self.registry = registry or MockRegistry()

    def replay(self, edits: list[StepEdit] | None = None) -> ReplayResult:
        replay_record = StepEditor(self.record).apply(edits or []) if edits else self.record
        tracker = DivergenceTracker()
        result = ReplayResult(
            run_id=self.record.run_id,
            divergences=tracker.compare_records(self.record, replay_record),
        )

        originals = {step.step_id: step for step in self.record.steps}
        for step in replay_record.steps:
            original = originals[step.step_id]
            input_matches = step.input_fingerprint() == original.input_fingerprint()
            if step.llm is not None:
                result.replayed_steps.append(
                    ReplayStepResult(
                        step_id=step.step_id,
                        kind=step.kind,
                        output=step.llm.response,
                        input_matches_original=input_matches,
                    )
                )
                continue

            if step.tool is not None:
                decision = self.registry.classify(
                    step.tool.tool_name,
                    step.tool.parameters,
                )
                should_mock = step.tool.side_effecting or decision.side_effecting
                output = self.registry.mock_payload_for(step.tool) if should_mock else step.tool.result
                result.replayed_steps.append(
                    ReplayStepResult(
                        step_id=step.step_id,
                        kind=step.kind,
                        output=output,
                        input_matches_original=input_matches,
                        mocked=should_mock,
                        side_effect_blocked=should_mock,
                    )
                )
                continue

            result.replayed_steps.append(
                ReplayStepResult(
                    step_id=step.step_id,
                    kind=step.kind,
                    output=step.observation,
                    input_matches_original=input_matches,
                )
            )
        return result
