from recorder.serializer.aer_schema import AgentExecutionRecord, TraceIndex
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


def test_trace_index_derives_dashboard_metadata() -> None:
    record = AgentExecutionRecord(run_id="run-2", jira_ticket_id="DEMO-2")
    builder = StepSnapshotBuilder(record)
    builder.add_llm_step(prompt="classify", response="decision", intent="Classify ticket")
    builder.add_tool_step(
        tool_name="jira.update_ticket",
        parameters={"ticket_id": "DEMO-2", "priority": "High"},
        result={"ok": True},
        side_effecting=True,
        intent="Update Jira triage fields",
    )
    record.complete()

    index = TraceIndex.from_record(record)

    assert index.step_count == 2
    assert index.llm_call_count == 1
    assert index.tool_call_count == 1
    assert index.side_effect_count == 1
    assert index.category_distribution["decision_making"] == 1
    assert index.category_distribution["data_mutation"] == 1
    assert index.canonical_path == [
        "llm:classify_ticket",
        "tool:jira.update_ticket:{priority,ticket_id}",
    ]
