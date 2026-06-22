"""Second demo scenario: audit a Confluence page and avoid replay side effects."""

from __future__ import annotations

from uuid import uuid4

from agent.tools.email_tool import EmailTool
from recorder.proxy.llm_proxy import LLMProxy, deterministic_llm
from recorder.proxy.tool_proxy import ToolProxy
from recorder.serializer.aer_schema import AgentExecutionRecord


def run_confluence_audit(page_id: str = "CONF-77") -> AgentExecutionRecord:
    record = AgentExecutionRecord(
        run_id=f"run-{uuid4().hex[:8]}",
        jira_ticket_id="AUDIT-77",
        agent_name="confluence-audit-demo",
        metadata={"page_id": page_id},
    )
    llm = LLMProxy(record, deterministic_llm)
    tools = ToolProxy(record)
    email = EmailTool()

    llm.complete(
        "Audit this Confluence page for stale ownership and risky instructions.",
        system_message="Escalate only with evidence.",
        intent="Audit page content",
        context={"page_id": page_id, "owner": "docs-owner@example.com"},
    )
    tools.call(
        "email.send",
        email.send_email,
        {
            "to": "docs-owner@example.com",
            "subject": "Confluence page audit follow-up",
            "body": "Please review the stale operational instructions.",
        },
        intent="Send owner follow-up",
    )
    record.complete()
    return record
