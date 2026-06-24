import json

from agent.jira_triage_agent import AgentRuntimeConfig, run_jira_triage_live


class FakePlanner:
    model = "fake-planner"
    last_metadata = {}

    def __init__(self, responses):
        self.responses = list(responses)

    def __call__(self, prompt: str, **_: object) -> str:
        self.last_metadata = {"provider": "fake"}
        if not self.responses:
            return json.dumps({"action": "final", "done": True, "rationale": "done"})
        return json.dumps(self.responses.pop(0))


def test_live_agent_allows_jira_write_and_records_steps(monkeypatch) -> None:
    planner = FakePlanner(
        [
            {
                "action": "tool",
                "tool_name": "jira.add_comment",
                "parameters": {"ticket_id": "REAL-1", "body": "Triage note"},
                "rationale": "Add triage note",
                "done": False,
            },
            {"action": "final", "done": True, "rationale": "commented"},
        ]
    )
    monkeypatch.setattr("agent.jira_triage_agent._resolve_llm_backend", lambda _: (planner, planner.model, "fake"))

    record = run_jira_triage_live(
        "REAL-1",
        issue={"summary": "Production issue", "description": "Needs triage"},
        config=AgentRuntimeConfig(agent_mode="live", tool_mode="mock", write_policy="jira_only", max_steps=4),
    )

    assert record.status == "completed"
    assert record.metadata["agent_mode"] == "live"
    assert any(step.tool and step.tool.tool_name == "jira.add_comment" for step in record.steps)


def test_live_agent_dry_runs_external_messages(monkeypatch) -> None:
    planner = FakePlanner(
        [
            {
                "action": "tool",
                "tool_name": "email.send",
                "parameters": {"to": "a@example.com", "subject": "Heads up", "body": "Review this"},
                "rationale": "Notify owner",
                "done": False,
            },
            {"action": "final", "done": True, "rationale": "proposed notification"},
        ]
    )
    monkeypatch.setattr("agent.jira_triage_agent._resolve_llm_backend", lambda _: (planner, planner.model, "fake"))

    record = run_jira_triage_live(
        "REAL-1",
        issue={"summary": "Production issue", "description": "Needs triage"},
        config=AgentRuntimeConfig(
            agent_mode="live",
            tool_mode="mock",
            write_policy="jira_only",
            external_messages="dry_run",
            max_steps=4,
        ),
    )

    email_step = next(step for step in record.steps if step.tool and step.tool.tool_name == "email.send")
    assert email_step.tool is not None
    assert email_step.tool.side_effecting is True
    assert email_step.tool.result["dry_run"] is True


def test_live_agent_blocks_cross_issue_jira_writes(monkeypatch) -> None:
    planner = FakePlanner(
        [
            {
                "action": "tool",
                "tool_name": "jira.add_comment",
                "parameters": {"ticket_id": "OTHER-1", "body": "Wrong issue"},
                "rationale": "Comment elsewhere",
                "done": False,
            }
        ]
    )
    monkeypatch.setattr("agent.jira_triage_agent._resolve_llm_backend", lambda _: (planner, planner.model, "fake"))

    record = run_jira_triage_live(
        "REAL-1",
        issue={"summary": "Production issue", "description": "Needs triage"},
        config=AgentRuntimeConfig(agent_mode="live", tool_mode="mock", write_policy="jira_only", max_steps=4),
    )

    assert record.status == "blocked"
    assert record.metadata["blocked_action"]["reason"] == "Jira writes are restricted to the active issue"
