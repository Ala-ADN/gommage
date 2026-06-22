"""Runnable demo Jira triage agent wrapped by Gommage proxies."""

from __future__ import annotations

from uuid import uuid4

from agent.tools.db_tool import DatabaseTool
from agent.tools.email_tool import EmailTool
from agent.tools.jira_tools import JiraToolset
from recorder.proxy.llm_proxy import LLMProxy, deterministic_llm
from recorder.proxy.tool_proxy import ToolProxy
from recorder.serializer.aer_schema import AgentExecutionRecord


def run_jira_triage(ticket_id: str = "DEMO-101") -> AgentExecutionRecord:
    record = AgentExecutionRecord(
        run_id=f"run-{uuid4().hex[:8]}",
        jira_ticket_id=ticket_id,
        agent_name="jira-triage-demo",
    )
    llm = LLMProxy(record, deterministic_llm)
    tools = ToolProxy(record)
    jira = JiraToolset()
    db = DatabaseTool()
    email = EmailTool()

    ticket = tools.call(
        "jira.get_ticket",
        jira.get_ticket,
        {"ticket_id": ticket_id},
        intent="Load ticket context",
    )
    llm.complete(
        f"Triage this Jira ticket: {ticket['summary']}",
        system_message="You are a careful support triage agent.",
        intent="Classify ticket",
        context={"ticket_id": ticket_id, "priority": ticket["priority"]},
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
