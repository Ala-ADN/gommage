"""Deterministic replay engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from recorder.proxy.mock_registry import MockRegistry
from recorder.serializer.aer_schema import AgentExecutionRecord
from replay.engine.divergence_tracker import Divergence, DivergenceTracker
from replay.engine.sandbox_overlay import SandboxOverlay
from replay.engine.step_editor import StepEdit, StepEditor


@dataclass(slots=True)
class ReplayStepResult:
    step_id: int
    kind: str
    output: Any
    input_matches_original: bool
    mocked: bool = False
    side_effect_blocked: bool = False
    sandboxed: bool = False
    unrecorded_tool_call: dict[str, Any] | None = None


@dataclass(slots=True)
class ReplayResult:
    run_id: str
    replayed_steps: list[ReplayStepResult] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)
    mode: str = "record_replay"
    sandbox_from_step_id: int | None = None
    sandbox_writes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def side_effects_blocked(self) -> int:
        return sum(1 for step in self.replayed_steps if step.side_effect_blocked)

    @property
    def unrecorded_tool_calls(self) -> list[dict[str, Any]]:
        return [
            step.unrecorded_tool_call
            for step in self.replayed_steps
            if step.unrecorded_tool_call is not None
        ]


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
        edits = edits or []
        replay_record = StepEditor(self.record).apply(edits) if edits else self.record
        tracker = DivergenceTracker()
        sandbox_from_step_id = min((edit.step_id for edit in edits), default=None)
        result = ReplayResult(
            run_id=self.record.run_id,
            divergences=tracker.compare_records(self.record, replay_record),
            mode="sandbox_overlay" if sandbox_from_step_id is not None else "record_replay",
            sandbox_from_step_id=sandbox_from_step_id,
        )
        overlay = SandboxOverlay()

        originals = {step.step_id: step for step in self.record.steps}
        branch_context: dict[str, Any] = {}
        for step in replay_record.steps:
            original = originals[step.step_id]
            input_matches = step.input_fingerprint() == original.input_fingerprint()
            sandboxed = sandbox_from_step_id is not None and step.step_id >= sandbox_from_step_id
            if step.llm is not None:
                result.replayed_steps.append(
                    ReplayStepResult(
                        step_id=step.step_id,
                        kind=step.kind,
                        output=step.llm.response,
                        input_matches_original=input_matches,
                        sandboxed=sandboxed,
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
                unrecorded_call = None
                if edited_parameters:
                    output = _simulated_tool_output(step.tool.tool_name, step.tool.parameters, step.tool.result)
                    unrecorded_call = {
                        "step_id": step.step_id,
                        "tool_name": step.tool.tool_name,
                        "parameters": step.tool.parameters,
                        "reason": "Replay fork changed the recorded tool input.",
                        "choices": ["manual_response", "execute_live_unsafe", "abort_replay"],
                    }
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
                if sandboxed and should_mock:
                    output = overlay.capture_write(
                        step_id=step.step_id,
                        tool_name=step.tool.tool_name,
                        parameters=step.tool.parameters,
                        output=output,
                    )
                result.replayed_steps.append(
                    ReplayStepResult(
                        step_id=step.step_id,
                        kind=step.kind,
                        output=output,
                        input_matches_original=input_matches,
                        mocked=should_mock,
                        side_effect_blocked=should_mock,
                        sandboxed=sandboxed,
                        unrecorded_tool_call=unrecorded_call,
                    )
                )
                continue

            result.replayed_steps.append(
                ReplayStepResult(
                    step_id=step.step_id,
                    kind=step.kind,
                    output=step.observation,
                    input_matches_original=input_matches,
                    sandboxed=sandboxed,
                )
            )
        result.sandbox_writes = overlay.to_dicts()
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
    if tool_name == "slack.post":
        return {
            "ok": True,
            "mocked": True,
            "branch": "edited-parameters",
            "tool_name": tool_name,
            "channel": parameters.get("channel", ""),
            "text": parameters.get("text", ""),
            "reason": "replay used edited parameters and did not post to Slack",
        }
    if tool_name in {"jira.add_comment", "jira.update_ticket", "jira.transition"}:
        return {
            "ok": True,
            "mocked": True,
            "branch": "edited-parameters",
            "tool_name": tool_name,
            "ticket_id": parameters.get("ticket_id"),
            "status": parameters.get("status"),
            "priority": parameters.get("priority"),
            "labels": parameters.get("labels"),
            "assignee": parameters.get("assignee"),
            "comment": parameters.get("comment") or parameters.get("body"),
            "customer_response_path_valid": bool(parameters.get("comment") or parameters.get("body")),
            "closure_allowed": tool_name == "jira.transition" and bool(parameters.get("comment")),
            "reason": "replay used edited Jira parameters without mutating Jira",
        }
    if tool_name == "jira.assign":
        return {
            "ok": True,
            "mocked": True,
            "branch": "edited-parameters",
            "tool_name": tool_name,
            "ticket_id": parameters.get("ticket_id"),
            "assignee": parameters.get("assignee"),
            "reason": "replay used edited Jira assignment without mutating Jira",
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
