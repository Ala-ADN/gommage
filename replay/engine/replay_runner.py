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
        branch_context: dict[str, Any] = {}
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
                edited_parameters = step.tool.parameters != original.tool.parameters
                if edited_parameters:
                    output = _simulated_tool_output(step.tool.tool_name, step.tool.parameters, step.tool.result)
                elif (
                    step.tool.tool_name == "jira.transition"
                    and branch_context.get("customer_response_path_valid")
                ):
                    output = _simulated_tool_output(
                        step.tool.tool_name,
                        {
                            **step.tool.parameters,
                            "comment": step.tool.parameters.get("comment")
                            or "Customer response path verified during replay before closure.",
                        },
                        step.tool.result,
                    )
                elif should_mock:
                    output = self.registry.mock_payload_for(step.tool)
                else:
                    output = step.tool.result
                if step.tool.tool_name == "email.send" and isinstance(output, dict):
                    if output.get("to"):
                        branch_context["customer_response_path_valid"] = True
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


def _simulated_tool_output(tool_name: str, parameters: dict[str, Any], original_result: Any) -> Any:
    """Return a deterministic branch result when replay edits tool parameters."""
    if tool_name == "email.send":
        return {
            "ok": True,
            "mocked": True,
            "branch": "edited-parameters",
            "tool_name": tool_name,
            "to": parameters.get("to", ""),
            "subject": parameters.get("subject", ""),
            "body": parameters.get("body", ""),
            "reason": "replay used edited parameters and did not send external email",
        }
    if tool_name in {"jira.add_comment", "jira.update_ticket", "jira.transition"}:
        return {
            "ok": True,
            "mocked": True,
            "branch": "edited-parameters",
            "tool_name": tool_name,
            "ticket_id": parameters.get("ticket_id"),
            "status": parameters.get("status"),
            "comment": parameters.get("comment") or parameters.get("body"),
            "customer_response_path_valid": bool(parameters.get("comment") or parameters.get("body")),
            "closure_allowed": tool_name == "jira.transition" and bool(parameters.get("comment")),
            "reason": "replay used edited Jira parameters without mutating Jira",
        }
    if isinstance(original_result, dict):
        return {
            **original_result,
            "branch": "edited-parameters",
            "parameters": parameters,
        }
    return {
        "branch": "edited-parameters",
        "tool_name": tool_name,
        "parameters": parameters,
        "original_result": original_result,
    }
