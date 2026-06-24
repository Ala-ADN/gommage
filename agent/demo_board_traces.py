"""Curated trace records for the Jira board-level demo."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


BASE_TIME = datetime(2026, 6, 24, 20, 15, 15, tzinfo=timezone.utc)
DEMO_LLM_MODEL = "gpt-4o-mini"


def build_board_demo_traces() -> list[AgentExecutionRecord]:
    """Return a small, clean trace set that makes the aggregate Sankey useful."""
    return [
        _silent_closure(),
        _safe_customer_response(),
        _safe_account_response(),
        _reassignment_path(),
        _sla_escalation_path(),
        _manual_review_path(),
    ]


def _record(
    *,
    run_id: str,
    ticket_id: str,
    issue: dict[str, Any],
    story: str,
    offset_minutes: int,
    agent_name: str = "jira-triage",
) -> AgentExecutionRecord:
    return AgentExecutionRecord(
        run_id=run_id,
        jira_ticket_id=ticket_id,
        agent_name=agent_name,
        started_at=_ts(offset_minutes),
        metadata={
            "agent_mode": "demo",
            "board_demo": True,
            "demo_story": story,
            "external_messages": "dry_run",
            "issue": issue,
            "llm_backend": "deterministic",
            "llm_model": DEMO_LLM_MODEL,
            "max_steps": 8,
            "tool_mode": "demo",
            "write_policy": "jira_only",
        },
    )


def _finish(record: AgentExecutionRecord, *, offset_minutes: int, status: str = "completed") -> AgentExecutionRecord:
    for index, step in enumerate(record.steps):
        step.timestamp = _ts(offset_minutes, seconds=index * 7)
    record.status = status
    record.completed_at = _ts(offset_minutes, seconds=len(record.steps) * 7 + 5)
    return record


def _issue(
    *,
    ticket_id: str,
    summary: str,
    description: str,
    priority: str,
    status: str,
    assignee: str,
    labels: list[str],
    reporter: str = "customer@example.com",
    owner: str = "support-ops@example.com",
) -> dict[str, Any]:
    return {
        "ticket_id": ticket_id,
        "summary": summary,
        "description": description,
        "priority": priority,
        "reporter": reporter,
        "owner": owner,
        "assignee": assignee,
        "labels": labels,
        "status": status,
        "issue_type": "Task",
        "source": "jira",
    }


def _silent_closure() -> AgentExecutionRecord:
    issue = _issue(
        ticket_id="SCRUM-18",
        summary="PROJ-4421: Billing export request closed without customer response",
        description=(
            "Customer reported that a billing export support request was marked Done, "
            "but they never received a response or confirmation."
        ),
        priority="High",
        status="Done",
        assignee="Backend",
        owner="backend-team@example.com",
        labels=["billing-export", "customer-impact", "silent-closure"],
    )
    record = _record(
        run_id="run-bd71afa8",
        ticket_id="SCRUM-18",
        issue=issue,
        story="proj-4421-silent-closure",
        offset_minutes=0,
        agent_name="jira-triage-proj-4421",
    )
    record.metadata.update(
        {
            "bad_path": {
                "closed_by": "agent",
                "customer_comment": None,
                "email_to": "",
                "route_team": "Backend",
            },
            "corrected_path_hint": {
                "email_to": "customer@example.com",
                "required_before_done": "customer-facing response or comment",
                "route_team": "Customer Success",
            },
        }
    )
    snapshots = StepSnapshotBuilder(record)
    snapshots.add_tool_step(
        tool_name="jira.get_ticket",
        parameters={"ticket_id": "SCRUM-18"},
        result={**issue, "comments": [], "closed_by": "agent", "customer_response_present": False},
        side_effecting=False,
        intent="Load Done ticket state",
        observation="Ticket is already Done with no customer-facing comments.",
        inference="The apparent success state conflicts with missing customer response evidence.",
        context={"ticket_id": "SCRUM-18", "status": "Done", "priority": "High"},
        latency_ms=42,
    )
    snapshots.add_llm_step(
        prompt=(
            "Triage Jira issue PROJ-4421.\n"
            "Fields: Status=Done, Priority=High, Assignee=Backend, Comments=0, Closed by=agent.\n"
            "Decide whether the customer received a valid response."
        ),
        response="Treat the Backend assignment as sufficient owner notification and close the workflow.",
        system_message="You are a Jira triage agent. Prefer automated routing when a technical owner is present.",
        model=DEMO_LLM_MODEL,
        intent="Classify silent closure",
        observation="The agent sees Done and Backend assignment but does not verify customer response.",
        inference="Bug: Done status was trusted more than customer-facing evidence.",
        context={
            "ticket_id": "SCRUM-18",
            "assignee": "Backend",
            "comments": 0,
            "customer_response_present": False,
        },
        latency_ms=118,
        token_count=78,
    )
    snapshots.add_tool_step(
        tool_name="jira.search",
        parameters={"jql": 'issue = "SCRUM-18" AND comments is not EMPTY', "max_results": 5},
        result=[],
        side_effecting=False,
        intent="Check response evidence",
        observation="No comment evidence found.",
        inference="The agent should block closure here, but the next step ignores this evidence.",
        context={"ticket_id": "SCRUM-18", "expected": "customer comment before Done"},
        latency_ms=67,
    )
    snapshots.add_llm_step(
        prompt="Route the Done ticket. Evidence search returned no comments. Choose notification target and closure action.",
        response='{"team":"Backend","notify_to":"","close":true,"rationale":"Backend owns export systems."}',
        system_message="Return compact routing JSON.",
        model=DEMO_LLM_MODEL,
        intent="Plan route and notification",
        observation="The plan routes to Backend and leaves notify_to empty.",
        inference="This is the silent-closure decision: Backend route, empty recipient, close=true.",
        context={
            "ticket_id": "SCRUM-18",
            "evidence": {"comments": 0},
            "planned_team": "Backend",
            "notify_to": "",
            "close": True,
        },
        latency_ms=96,
        token_count=64,
    )
    snapshots.add_tool_step(
        tool_name="email.send",
        parameters={
            "to": "",
            "subject": "Confirmation for SCRUM-18",
            "body": "Your support request has been routed and closed.",
        },
        result={
            "ok": True,
            "dry_run": True,
            "to": "",
            "accepted_recipients": [],
            "warning": "dry-run mailer accepted empty recipient",
        },
        side_effecting=True,
        intent="Send customer confirmation",
        observation="Confirmation email has no recipient.",
        inference="No customer could receive this confirmation.",
        context={"ticket_id": "SCRUM-18", "planned_team": "Backend", "notify_to": ""},
        latency_ms=211,
        metadata={"mock_recommended": True},
    )
    snapshots.add_tool_step(
        tool_name="jira.transition",
        parameters={"ticket_id": "SCRUM-18", "status": "Done", "comment": ""},
        result={
            "ok": True,
            "ticket_id": "SCRUM-18",
            "status": "Done",
            "comment_created": False,
            "closed_by": "agent",
        },
        side_effecting=True,
        intent="Close ticket",
        observation="Ticket remains Done without a customer comment.",
        inference="The closure hides the missing response unless the trace is replayed.",
        context={"ticket_id": "SCRUM-18", "customer_response_present": False, "status": "Done"},
        latency_ms=133,
        metadata={"mock_recommended": True},
    )
    return _finish(record, offset_minutes=0)


def _safe_customer_response() -> AgentExecutionRecord:
    issue = _issue(
        ticket_id="SCRUM-19",
        summary="PROJ-4422: Billing export request resolved with customer response",
        description="Customer requested billing export status after account migration.",
        priority="High",
        status="Done",
        assignee="Customer Success",
        labels=["billing-export", "customer-impact", "verified-response"],
    )
    record = _record(
        run_id="run-4c19d2aa",
        ticket_id="SCRUM-19",
        issue=issue,
        story="verified-customer-response",
        offset_minutes=2,
    )
    snapshots = StepSnapshotBuilder(record)
    _add_get_ticket(snapshots, issue, comments=1)
    _add_llm(
        snapshots,
        intent="Classify customer response",
        response="Customer-facing response exists. Continue only if closure evidence is preserved.",
        context={"ticket_id": "SCRUM-19", "comments": 1, "customer_response_present": True},
    )
    _add_search(
        snapshots,
        "SCRUM-19",
        result=[{"author": "Customer Success", "body": "Customer notified with export recovery steps."}],
        inference="Closure has customer-visible evidence.",
    )
    _add_llm(
        snapshots,
        intent="Plan customer response",
        response='{"notify_to":"customer@example.com","close":true,"reason":"response evidence found"}',
        context={"ticket_id": "SCRUM-19", "planned_team": "Customer Success", "notify_to": "customer@example.com"},
    )
    _add_email(snapshots, "SCRUM-19", to="customer@example.com")
    _add_transition(
        snapshots,
        "SCRUM-19",
        status="Done",
        comment="Customer response path verified before closure.",
        response_valid=True,
    )
    return _finish(record, offset_minutes=2)


def _safe_account_response() -> AgentExecutionRecord:
    issue = _issue(
        ticket_id="SCRUM-20",
        summary="PROJ-4423: Account access restored after support confirmation",
        description="Customer could not access the billing workspace after migration.",
        priority="Medium",
        status="Done",
        assignee="Customer Success",
        labels=["account-access", "customer-impact", "verified-response"],
    )
    record = _record(
        run_id="run-82cf31e4",
        ticket_id="SCRUM-20",
        issue=issue,
        story="verified-account-response",
        offset_minutes=4,
    )
    snapshots = StepSnapshotBuilder(record)
    _add_get_ticket(snapshots, issue, comments=2)
    _add_llm(
        snapshots,
        intent="Classify customer response",
        response="Access issue is resolved and the customer has already acknowledged the fix.",
        context={"ticket_id": "SCRUM-20", "comments": 2, "customer_response_present": True},
    )
    _add_search(
        snapshots,
        "SCRUM-20",
        result=[{"author": "Customer", "body": "Confirmed access is restored."}],
        inference="Customer acknowledgment makes closure safe.",
    )
    _add_llm(
        snapshots,
        intent="Plan customer response",
        response='{"notify_to":"customer@example.com","close":true,"reason":"customer acknowledged fix"}',
        context={"ticket_id": "SCRUM-20", "planned_team": "Customer Success", "notify_to": "customer@example.com"},
    )
    _add_email(snapshots, "SCRUM-20", to="customer@example.com", subject="Access restored for SCRUM-20")
    _add_transition(
        snapshots,
        "SCRUM-20",
        status="Done",
        comment="Customer confirmed account access was restored.",
        response_valid=True,
    )
    return _finish(record, offset_minutes=4)


def _reassignment_path() -> AgentExecutionRecord:
    issue = _issue(
        ticket_id="SCRUM-21",
        summary="PROJ-4424: Billing export routed to wrong owner",
        description="Billing export request needs a customer-facing owner before engineering review.",
        priority="High",
        status="In Progress",
        assignee="Backend",
        owner="backend-team@example.com",
        labels=["billing-export", "routing-gap", "customer-impact"],
    )
    record = _record(
        run_id="run-5f6a91c0",
        ticket_id="SCRUM-21",
        issue=issue,
        story="routing-correction",
        offset_minutes=6,
    )
    snapshots = StepSnapshotBuilder(record)
    _add_get_ticket(snapshots, issue, comments=0)
    _add_llm(
        snapshots,
        intent="Classify routing gap",
        response="Backend ownership does not satisfy customer communication requirements.",
        context={"ticket_id": "SCRUM-21", "assignee": "Backend", "customer_response_present": False},
    )
    _add_search(snapshots, "SCRUM-21", result=[], inference="No customer-facing response exists.")
    _add_llm(
        snapshots,
        intent="Plan reassignment",
        response='{"assignee":"Customer Success","comment":"Needs customer-facing follow-up before closure."}',
        context={"ticket_id": "SCRUM-21", "planned_team": "Customer Success", "close": False},
    )
    snapshots.add_tool_step(
        tool_name="jira.assign",
        parameters={"ticket_id": "SCRUM-21", "assignee": "Customer Success"},
        result={"ok": True, "ticket_id": "SCRUM-21", "assignee": "Customer Success"},
        side_effecting=True,
        intent="Reassign customer owner",
        observation="Ticket reassigned to Customer Success.",
        inference="The run creates an accountable customer-response owner instead of closing.",
        context={"ticket_id": "SCRUM-21", "assigned_team": "Customer Success"},
        latency_ms=98,
        metadata={"mock_recommended": True},
    )
    _add_comment(
        snapshots,
        "SCRUM-21",
        body="Customer Success must respond before this request can be closed.",
        inference="The ticket now contains explicit follow-up guidance.",
    )
    return _finish(record, offset_minutes=6)


def _sla_escalation_path() -> AgentExecutionRecord:
    issue = _issue(
        ticket_id="SCRUM-22",
        summary="PROJ-4425: Enterprise export request at SLA risk",
        description="Enterprise customer has waited more than four hours for an export recovery update.",
        priority="Highest",
        status="In Progress",
        assignee="Support Ops",
        labels=["billing-export", "enterprise", "sla-risk"],
    )
    record = _record(
        run_id="run-1dc739b5",
        ticket_id="SCRUM-22",
        issue=issue,
        story="sla-escalation",
        offset_minutes=8,
    )
    snapshots = StepSnapshotBuilder(record)
    _add_get_ticket(snapshots, issue, comments=0)
    _add_llm(
        snapshots,
        intent="Classify SLA risk",
        response="Priority and wait time require escalation before any closure action.",
        context={"ticket_id": "SCRUM-22", "priority": "Highest", "sla_risk": True},
    )
    _add_search(snapshots, "SCRUM-22", result=[], inference="No recent customer update was found.")
    _add_llm(
        snapshots,
        intent="Plan escalation",
        response='{"channel":"support-war-room","status":"Escalated","notify_customer":true}',
        context={"ticket_id": "SCRUM-22", "channel": "support-war-room", "close": False},
    )
    snapshots.add_tool_step(
        tool_name="slack.post",
        parameters={"channel": "support-war-room", "text": "SCRUM-22 is at SLA risk."},
        result={"ok": True, "dry_run": True, "channel": "support-war-room"},
        side_effecting=True,
        intent="Escalate SLA risk",
        observation="Escalation message prepared for support war room.",
        inference="Replay blocks this external message while preserving the escalation decision.",
        context={"ticket_id": "SCRUM-22", "sla_risk": True},
        latency_ms=124,
        metadata={"mock_recommended": True},
    )
    _add_comment(
        snapshots,
        "SCRUM-22",
        body="Escalated due to SLA risk; customer update required before closure.",
        inference="The ticket records why escalation happened.",
    )
    _add_transition(
        snapshots,
        "SCRUM-22",
        status="Escalated",
        comment="SLA risk escalated for customer follow-up.",
        response_valid=False,
    )
    return _finish(record, offset_minutes=8)


def _manual_review_path() -> AgentExecutionRecord:
    issue = _issue(
        ticket_id="SCRUM-23",
        summary="PROJ-4426: Webhook retry request missing evidence",
        description="Customer asked for a webhook retry, but the original delivery evidence is incomplete.",
        priority="Medium",
        status="In Progress",
        assignee="Support Ops",
        labels=["webhook", "manual-review", "missing-evidence"],
    )
    record = _record(
        run_id="run-9e2a47d1",
        ticket_id="SCRUM-23",
        issue=issue,
        story="manual-review-required",
        offset_minutes=10,
    )
    snapshots = StepSnapshotBuilder(record)
    _add_get_ticket(snapshots, issue, comments=0)
    _add_llm(
        snapshots,
        intent="Classify missing evidence",
        response="Evidence is incomplete; do not retry or close automatically.",
        context={"ticket_id": "SCRUM-23", "evidence_complete": False, "close": False},
    )
    snapshots.add_tool_step(
        tool_name="jira.search",
        parameters={"jql": 'issue = "SCRUM-23" AND text ~ "webhook delivery"', "max_results": 5},
        result=[],
        side_effecting=False,
        intent="Check response evidence",
        observation="Search returned no delivery evidence.",
        inference="The agent cannot prove a retry is safe.",
        context={"ticket_id": "SCRUM-23", "expected": "delivery evidence before retry"},
        latency_ms=73,
        error="No delivery evidence found",
    )
    _add_llm(
        snapshots,
        intent="Plan manual review",
        response='{"close":false,"action":"add_comment","reason":"missing delivery evidence"}',
        context={"ticket_id": "SCRUM-23", "close": False, "manual_review": True},
    )
    _add_comment(
        snapshots,
        "SCRUM-23",
        body="Manual review required before webhook retry or closure.",
        inference="The run stops short of a risky side effect.",
    )
    return _finish(record, offset_minutes=10, status="partial")


def _add_get_ticket(snapshots: StepSnapshotBuilder, issue: dict[str, Any], *, comments: int) -> None:
    snapshots.add_tool_step(
        tool_name="jira.get_ticket",
        parameters={"ticket_id": issue["ticket_id"]},
        result={**issue, "comments": [{}] * comments, "customer_response_present": comments > 0},
        side_effecting=False,
        intent="Load ticket context",
        observation="Ticket fields loaded from Jira.",
        inference="The agent has status, priority, owner, and visible response evidence.",
        context={
            "ticket_id": issue["ticket_id"],
            "status": issue["status"],
            "priority": issue["priority"],
        },
        latency_ms=44,
    )


def _add_llm(
    snapshots: StepSnapshotBuilder,
    *,
    intent: str,
    response: str,
    context: dict[str, Any],
) -> None:
    snapshots.add_llm_step(
        prompt=f"Evaluate the Jira ticket and choose the next safe action. Context: {context}",
        response=response,
        system_message="You are a Jira triage agent. Verify customer-facing evidence before side effects.",
        model=DEMO_LLM_MODEL,
        intent=intent,
        observation=response,
        inference="The model converts ticket evidence into the next action plan.",
        context=context,
        latency_ms=102,
        token_count=72,
    )


def _add_search(
    snapshots: StepSnapshotBuilder,
    ticket_id: str,
    *,
    result: list[dict[str, Any]],
    inference: str,
) -> None:
    snapshots.add_tool_step(
        tool_name="jira.search",
        parameters={"jql": f'issue = "{ticket_id}" AND comments is not EMPTY', "max_results": 5},
        result=result,
        side_effecting=False,
        intent="Check response evidence",
        observation=f"Found {len(result)} customer-response artifact(s).",
        inference=inference,
        context={"ticket_id": ticket_id, "comments": len(result)},
        latency_ms=68,
    )


def _add_email(
    snapshots: StepSnapshotBuilder,
    ticket_id: str,
    *,
    to: str,
    subject: str | None = None,
) -> None:
    snapshots.add_tool_step(
        tool_name="email.send",
        parameters={
            "to": to,
            "subject": subject or f"Update for {ticket_id}",
            "body": "We verified the response path and recorded the customer update.",
        },
        result={"ok": True, "dry_run": True, "to": to, "accepted_recipients": [to]},
        side_effecting=True,
        intent="Send customer update",
        observation="Customer update prepared.",
        inference="Replay can block the send while preserving the intended recipient.",
        context={"ticket_id": ticket_id, "notify_to": to},
        latency_ms=180,
        metadata={"mock_recommended": True},
    )


def _add_transition(
    snapshots: StepSnapshotBuilder,
    ticket_id: str,
    *,
    status: str,
    comment: str,
    response_valid: bool,
) -> None:
    snapshots.add_tool_step(
        tool_name="jira.transition",
        parameters={"ticket_id": ticket_id, "status": status, "comment": comment},
        result={
            "ok": True,
            "ticket_id": ticket_id,
            "status": status,
            "comment_created": bool(comment),
        },
        side_effecting=True,
        intent="Transition ticket",
        observation=f"Ticket transition prepared: {status}.",
        inference="The transition is safe only when response evidence is present or escalation is explicit.",
        context={"ticket_id": ticket_id, "customer_response_present": response_valid, "status": status},
        latency_ms=121,
        metadata={"mock_recommended": True},
    )


def _add_comment(
    snapshots: StepSnapshotBuilder,
    ticket_id: str,
    *,
    body: str,
    inference: str,
) -> None:
    snapshots.add_tool_step(
        tool_name="jira.add_comment",
        parameters={"ticket_id": ticket_id, "body": body},
        result={"ok": True, "ticket_id": ticket_id, "comment_created": True},
        side_effecting=True,
        intent="Add Jira comment",
        observation="Jira comment prepared.",
        inference=inference,
        context={"ticket_id": ticket_id, "comment_created": True},
        latency_ms=110,
        metadata={"mock_recommended": True},
    )


def _ts(offset_minutes: int, *, seconds: int = 0) -> str:
    return (BASE_TIME + timedelta(minutes=offset_minutes, seconds=seconds)).isoformat()
