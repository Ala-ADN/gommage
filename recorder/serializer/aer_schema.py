"""Agent Execution Record schema.

The project README mentions Pydantic, but the MVP keeps the schema dependency
free so traces can be recorded and replayed in constrained environments.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Literal


StepKind = Literal["llm", "tool", "observation", "decision"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def stable_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class EvidenceLink:
    source: str
    reference: str
    verdict: str = "observed"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidenceLink":
        return cls(
            source=payload["source"],
            reference=payload["reference"],
            verdict=payload.get("verdict", "observed"),
            confidence=float(payload.get("confidence", 1.0)),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class LLMExchange:
    prompt: str
    response: str
    system_message: str = ""
    model: str = "deterministic-demo"
    latency_ms: int = 0
    token_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LLMExchange":
        return cls(
            prompt=payload["prompt"],
            response=payload["response"],
            system_message=payload.get("system_message", ""),
            model=payload.get("model", "deterministic-demo"),
            latency_ms=int(payload.get("latency_ms", 0)),
            token_count=payload.get("token_count"),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class ToolCall:
    tool_name: str
    parameters: dict[str, Any]
    result: Any
    side_effecting: bool = False
    mocked: bool = False
    latency_ms: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ToolCall":
        return cls(
            tool_name=payload["tool_name"],
            parameters=dict(payload.get("parameters", {})),
            result=payload.get("result"),
            side_effecting=bool(payload.get("side_effecting", False)),
            mocked=bool(payload.get("mocked", False)),
            latency_ms=int(payload.get("latency_ms", 0)),
            error=payload.get("error"),
            metadata=dict(payload.get("metadata", {})),
        )


@dataclass(slots=True)
class AERStep:
    step_id: int
    kind: StepKind
    intent: str
    observation: str = ""
    inference: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    llm: LLMExchange | None = None
    tool: ToolCall | None = None
    evidence: list[EvidenceLink] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now_iso)

    def input_fingerprint(self) -> str:
        if self.llm is not None:
            payload = {
                "kind": self.kind,
                "system_message": self.llm.system_message,
                "prompt": self.llm.prompt,
                "context": self.context,
            }
        elif self.tool is not None:
            payload = {
                "kind": self.kind,
                "tool_name": self.tool.tool_name,
                "parameters": self.tool.parameters,
                "context": self.context,
            }
        else:
            payload = {"kind": self.kind, "context": self.context, "intent": self.intent}
        return sha256(stable_json(payload).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AERStep":
        return cls(
            step_id=int(payload["step_id"]),
            kind=payload["kind"],
            intent=payload.get("intent", ""),
            observation=payload.get("observation", ""),
            inference=payload.get("inference", ""),
            context=dict(payload.get("context", {})),
            llm=LLMExchange.from_dict(payload["llm"]) if payload.get("llm") else None,
            tool=ToolCall.from_dict(payload["tool"]) if payload.get("tool") else None,
            evidence=[
                EvidenceLink.from_dict(item)
                for item in payload.get("evidence", [])
            ],
            timestamp=payload.get("timestamp", utc_now_iso()),
        )


@dataclass(slots=True)
class AgentExecutionRecord:
    run_id: str
    jira_ticket_id: str
    agent_name: str = "unknown"
    status: str = "running"
    schema_version: str = "1.0"
    started_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    steps: list[AERStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def next_step_id(self) -> int:
        if not self.steps:
            return 1
        return max(step.step_id for step in self.steps) + 1

    def add_step(self, step: AERStep) -> AERStep:
        if any(existing.step_id == step.step_id for existing in self.steps):
            raise ValueError(f"duplicate step_id {step.step_id}")
        self.steps.append(step)
        return step

    def complete(self, status: str = "completed") -> None:
        self.status = status
        self.completed_at = utc_now_iso()

    def validate(self) -> None:
        if not self.run_id:
            raise ValueError("run_id is required")
        if not self.jira_ticket_id:
            raise ValueError("jira_ticket_id is required")
        seen: set[int] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError(f"duplicate step_id {step.step_id}")
            seen.add(step.step_id)
            if step.kind == "llm" and step.llm is None:
                raise ValueError(f"step {step.step_id} is kind=llm but has no llm payload")
            if step.kind == "tool" and step.tool is None:
                raise ValueError(f"step {step.step_id} is kind=tool but has no tool payload")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def trace_hash(self) -> str:
        return sha256(stable_json(self.to_dict()).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgentExecutionRecord":
        record = cls(
            run_id=payload["run_id"],
            jira_ticket_id=payload["jira_ticket_id"],
            agent_name=payload.get("agent_name", "unknown"),
            status=payload.get("status", "running"),
            schema_version=payload.get("schema_version", "1.0"),
            started_at=payload.get("started_at", utc_now_iso()),
            completed_at=payload.get("completed_at"),
            steps=[AERStep.from_dict(item) for item in payload.get("steps", [])],
            metadata=dict(payload.get("metadata", {})),
        )
        record.validate()
        return record

    @classmethod
    def from_json(cls, payload: str) -> "AgentExecutionRecord":
        return cls.from_dict(json.loads(payload))
