from agent.jira_triage_agent import run_jira_triage
from replay.ui.fix_issue_brief import generate_fix_issue_brief


def test_fix_issue_brief_fallback_includes_replay_evidence() -> None:
    record = run_jira_triage(
        "SCRUM-6",
        issue={
            "summary": "Duplicate billing export notification",
            "description": "Two emails were sent for one export.",
            "priority": "High",
            "reporter": "ops@example.com",
            "owner": "billing-owner@example.com",
        },
    )

    brief = generate_fix_issue_brief(
        record,
        replay_metrics={
            "replay_fidelity": 1.0,
            "mock_recall": 1.0,
            "side_effects_blocked": 1,
        },
        backend="fallback",
    )

    assert brief["source"] == "fallback"
    assert "SCRUM-6" in brief["summary"]
    assert any("email.send" in item for item in brief["debug_evidence"])
    assert any("blocked 1" in paragraph for paragraph in brief["description_paragraphs"])
