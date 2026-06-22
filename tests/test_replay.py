from agent.jira_triage_agent import run_jira_triage
from evaluation.metrics.mock_recall import mock_recall_rate
from evaluation.metrics.replay_fidelity import replay_fidelity_score
from replay.engine.replay_runner import ReplayRunner
from replay.engine.step_editor import StepEdit, StepEditor


def test_replay_blocks_recorded_side_effects() -> None:
    record = run_jira_triage("DEMO-101")

    result = ReplayRunner(record).replay()

    assert result.side_effects_blocked == 1
    assert replay_fidelity_score(result) == 1.0
    assert mock_recall_rate(record, result) == 1.0


def test_step_editor_creates_divergence_without_mutating_original() -> None:
    record = run_jira_triage("DEMO-101")
    llm_step = next(step for step in record.steps if step.llm is not None)
    original_prompt = llm_step.llm.prompt

    edited = StepEditor(record).apply(
        [StepEdit(step_id=llm_step.step_id, prompt="Use a safer triage prompt.")]
    )
    result = ReplayRunner(record).replay(
        [StepEdit(step_id=llm_step.step_id, prompt="Use a safer triage prompt.")]
    )

    assert llm_step.llm.prompt == original_prompt
    assert edited.steps[llm_step.step_id - 1].llm.prompt == "Use a safer triage prompt."
    assert replay_fidelity_score(result) < 1.0
    assert any(divergence.field == "llm.prompt" for divergence in result.divergences)
