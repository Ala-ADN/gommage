"""Runnable Jira triage agents wrapped by Gommage adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Callable
from uuid import uuid4

from agent.tools.db_tool import DatabaseTool
from agent.tools.email_tool import EmailTool
from agent.tools.jira_tools import JiraToolset
from recorder.proxy.llm_proxy import LLMProxy, deterministic_llm
from recorder.proxy.openai_client import OpenAICompletion
from recorder.adapters.function_adapter import gommage_tool
from recorder.proxy.tool_proxy import ToolProxy
from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


DEMO_DISPLAY_MODEL = "gpt-4o-mini"

TRIAGE_SYSTEM_PROMPT = """You are a careful Jira triage agent.
Choose one next action at a time from the allowed tools. Return only JSON with:
- action: "tool" or "final"
- tool_name: one allowed tool name when action is "tool"
- parameters: object of tool arguments
- rationale: short reason for the action
- done: boolean
Do not invent Jira facts. Prefer reading/searching before writing. External messages are proposed only unless policy says they are live.
""".strip()


@dataclass(slots=True)
class AgentRuntimeConfig:
    agent_mode: str = "auto"
    tool_mode: str = "auto"
    write_policy: str = "jira_only"
    external_messages: str = "dry_run"
    max_steps: int = 8
    system_prompt: str = TRIAGE_SYSTEM_PROMPT

    @classmethod
    def from_env(
        cls,
        *,
        agent_mode: str | None = None,
        tool_mode: str | None = None,
        write_policy: str | None = None,
        external_messages: str | None = None,
        max_steps: int | str | None = None,
        system_prompt: str | None = None,
    ) -> "AgentRuntimeConfig":
        return cls(
            agent_mode=_clean_choice(agent_mode, "GOMMAGE_AGENT_MODE", "auto"),
            tool_mode=_clean_choice(tool_mode, "GOMMAGE_TOOL_MODE", "auto"),
            write_policy=_clean_choice(write_policy, "GOMMAGE_WRITE_POLICY", "jira_only"),
            external_messages=_clean_choice(
                external_messages,
                "GOMMAGE_EXTERNAL_MESSAGES",
                "dry_run",
            ),
            max_steps=_positive_int(max_steps or os.getenv("GOMMAGE_MAX_AGENT_STEPS"), 8),
            system_prompt=(
                system_prompt
                or os.getenv("GOMMAGE_TRIAGE_SYSTEM_PROMPT")
                or TRIAGE_SYSTEM_PROMPT
            ),
        )


def _clean_choice(value: str | None, env_name: str, default: str) -> str:
    return (value or os.getenv(env_name) or default).strip().lower()


def _positive_int(value: int | str | None, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


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
    agent_mode: str | None = None,
    tool_mode: str | None = None,
    write_policy: str | None = None,
    external_messages: str | None = None,
    max_steps: int | str | None = None,
    system_prompt: str | None = None,
) -> AgentExecutionRecord:
    config = AgentRuntimeConfig.from_env(
        agent_mode=agent_mode,
        tool_mode=tool_mode,
        write_policy=write_policy,
        external_messages=external_messages,
        max_steps=max_steps,
        system_prompt=system_prompt,
    )
    mode = config.agent_mode
    if mode == "auto":
        mode = "live" if os.getenv("OPENAI_API_KEY") else "demo"
    if mode in {"demo", "deterministic", "local", "mock"}:
        if _is_proj_4421_story(ticket_id, issue):
            return run_proj_4421_silent_closure_demo(ticket_id, issue=issue, config=config)
        return run_jira_triage_demo(ticket_id, issue=issue, llm_backend=llm_backend, config=config)
    if mode in {"live", "planner", "full"}:
        return run_jira_triage_live(ticket_id, issue=issue, llm_backend=llm_backend, config=config)
    raise ValueError(f"unsupported agent mode: {config.agent_mode}")


def _is_proj_4421_story(ticket_id: str, issue: dict | None) -> bool:
    text = " ".join(
        str(part or "")
        for part in [
            ticket_id,
            issue.get("ticket_id") if issue else "",
            issue.get("key") if issue else "",
            issue.get("summary") if issue else "",
            issue.get("description") if issue else "",
        ]
    ).lower()
    return "proj-4421" in text or "silent" in text and "closed" in text


def _new_record(
    ticket_id: str,
    normalized_issue: dict | None,
    *,
    llm_model: str,
    llm_backend: str,
    agent_mode: str,
    config: AgentRuntimeConfig,
) -> AgentExecutionRecord:
    return AgentExecutionRecord(
        run_id=f"run-{uuid4().hex[:8]}",
        jira_ticket_id=ticket_id,
        agent_name="jira-triage-demo" if agent_mode == "demo" else "jira-triage-live",
        metadata={
            **({"issue": normalized_issue} if normalized_issue else {}),
            "agent_mode": agent_mode,
            "llm_backend": llm_backend,
            "llm_model": llm_model,
            "tool_mode": config.tool_mode,
            "write_policy": config.write_policy,
            "external_messages": config.external_messages,
            "max_steps": config.max_steps,
        },
    )


def run_jira_triage_demo(
    ticket_id: str = "DEMO-101",
    *,
    issue: dict | None = None,
    llm_backend: str | None = None,
    config: AgentRuntimeConfig | None = None,
) -> AgentExecutionRecord:
    config = config or AgentRuntimeConfig.from_env(agent_mode="demo")
    normalized_issue = _normalize_issue(ticket_id, issue)
    llm_callable, llm_model, resolved_backend = _resolve_llm_backend(llm_backend)
    record = _new_record(
        ticket_id,
        normalized_issue,
        llm_model=llm_model,
        llm_backend=resolved_backend,
        agent_mode="demo",
        config=config,
    )
    llm = LLMProxy(record, llm_callable, model=llm_model)
    jira = JiraToolset(tickets={ticket_id: normalized_issue} if normalized_issue else {}, enable_live=False)
    db = DatabaseTool()
    email = EmailTool()

    get_ticket_tool = gommage_tool(name="jira.get_ticket", record=record)(jira.get_ticket)
    db_query_tool = gommage_tool(name="db.query", record=record)(db.query)
    email_send_tool = gommage_tool(name="email.send", record=record)(email.send_email)

    ticket = get_ticket_tool(ticket_id=ticket_id)
    if self_steps := record.steps:
        self_steps[-1].intent = "Load ticket context"

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


def run_proj_4421_silent_closure_demo(
    ticket_id: str,
    *,
    issue: dict | None = None,
    config: AgentRuntimeConfig | None = None,
) -> AgentExecutionRecord:
    config = config or AgentRuntimeConfig.from_env(agent_mode="demo")
    normalized_issue = _normalize_issue(ticket_id, issue) or {
        "ticket_id": ticket_id,
        "summary": "PROJ-4421: Billing export request closed without customer response",
        "description": (
            "Customer reported that a billing export support request was marked Done, "
            "but they never received a response or confirmation."
        ),
        "priority": "High",
        "reporter": "customer-success@example.com",
        "owner": "backend-team@example.com",
        "assignee": "Backend",
        "labels": ["billing-export", "customer-impact", "silent-closure"],
        "status": "Done",
        "issue_type": "Task",
        "source": "jira",
    }
    record = _new_record(
        ticket_id,
        normalized_issue,
        llm_model=DEMO_DISPLAY_MODEL,
        llm_backend="deterministic",
        agent_mode="demo",
        config=config,
    )
    record.agent_name = "jira-triage-proj-4421"
    record.metadata.update(
        {
            "demo_story": "proj-4421-silent-closure",
            "bad_path": {
                "route_team": "Backend",
                "email_to": "",
                "customer_comment": None,
                "closed_by": "agent",
            },
            "corrected_path_hint": {
                "route_team": "Customer Success",
                "email_to": normalized_issue.get("reporter") or "customer@example.com",
                "required_before_done": "customer-facing response or comment",
            },
        }
    )
    snapshots = StepSnapshotBuilder(record)

    ticket_payload = {
        **normalized_issue,
        "customer_response_present": False,
        "comments": [],
        "closed_by": "agent",
    }
    snapshots.add_tool_step(
        tool_name="jira.get_ticket",
        parameters={"ticket_id": ticket_id},
        result=ticket_payload,
        side_effecting=False,
        intent="Load Done ticket state",
        observation="Ticket is already Done with no customer-facing comments.",
        inference="The apparent success state conflicts with missing customer response evidence.",
        context={"ticket_id": ticket_id, "status": "Done", "priority": "High"},
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
        model=DEMO_DISPLAY_MODEL,
        intent="Classify silent closure",
        observation="The agent sees Done and Backend assignment but does not verify customer response.",
        inference="Bug: Done status was trusted more than customer-facing evidence.",
        context={
            "ticket_id": ticket_id,
            "assignee": "Backend",
            "comments": 0,
            "customer_response_present": False,
        },
        latency_ms=118,
        token_count=78,
    )
    snapshots.add_tool_step(
        tool_name="jira.search",
        parameters={"jql": f'issue = "{ticket_id}" AND comments is not EMPTY', "max_results": 5},
        result=[],
        side_effecting=False,
        intent="Check response evidence",
        observation="No comment evidence found.",
        inference="The agent should block closure here, but the next step ignores this evidence.",
        context={"ticket_id": ticket_id, "expected": "customer comment before Done"},
        latency_ms=67,
    )
    snapshots.add_llm_step(
        prompt=(
            "Route the Done ticket. Evidence search returned no comments. "
            "Choose notification target and closure action."
        ),
        response='{"team":"Backend","notify_to":"","close":true,"rationale":"Backend owns export systems."}',
        system_message="Return compact routing JSON.",
        model=DEMO_DISPLAY_MODEL,
        intent="Plan route and notification",
        observation="The plan routes to Backend and leaves notify_to empty.",
        inference="This is the silent-closure decision: Backend route, empty recipient, close=true.",
        context={
            "ticket_id": ticket_id,
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
            "subject": f"Confirmation for {ticket_id}",
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
        context={"ticket_id": ticket_id, "planned_team": "Backend", "notify_to": ""},
        latency_ms=211,
        metadata={"mock_recommended": True},
    )
    snapshots.add_tool_step(
        tool_name="jira.transition",
        parameters={
            "ticket_id": ticket_id,
            "status": "Done",
            "comment": "",
        },
        result={
            "ok": True,
            "ticket_id": ticket_id,
            "status": "Done",
            "comment_created": False,
            "closed_by": "agent",
        },
        side_effecting=True,
        intent="Close ticket",
        observation="Ticket remains Done without a customer comment.",
        inference="The closure hides the missing response unless the trace is replayed.",
        context={"ticket_id": ticket_id, "customer_response_present": False, "status": "Done"},
        latency_ms=133,
        metadata={"mock_recommended": True},
    )
    record.complete()
    return record


def run_jira_triage_live(
    ticket_id: str,
    *,
    issue: dict | None = None,
    llm_backend: str | None = None,
    config: AgentRuntimeConfig | None = None,
) -> AgentExecutionRecord:
    config = config or AgentRuntimeConfig.from_env(agent_mode="live")
    normalized_issue = _normalize_issue(ticket_id, issue)
    llm_callable, llm_model, resolved_backend = _resolve_llm_backend(llm_backend or "auto")
    record = _new_record(
        ticket_id,
        normalized_issue,
        llm_model=llm_model,
        llm_backend=resolved_backend,
        agent_mode="live",
        config=config,
    )
    llm = LLMProxy(record, llm_callable, model=llm_model)
    tools = ToolProxy(record)
    jira = JiraToolset(
        tickets={ticket_id: normalized_issue} if normalized_issue else {},
        enable_live=_tool_mode_is_live(config.tool_mode),
    )

    tool_specs = _tool_specs(config)
    tool_registry = _tool_registry(jira, config)
    history: list[dict[str, Any]] = []

    try:
        ticket = tools.call(
            "jira.get_ticket",
            jira.get_ticket,
            {"ticket_id": ticket_id},
            intent="Load ticket context",
        )
    except RuntimeError as exc:
        record.complete(status="failed")
        record.metadata["failure"] = str(exc)
        return record

    for turn in range(1, config.max_steps + 1):
        prompt = _planner_prompt(ticket_id, ticket, history, tool_specs, config)
        raw_decision = llm.complete(
            prompt,
            system_message=config.system_prompt,
            intent="Plan next triage action",
            context={
                "ticket_id": ticket_id,
                "turn": turn,
                "tool_specs": tool_specs,
                "history": history[-6:],
                "write_policy": config.write_policy,
                "external_messages": config.external_messages,
            },
            temperature=0.2,
            max_output_tokens=700,
        )
        decision = _parse_decision(raw_decision)
        history.append({"turn": turn, "decision": decision})

        if decision.get("done") or decision.get("action") == "final":
            record.metadata["final_rationale"] = decision.get("rationale") or raw_decision
            record.complete()
            return record

        tool_name = str(decision.get("tool_name") or "")
        parameters = decision.get("parameters") if isinstance(decision.get("parameters"), dict) else {}
        allowed, reason = _tool_allowed(tool_name, parameters, ticket_id, config)
        if not allowed:
            history[-1]["blocked"] = reason
            record.metadata["blocked_action"] = {"tool_name": tool_name, "reason": reason}
            record.complete(status="blocked")
            return record

        tool = tool_registry.get(tool_name)
        if tool is None:
            record.metadata["blocked_action"] = {"tool_name": tool_name, "reason": "unknown tool"}
            record.complete(status="blocked")
            return record

        try:
            output = tools.call(
                tool_name,
                tool,
                parameters,
                intent=str(decision.get("rationale") or f"Execute {tool_name}"),
                context={"turn": turn, "ticket_id": ticket_id},
            )
        except RuntimeError as exc:
            history[-1]["error"] = str(exc)
            record.metadata["failure"] = str(exc)
            record.complete(status="failed")
            return record

        history[-1]["result"] = _compact(output)
        if tool_name == "jira.get_ticket" and isinstance(output, dict):
            ticket = output

    record.metadata["stop_reason"] = "max_steps"
    record.complete(status="partial")
    return record


def _tool_mode_is_live(tool_mode: str) -> bool | None:
    if tool_mode == "live":
        return True
    if tool_mode in {"mock", "demo", "local"}:
        return False
    return None


def _tool_specs(config: AgentRuntimeConfig) -> list[dict[str, Any]]:
    specs = [
        {
            "name": "jira.get_ticket",
            "side_effecting": False,
            "parameters": {"ticket_id": "issue key"},
        },
        {
            "name": "jira.search",
            "side_effecting": False,
            "parameters": {"jql": "Jira JQL", "max_results": "integer <= 10"},
        },
        {
            "name": "jira.add_comment",
            "side_effecting": True,
            "parameters": {"ticket_id": "active issue key", "body": "comment text"},
        },
        {
            "name": "jira.update_ticket",
            "side_effecting": True,
            "parameters": {
                "ticket_id": "active issue key",
                "priority": "optional priority name",
                "labels": "optional labels array",
                "comment": "optional audit comment",
            },
        },
        {
            "name": "jira.transition",
            "side_effecting": True,
            "parameters": {"ticket_id": "active issue key", "status": "target status", "comment": "optional comment"},
        },
        {
            "name": "email.send",
            "side_effecting": True,
            "parameters": {"to": "recipient", "subject": "subject", "body": "body"},
        },
    ]
    if config.external_messages != "live":
        specs[-1]["policy"] = "dry_run"
    return specs


def _tool_registry(jira: JiraToolset, config: AgentRuntimeConfig) -> dict[str, Callable[..., Any]]:
    return {
        "jira.get_ticket": jira.get_ticket,
        "jira.search": jira.search,
        "jira.add_comment": jira.add_comment,
        "jira.update_ticket": jira.update_ticket,
        "jira.transition": jira.transition,
        "email.send": _email_sender(config),
    }


def _email_sender(config: AgentRuntimeConfig) -> Callable[..., dict[str, Any]]:
    if config.external_messages == "live":
        return EmailTool().send_email

    def dry_run_email(to: str, subject: str, body: str) -> dict[str, Any]:
        return {
            "ok": True,
            "dry_run": True,
            "tool_name": "email.send",
            "to": to,
            "subject": subject,
            "body": body,
            "reason": "external messages are dry-run by policy",
        }

    return dry_run_email


def _planner_prompt(
    ticket_id: str,
    ticket: dict[str, Any],
    history: list[dict[str, Any]],
    tool_specs: list[dict[str, Any]],
    config: AgentRuntimeConfig,
) -> str:
    return (
        "Plan the next Jira triage action. Return JSON only.\n\n"
        f"Active issue: {ticket_id}\n"
        f"Ticket JSON:\n{json.dumps(_compact(ticket), indent=2, sort_keys=True)}\n\n"
        f"Recent action history:\n{json.dumps(history[-6:], indent=2, sort_keys=True)}\n\n"
        f"Allowed tools:\n{json.dumps(tool_specs, indent=2, sort_keys=True)}\n\n"
        f"Policy: write_policy={config.write_policy}, external_messages={config.external_messages}."
    )


def _parse_decision(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {"action": "final", "done": True, "rationale": raw_text}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {"action": "final", "done": True, "rationale": raw_text}
    if not isinstance(parsed, dict):
        return {"action": "final", "done": True, "rationale": raw_text}
    parsed.setdefault("parameters", {})
    parsed.setdefault("done", False)
    return parsed


def _tool_allowed(
    tool_name: str,
    parameters: dict[str, Any],
    active_ticket_id: str,
    config: AgentRuntimeConfig,
) -> tuple[bool, str]:
    if tool_name not in {spec["name"] for spec in _tool_specs(config)}:
        return False, "tool is not in the allowlist"

    if tool_name.startswith("jira.") and tool_name not in {"jira.get_ticket", "jira.search"}:
        if config.write_policy not in {"jira_only", "all", "live"}:
            return False, "Jira writes are disabled by policy"
        target_ticket = str(parameters.get("ticket_id") or active_ticket_id)
        if target_ticket != active_ticket_id:
            return False, "Jira writes are restricted to the active issue"
        parameters["ticket_id"] = target_ticket
        return True, "allowed Jira write"

    if tool_name == "email.send" and config.external_messages != "live":
        return True, "allowed as dry-run external message"

    if tool_name == "email.send" and config.write_policy not in {"all", "live"}:
        return False, "external messages are disabled by write policy"

    return True, "allowed"


def _compact(value: Any, *, limit: int = 1200) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact(item, limit=limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact(item, limit=limit) for item in value[:20]]
    if isinstance(value, str) and len(value) > limit:
        return f"{value[: limit - 3].rstrip()}..."
    return value


if __name__ == "__main__":
    print(run_jira_triage().to_json())
