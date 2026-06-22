"""Runnable demo Jira triage agent wrapped by Gommage adapters."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent.tools.db_tool import DatabaseTool
from agent.tools.email_tool import EmailTool
from agent.tools.jira_tools import JiraToolset
from recorder.adapters.base import BaseLLMAdapter
from recorder.adapters.function_adapter import gommage_tool
from recorder.proxy.llm_proxy import deterministic_llm
from recorder.serializer.aer_schema import AgentExecutionRecord


class DemoLLMAdapter(BaseLLMAdapter):
    def complete(
        self,
        prompt: str,
        *,
        system_message: str = "",
        intent: str = "llm completion",
        context: dict[str, Any] | None = None,
    ) -> str:
        self.capture_input(prompt, system_message, context)
        response = deterministic_llm(prompt, system_message=system_message, context=context)
        token_usage = {"total": len(prompt.split()) + len(str(response).split())}
        self.capture_output(str(response), token_usage=token_usage, model_name="deterministic-demo")
        if self.record.steps:
            self.record.steps[-1].intent = intent
            if context:
                self.record.steps[-1].context = context
        return str(response)


def run_jira_triage(ticket_id: str = "DEMO-101") -> AgentExecutionRecord:
    record = AgentExecutionRecord(
        run_id=f"run-{uuid4().hex[:8]}",
        jira_ticket_id=ticket_id,
        agent_name="jira-triage-demo",
    )
    llm = DemoLLMAdapter(record)
    jira = JiraToolset()
    db = DatabaseTool()
    email = EmailTool()

    get_ticket_tool = gommage_tool(name="jira.get_ticket", record=record)(jira.get_ticket)
    db_query_tool = gommage_tool(name="db.query", record=record)(db.query)
    email_send_tool = gommage_tool(name="email.send", record=record)(email.send_email)

    ticket = get_ticket_tool(ticket_id=ticket_id)
    if self_steps := record.steps:
        self_steps[-1].intent = "Load ticket context"

    llm.complete(
        f"Triage this Jira ticket: {ticket['summary']}",
        system_message="You are a careful support triage agent.",
        intent="Classify ticket",
        context={"ticket_id": ticket_id, "priority": ticket.get("priority")},
    )
    
    rows = db_query_tool(sql=f"SELECT * FROM exports WHERE ticket_id = '{ticket_id}'")
    if self_steps := record.steps:
        self_steps[-1].intent = "Gather database evidence"

    llm.complete(
        "Decide whether the ticket owner needs an email.",
        system_message="Use evidence before taking action.",
        intent="Plan owner notification",
        context={"owner": ticket.get("owner"), "db_rows": rows},
    )
    
    email_send_tool(
        to=ticket.get("owner", ""),
        subject=f"Follow-up needed for {ticket_id}",
        body="The export failure appears related to migration state. Please review.",
    )
    if self_steps := record.steps:
        self_steps[-1].intent = "Notify owner"

    record.complete()
    return record


if __name__ == "__main__":
    print(run_jira_triage().to_json())
