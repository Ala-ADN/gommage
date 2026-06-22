"""Mock recall metric."""

from __future__ import annotations

from recorder.serializer.aer_schema import AgentExecutionRecord
from replay.engine.replay_runner import ReplayResult


def mock_recall_rate(record: AgentExecutionRecord, result: ReplayResult) -> float:
    side_effecting_step_ids = {
        step.step_id
        for step in record.steps
        if step.tool is not None and step.tool.side_effecting
    }
    if not side_effecting_step_ids:
        return 1.0
    replayed_mocked_ids = {
        step.step_id
        for step in result.replayed_steps
        if step.mocked or step.side_effect_blocked
    }
    return len(side_effecting_step_ids & replayed_mocked_ids) / len(side_effecting_step_ids)
