"""Second demo scenario: audit a Confluence page and avoid replay side effects."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agent.tools.email_tool import EmailTool
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


def run_confluence_audit(page_id: str = "CONF-77") -> AgentExecutionRecord:
    record = AgentExecutionRecord(
        run_id=f"run-{uuid4().hex[:8]}",
        jira_ticket_id="AUDIT-77",
        agent_name="confluence-audit-demo",
        metadata={"page_id": page_id},
    )
    llm = DemoLLMAdapter(record)
    email = EmailTool()
    
    email_send_tool = gommage_tool(name="email.send", record=record)(email.send_email)

    llm.complete(
        "Audit this Confluence page for stale ownership and risky instructions.",
        system_message="Escalate only with evidence.",
        intent="Audit page content",
        context={"page_id": page_id, "owner": "docs-owner@example.com"},
    )
    
    email_send_tool(
        to="docs-owner@example.com",
        subject="Confluence page audit follow-up",
        body="Please review the stale operational instructions.",
    )
    if self_steps := record.steps:
        self_steps[-1].intent = "Send owner follow-up"

    record.complete()
    return record
