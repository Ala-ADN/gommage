from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


def test_record_round_trips_and_validates() -> None:
    record = AgentExecutionRecord(run_id="run-1", jira_ticket_id="DEMO-1")
    builder = StepSnapshotBuilder(record)
    builder.add_llm_step(prompt="hello", response="world", intent="test llm")
    builder.add_tool_step(
        tool_name="jira.get_ticket",
        parameters={"ticket_id": "DEMO-1"},
        result={"summary": "Example"},
        side_effecting=False,
        intent="test tool",
    )
    record.complete()

    restored = AgentExecutionRecord.from_json(record.to_json())

    assert restored.run_id == "run-1"
    assert restored.status == "completed"
    assert restored.steps[0].llm is not None
    assert restored.steps[1].tool is not None
    assert restored.trace_hash() == record.trace_hash()
