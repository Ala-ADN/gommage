import uuid
from recorder.serializer.aer_schema import AgentExecutionRecord, AERStep, ToolCall, LLMExchange

def generate_golden_path():
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    record = AgentExecutionRecord(run_id=run_id, jira_ticket_id="TEST-1", agent_name="support-agent")
    
    # Step 1: Load ticket
    record.add_step(AERStep(
        step_id=1, kind="tool", intent="Load ticket context",
        context={"ticket_id": "TEST-1"},
        tool=ToolCall(tool_name="jira.get_ticket", parameters={"ticket_id": "TEST-1"}, result={"summary": "DB slow"}, latency_ms=120)
    ))
    
    # Step 2: Classify
    record.add_step(AERStep(
        step_id=2, kind="llm", intent="Classify ticket",
        context={"summary": "DB slow", "priority": "medium"},
        llm=LLMExchange(prompt="Triage", response="Performance issue", latency_ms=400)
    ))
    
    # Step 3: DB Query
    record.add_step(AERStep(
        step_id=3, kind="tool", intent="Gather database evidence",
        context={"ticket_id": "TEST-1"},
        tool=ToolCall(tool_name="db.query", parameters={"sql": "SELECT * FROM logs"}, result=["slow query found"], latency_ms=250)
    ))
    
    # Step 4: Notify Owner
    record.add_step(AERStep(
        step_id=4, kind="tool", intent="Notify owner",
        context={"owner": "alice@example.com"},
        tool=ToolCall(tool_name="email.send", parameters={"to": "alice@example.com", "subject": "Fix DB"}, result="Sent", side_effecting=True, latency_ms=800)
    ))
    
    record.complete()
    return record

def generate_error_path():
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    record = AgentExecutionRecord(run_id=run_id, jira_ticket_id="TEST-2", agent_name="support-agent")
    
    # Step 1: Load ticket
    record.add_step(AERStep(
        step_id=1, kind="tool", intent="Load ticket context",
        context={"ticket_id": "TEST-2"},
        tool=ToolCall(tool_name="jira.get_ticket", parameters={"ticket_id": "TEST-2"}, result={"summary": "DB down"}, latency_ms=130)
    ))
    
    # Step 2: Classify
    record.add_step(AERStep(
        step_id=2, kind="llm", intent="Classify ticket",
        context={"summary": "DB down", "priority": "high"},
        llm=LLMExchange(prompt="Triage", response="Critical infrastructure issue", latency_ms=450)
    ))
    
    # Step 3: DB Query (ERROR)
    record.add_step(AERStep(
        step_id=3, kind="tool", intent="Gather database evidence",
        context={"ticket_id": "TEST-2"},
        tool=ToolCall(tool_name="db.query", parameters={"sql": "SELECT * FROM logs"}, result=None, error="Connection timeout", latency_ms=5000)
    ))
    
    # Step 4: Fallback Comment
    record.add_step(AERStep(
        step_id=4, kind="tool", intent="Add comment to ticket",
        context={"ticket_id": "TEST-2", "error": "Connection timeout"},
        tool=ToolCall(tool_name="jira.add_comment", parameters={"ticket_id": "TEST-2", "body": "Could not query DB"}, result="Added", side_effecting=True, latency_ms=150)
    ))
    
    record.complete("failed")
    return record

def generate_escalation_path():
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    record = AgentExecutionRecord(run_id=run_id, jira_ticket_id="TEST-3", agent_name="support-agent")
    
    # Step 1: Load ticket
    record.add_step(AERStep(
        step_id=1, kind="tool", intent="Load ticket context",
        context={"ticket_id": "TEST-3"},
        tool=ToolCall(tool_name="jira.get_ticket", parameters={"ticket_id": "TEST-3"}, result={"summary": "App crashing"}, latency_ms=110)
    ))
    
    # Step 2: Classify
    record.add_step(AERStep(
        step_id=2, kind="llm", intent="Classify ticket",
        context={"summary": "App crashing", "priority": "urgent"},
        llm=LLMExchange(prompt="Triage", response="Urgent crash, escalate immediately", latency_ms=300)
    ))
    
    # Step 3: Escalate to Slack
    record.add_step(AERStep(
        step_id=3, kind="tool", intent="Escalate issue",
        context={"channel": "#on-call"},
        tool=ToolCall(tool_name="slack.send", parameters={"channel": "#on-call", "message": "App crashing"}, result="Sent", side_effecting=True, latency_ms=200)
    ))
    
    record.complete()
    return record

if __name__ == "__main__":
    from recorder.storage.local_store import LocalTraceStore
    store = LocalTraceStore(".gommage/traces")
    
    r1 = generate_golden_path()
    r2 = generate_error_path()
    r3 = generate_escalation_path()
    
    store.save(r1)
    store.save(r2)
    store.save(r3)
    
    print(f"Generated realistic traces: {r1.run_id}, {r2.run_id}, {r3.run_id}")
