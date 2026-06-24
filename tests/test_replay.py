from agent.demo_board_traces import build_board_demo_traces
from agent.jira_triage_agent import run_jira_triage
from evaluation.metrics.mock_recall import mock_recall_rate
from evaluation.metrics.replay_fidelity import replay_fidelity_score
from replay.engine.replay_runner import ReplayRunner
from replay.engine.step_editor import StepEdit, StepEditor


def test_replay_blocks_recorded_side_effects() -> None:
    record = run_jira_triage("DEMO-106")

    result = ReplayRunner(record).replay()

    assert result.side_effects_blocked == 3
    assert replay_fidelity_score(result) == 1.0
    assert mock_recall_rate(record, result) == 1.0


def test_jira_triage_uses_real_issue_payload() -> None:
    record = run_jira_triage(
        "REAL-1",
        issue={
            "summary": "Production billing export sends duplicate emails",
            "description": "Customer reports two emails for every export.",
            "priority": "Highest",
            "reporter": "ops@example.com",
            "assignee": "billing-owner@example.com",
            "owner": "billing-owner@example.com",
            "labels": ["billing", "sensitive"],
            "status": "To Do",
            "issue_type": "Task",
        },
    )

    classify_step = next(step for step in record.steps if step.intent == "Classify ticket")

    assert record.metadata["issue"]["summary"] == "Production billing export sends duplicate emails"
    assert classify_step.llm is not None
    assert "Production billing export sends duplicate emails" in classify_step.llm.prompt
    assert "Customer reports two emails" in classify_step.llm.prompt
    assert classify_step.context["labels"] == ["billing", "sensitive"]
    assert record.metadata["llm_backend"] == "deterministic"
    assert record.metadata["llm_model"] == "deterministic-demo"


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


def test_proj_4421_story_records_silent_closure_trace() -> None:
    record = run_jira_triage(
        "SCRUM-18",
        issue={
            "summary": "PROJ-4421: Billing export request closed without customer response",
            "description": "Customer reported that the request was marked Done without a response.",
            "priority": "High",
            "reporter": "customer@example.com",
            "assignee": "Backend",
            "status": "Done",
            "labels": ["billing-export", "customer-impact", "silent-closure"],
        },
    )

    email_step = next(step for step in record.steps if step.tool and step.tool.tool_name == "email.send")
    close_step = next(step for step in record.steps if step.tool and step.tool.tool_name == "jira.transition")

    assert record.metadata["demo_story"] == "proj-4421-silent-closure"
    assert record.agent_name == "jira-triage-proj-4421"
    assert record.metadata["llm_model"] == "gpt-4o-mini"
    assert email_step.tool is not None
    assert email_step.tool.parameters["to"] == ""
    assert close_step.tool is not None
    assert close_step.tool.parameters["status"] == "Done"
    assert close_step.tool.parameters["comment"] == ""


def test_tool_parameter_edit_replays_corrected_safe_branch() -> None:
    record = run_jira_triage(
        "SCRUM-18",
        issue={
            "summary": "PROJ-4421: Billing export request closed without customer response",
            "priority": "High",
            "reporter": "customer@example.com",
            "assignee": "Backend",
            "status": "Done",
        },
    )
    email_step = next(step for step in record.steps if step.tool and step.tool.tool_name == "email.send")
    edited_params = {
        **email_step.tool.parameters,
        "to": "customer@example.com",
        "body": "We found the routing issue and reopened the customer response path.",
    }

    result = ReplayRunner(record).replay(
        [StepEdit(step_id=email_step.step_id, tool_parameters=edited_params)]
    )
    replay_email = next(step for step in result.replayed_steps if step.step_id == email_step.step_id)
    replay_close = next(
        step
        for step in result.replayed_steps
        if isinstance(step.output, dict) and step.output.get("tool_name") == "jira.transition"
    )

    assert replay_email.side_effect_blocked is True
    assert replay_email.input_matches_original is False
    assert replay_email.output["to"] == "customer@example.com"
    assert replay_email.output["branch"] == "edited-parameters"
    assert replay_close.output["closure_allowed"] is True
    assert replay_close.output["customer_response_path_valid"] is True
    assert any(divergence.field == "tool.parameters" for divergence in result.divergences)


def test_tool_parameter_edit_uses_sandbox_overlay_prompt() -> None:
    record = run_jira_triage(
        "EMAIL-1",
        issue={
            "summary": "Cyclic owner handoff zzqxp",
            "description": "Automation assigned work back to requester.",
            "priority": "Medium",
            "reporter": "requester@example.com",
            "assignee": "requester@example.com",
            "owner": "requester@example.com",
            "labels": ["routing-gap"],
            "status": "In Progress",
        },
    )
    email_step = next(step for step in record.steps if step.tool and step.tool.tool_name == "email.send")
    edited_params = {**email_step.tool.parameters, "to": "customer@example.com"}

    result = ReplayRunner(record).replay(
        [StepEdit(step_id=email_step.step_id, tool_parameters=edited_params, note="fork")]
    )
    replay_email = next(step for step in result.replayed_steps if step.step_id == email_step.step_id)

    assert result.mode == "sandbox_overlay"
    assert result.sandbox_from_step_id == email_step.step_id
    assert replay_email.sandboxed is True
    assert replay_email.unrecorded_tool_call is not None
    assert replay_email.unrecorded_tool_call["choices"] == [
        "manual_response",
        "execute_live_unsafe",
        "abort_replay",
    ]
    assert any(write["tool_name"] == "email.send" for write in result.sandbox_writes)


def test_board_demo_traces_are_curated_and_branchy() -> None:
    records = build_board_demo_traces()
    bad_tokens = (
        "DEMO-",
        "REAL-API",
        "Agent closed",
        "confirmation email to no one",
        "deterministic-demo",
        "silent-failure",
        "empty recipient accepted",
    )

    assert len(records) == 6
    assert len({record.run_id for record in records}) == len(records)
    assert {record.jira_ticket_id for record in records} == {
        "SCRUM-18",
        "SCRUM-19",
        "SCRUM-20",
        "SCRUM-21",
        "SCRUM-22",
        "SCRUM-23",
    }
    for record in records:
        record.validate()
        payload = record.to_json()
        assert not any(token in payload for token in bad_tokens)

    tool_names = {
        step.tool.tool_name
        for record in records
        for step in record.steps
        if step.tool is not None
    }
    assert {"email.send", "jira.assign", "slack.post", "jira.add_comment"} <= tool_names
