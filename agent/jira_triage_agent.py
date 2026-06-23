"""Runnable demo Jira triage agent wrapped by Gommage proxies."""

from __future__ import annotations

import os
from uuid import uuid4

from agent.tools.db_tool import DatabaseTool
from agent.tools.email_tool import EmailTool
from agent.tools.jira_tools import JiraToolset
from recorder.proxy.llm_proxy import LLMProxy, deterministic_llm
from recorder.proxy.openai_client import OpenAICompletion
from recorder.proxy.tool_proxy import ToolProxy
from recorder.serializer.aer_schema import AgentExecutionRecord


def _normalize_issue(ticket_id: str, issue: dict | None) -> dict | None:
    if not issue:
        return None
    return {
        "ticket_id": issue.get("ticket_id") or issue.get("key") or ticket_id,
        "summary": issue.get("summary") or "Untitled Jira issue",
        "description": issue.get("description") or "",
        "priority": issue.get("priority") or "medium",
        "reporter": issue.get("reporter") or "unknown reporter",
        "owner": issue.get("owner") or issue.get("assignee") or issue.get("reporter") or "support-team@example.com",
        "assignee": issue.get("assignee"),
        "labels": issue.get("labels") or [],
        "status": issue.get("status"),
        "issue_type": issue.get("issue_type"),
        "source": issue.get("source") or "jira",
    }


def _resolve_llm_backend(backend: str | None) -> tuple[object, str, str]:
    choice = (backend or os.getenv("GOMMAGE_LLM_BACKEND", "deterministic")).strip().lower()
    if choice == "auto":
        choice = "openai" if os.getenv("OPENAI_API_KEY") else "deterministic"
    if choice in {"openai", "real"}:
        completion = OpenAICompletion()
        return completion, completion.model, "openai"
    if choice in {"deterministic", "demo", "local", ""}:
        return deterministic_llm, "deterministic-demo", "deterministic"
    raise ValueError(f"unsupported llm backend: {backend}")


def run_jira_triage(
    ticket_id: str = "DEMO-101",
    *,
    issue: dict | None = None,
    llm_backend: str | None = None,
) -> AgentExecutionRecord:
    normalized_issue = _normalize_issue(ticket_id, issue)
    llm_callable, llm_model, resolved_backend = _resolve_llm_backend(llm_backend)
    record = AgentExecutionRecord(
        run_id=f"run-{uuid4().hex[:8]}",
        jira_ticket_id=ticket_id,
        agent_name="jira-triage-demo",
        metadata={
            **({"issue": normalized_issue} if normalized_issue else {}),
            "llm_backend": resolved_backend,
            "llm_model": llm_model,
        },
    )
    llm = LLMProxy(record, llm_callable, model=llm_model)
    tools = ToolProxy(record)
    jira = JiraToolset(
        tickets={ticket_id: normalized_issue} if normalized_issue else {}
    )
    db = DatabaseTool()
    email = EmailTool()

    ticket = tools.call(
        "jira.get_ticket",
        jira.get_ticket,
        {"ticket_id": ticket_id},
        intent="Load ticket context",
    )
    llm.complete(
        f"Triage this Jira ticket: {ticket['summary']}\n\nDescription: {ticket.get('description', '')}",
        system_message="You are a careful support triage agent.",
        intent="Classify ticket",
        context={
            "ticket_id": ticket_id,
            "priority": ticket["priority"],
            "status": ticket.get("status"),
            "labels": ticket.get("labels", []),
            "reporter": ticket.get("reporter"),
            "assignee": ticket.get("assignee"),
        },
    )
    rows = tools.call(
        "db.query",
        db.query,
        {"sql": f"SELECT * FROM exports WHERE ticket_id = '{ticket_id}'"},
        intent="Gather database evidence",
    )
    llm.complete(
        "Decide whether the ticket owner needs an email.",
        system_message="Use evidence before taking action.",
        intent="Plan owner notification",
        context={"owner": ticket["owner"], "db_rows": rows},
    )
    tools.call(
        "email.send",
        email.send_email,
        {
            "to": ticket["owner"],
            "subject": f"Follow-up needed for {ticket_id}",
            "body": "The export failure appears related to migration state. Please review.",
        },
        intent="Notify owner",
    )
    record.complete()
    return record


if __name__ == "__main__":
    print(run_jira_triage().to_json())
