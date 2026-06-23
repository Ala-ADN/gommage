from agent.tools.email_tool import EmailTool
from recorder.proxy.llm_proxy import LLMProxy, deterministic_llm
from recorder.proxy.mock_registry import MockRegistry
from recorder.proxy.tool_proxy import ToolProxy
from recorder.serializer.aer_schema import AgentExecutionRecord


def test_proxies_record_llm_and_tool_steps() -> None:
    record = AgentExecutionRecord(run_id="run-1", jira_ticket_id="DEMO-1")
    llm = LLMProxy(record, deterministic_llm)
    tools = ToolProxy(record)

    response = llm.complete("Triage ticket", intent="triage")
    tool_result = tools.call(
        "jira.get_ticket",
        lambda ticket_id: {"ticket_id": ticket_id},
        {"ticket_id": "DEMO-1"},
    )

    assert "Classify" in response
    assert tool_result == {"ticket_id": "DEMO-1"}
    assert [step.kind for step in record.steps] == ["llm", "tool"]


def test_llm_proxy_records_runtime_metadata() -> None:
    record = AgentExecutionRecord(run_id="run-1", jira_ticket_id="DEMO-1")

    class RuntimeMetadataLLM:
        model = "test-model"
        last_metadata = {}

        def __call__(self, prompt: str, **_: object) -> str:
            self.last_metadata = {"provider": "test", "response_id": "resp-1"}
            return f"handled {prompt}"

    llm = LLMProxy(record, RuntimeMetadataLLM())

    llm.complete("ticket", intent="triage")

    assert record.steps[0].llm is not None
    assert record.steps[0].llm.model == "test-model"
    assert record.steps[0].llm.metadata["provider"] == "test"
    assert record.steps[0].llm.metadata["response_id"] == "resp-1"


def test_mock_registry_detects_side_effecting_tools_and_mutating_sql() -> None:
    registry = MockRegistry()

    assert registry.is_side_effecting("email.send", {"to": "a@example.com"})
    assert registry.is_side_effecting("db.query", {"sql": "UPDATE users SET x = 1"})
    assert not registry.is_side_effecting("db.query", {"sql": "SELECT * FROM users"})


def test_tool_proxy_safe_mode_mocks_side_effects() -> None:
    record = AgentExecutionRecord(run_id="run-1", jira_ticket_id="DEMO-1")
    tools = ToolProxy(record, safe_mode=True)
    called = False

    def send_email(to: str) -> dict[str, str]:
        nonlocal called
        called = True
        return {"to": to}

    result = tools.call("email.send", send_email, {"to": "a@example.com"})

    assert called is False
    assert result["mocked"] is True
    assert record.steps[0].tool is not None
    assert record.steps[0].tool.mocked is True


def test_email_tool_writes_outbox_file(tmp_path) -> None:
    tool = EmailTool(outbox_root=tmp_path)

    result = tool.send_email("a@example.com", "Subject", "Body")

    assert result["ok"] is True
    assert (tmp_path / f"{result['message_id']}.json").exists()
