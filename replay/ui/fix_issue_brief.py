"""Generate Jira-ready fix issue content from a replay trace."""

from __future__ import annotations

import json
import os
from typing import Any

from recorder.proxy.openai_client import (
    OpenAIConfigurationError,
    OpenAIResponseError,
    OpenAIResponsesClient,
)
from recorder.serializer.aer_schema import AgentExecutionRecord


FIX_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "description": "A concise Jira issue summary under 90 characters.",
        },
        "description_paragraphs": {
            "type": "array",
            "minItems": 4,
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "comment_paragraphs": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {"type": "string"},
        },
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "recommended_prompt_change": {"type": "string"},
        "debug_evidence": {
            "type": "array",
            "minItems": 3,
            "maxItems": 8,
            "items": {"type": "string"},
        },
    },
    "required": [
        "summary",
        "description_paragraphs",
        "comment_paragraphs",
        "risk_level",
        "recommended_prompt_change",
        "debug_evidence",
    ],
}


def generate_fix_issue_brief(
    record: AgentExecutionRecord,
    *,
    replay_metrics: dict[str, Any] | None = None,
    edits: list[dict[str, Any]] | None = None,
    summary_hint: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Return Jira issue fields for a linked fix task."""

    selected_backend = (backend or os.getenv("GOMMAGE_FIX_BRIEF_BACKEND", "auto")).strip().lower()
    if selected_backend == "auto":
        selected_backend = "openai" if os.getenv("OPENAI_API_KEY") else "fallback"

    digest = _trace_digest(record, replay_metrics=replay_metrics, edits=edits or [])
    if selected_backend == "openai":
        try:
            client = OpenAIResponsesClient()
            brief = client.complete_json(
                system_message=(
                    "You write precise Jira fix issues for engineers debugging AI agents. "
                    "Focus on the observed failure, replay evidence, mocked side effects, "
                    "and the smallest prompt/control change needed. Do not invent facts."
                ),
                prompt=(
                    "Create a linked Jira fix issue from this Gommage replay trace. "
                    "The issue should be useful to an engineer who did not watch the demo. "
                    "Mention the trace ID, the risky tool call, the replay result, and the "
                    "specific prompt or control-plane change to make.\n\n"
                    f"Optional summary hint: {summary_hint or ''}\n\n"
                    f"Trace digest JSON:\n{json.dumps(digest, indent=2, sort_keys=True)}"
                ),
                context={"product": "Gommage Replay", "record_id": record.run_id},
                schema=FIX_BRIEF_SCHEMA,
                temperature=float(os.getenv("OPENAI_FIX_TEMPERATURE", "0.7")),
                max_output_tokens=int(os.getenv("OPENAI_FIX_MAX_OUTPUT_TOKENS", "1000")),
            )
            openai_metadata = brief.pop("_openai", {})
            brief["source"] = "openai"
            brief["openai"] = openai_metadata
            return brief
        except (OpenAIConfigurationError, OpenAIResponseError, ValueError) as exc:
            fallback = _fallback_fix_issue_brief(record, digest, summary_hint=summary_hint)
            fallback["source"] = "fallback"
            fallback["warning"] = str(exc)
            return fallback

    if selected_backend in {"fallback", "deterministic", "local", ""}:
        fallback = _fallback_fix_issue_brief(record, digest, summary_hint=summary_hint)
        fallback["source"] = "fallback"
        return fallback
    raise ValueError(f"unsupported fix brief backend: {backend}")


def _trace_digest(
    record: AgentExecutionRecord,
    *,
    replay_metrics: dict[str, Any] | None,
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    issue = dict(record.metadata.get("issue") or {})
    side_effects = [
        {
            "step_id": step.step_id,
            "tool_name": step.tool.tool_name,
            "parameters": step.tool.parameters,
        }
        for step in record.steps
        if step.tool is not None and step.tool.side_effecting
    ]
    llm_steps = [
        {
            "step_id": step.step_id,
            "intent": step.intent,
            "model": step.llm.model,
            "prompt": _trim(step.llm.prompt, 700),
            "response": _trim(step.llm.response, 700),
        }
        for step in record.steps
        if step.llm is not None
    ]
    tool_steps = [
        {
            "step_id": step.step_id,
            "intent": step.intent,
            "tool_name": step.tool.tool_name,
            "side_effecting": step.tool.side_effecting,
            "result": _trim(json.dumps(step.tool.result, sort_keys=True), 700),
        }
        for step in record.steps
        if step.tool is not None
    ]
    return {
        "run_id": record.run_id,
        "trace_hash": record.trace_hash(),
        "ticket_id": record.jira_ticket_id,
        "agent_name": record.agent_name,
        "llm_backend": record.metadata.get("llm_backend"),
        "llm_model": record.metadata.get("llm_model"),
        "issue": {
            "summary": issue.get("summary"),
            "priority": issue.get("priority"),
            "status": issue.get("status"),
            "labels": issue.get("labels"),
            "description": _trim(str(issue.get("description") or ""), 1000),
        },
        "steps": len(record.steps),
        "side_effects": side_effects,
        "llm_steps": llm_steps,
        "tool_steps": tool_steps,
        "replay_metrics": replay_metrics or {},
        "edits": edits,
    }


def _fallback_fix_issue_brief(
    record: AgentExecutionRecord,
    digest: dict[str, Any],
    *,
    summary_hint: str | None,
) -> dict[str, Any]:
    issue = digest.get("issue") or {}
    side_effects = digest.get("side_effects") or []
    metrics = digest.get("replay_metrics") or {}
    risky_tools = ", ".join(item["tool_name"] for item in side_effects) or "none"
    blocked = metrics.get("side_effects_blocked", 0)
    prompt_change = (
        "Require explicit evidence from ticket history and database state before any "
        "side-effecting notification. During replay/debug, preserve recorded tool "
        "responses and mock notification tools."
    )
    summary = summary_hint or f"Fix agent side-effect guard for {record.jira_ticket_id}"
    return {
        "summary": _trim(summary, 90),
        "description_paragraphs": [
            "Created from Gommage Replay after debugging an agent trajectory on the linked Jira issue.",
            (
                f"Observed failure surface: run {record.run_id} contained {len(record.steps)} steps "
                f"and side-effecting tool calls: {risky_tools}."
            ),
            (
                f"Replay evidence: debug replay blocked {blocked} side-effecting call(s) while "
                "preserving the original recorded responses for deterministic inspection."
            ),
            f"Original issue summary: {issue.get('summary') or 'not captured'}.",
            f"Recommended change: {prompt_change}",
            f"Trace hash: {digest['trace_hash']}",
        ],
        "comment_paragraphs": [
            f"Gommage trace: {record.run_id}",
            f"Risky tool calls: {risky_tools}",
            f"Recommended prompt/control change: {prompt_change}",
            "The full AER JSON trace is attached to this issue.",
        ],
        "risk_level": "high" if side_effects else "medium",
        "recommended_prompt_change": prompt_change,
        "debug_evidence": [
            f"Run ID: {record.run_id}",
            f"Trace hash: {digest['trace_hash']}",
            f"Side-effecting tools: {risky_tools}",
            f"Blocked side effects in replay: {blocked}",
        ],
    }


def _trim(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3].rstrip()}..."
