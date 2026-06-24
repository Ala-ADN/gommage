"""Agent Execution Record schema.

The project README mentions Pydantic, but the MVP keeps the schema dependency
free so traces can be recorded and replayed in constrained environments.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Literal


StepKind = Literal["llm", "tool", "observation", "decision"]
COMMUNICATION_TOOLS = {"email.send", "slack.post", "sms.send"}


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


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return text or "unknown"


def _duration_ms(started_at: str | None, completed_at: str | None) -> int:
    if not started_at or not completed_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((completed - started).total_seconds() * 1000))


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
    category: str = ""
    depends_on: list[int] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    canonical_id: str = ""
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
            category=payload.get("category", ""),
            depends_on=[int(item) for item in payload.get("depends_on", [])],
            produces=[str(item) for item in payload.get("produces", [])],
            canonical_id=payload.get("canonical_id", ""),
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
        _enrich_step_metadata(step, self.steps)
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

    def enrich_step_metadata(self) -> None:
        previous_steps: list[AERStep] = []
        for step in self.steps:
            _enrich_step_metadata(step, previous_steps)
            previous_steps.append(step)

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
        record.enrich_step_metadata()
        record.validate()
        return record

    @classmethod
    def from_json(cls, payload: str) -> "AgentExecutionRecord":
        return cls.from_dict(json.loads(payload))


@dataclass(slots=True)
class TraceIndex:
    """Lightweight trace metadata for dashboard rendering."""

    run_id: str
    jira_ticket_id: str
    agent_name: str
    status: str
    started_at: str
    completed_at: str | None
    step_count: int
    llm_call_count: int
    tool_call_count: int
    tool_error_count: int
    side_effect_count: int
    total_latency_ms: int
    duration_ms: int
    avg_tool_latency_ms: float
    category_distribution: dict[str, int]
    canonical_path: list[str]

    @classmethod
    def from_record(cls, record: AgentExecutionRecord) -> "TraceIndex":
        record.enrich_step_metadata()
        tool_latencies = [
            step.tool.latency_ms
            for step in record.steps
            if step.tool is not None
        ]
        total_latency = sum(
            (step.llm.latency_ms if step.llm else 0)
            + (step.tool.latency_ms if step.tool else 0)
            for step in record.steps
        )
        category_distribution: dict[str, int] = {}
        for step in record.steps:
            category_distribution[step.category or "other"] = (
                category_distribution.get(step.category or "other", 0) + 1
            )
        return cls(
            run_id=record.run_id,
            jira_ticket_id=record.jira_ticket_id,
            agent_name=record.agent_name,
            status=record.status,
            started_at=record.started_at,
            completed_at=record.completed_at,
            step_count=len(record.steps),
            llm_call_count=sum(1 for step in record.steps if step.llm is not None),
            tool_call_count=sum(1 for step in record.steps if step.tool is not None),
            tool_error_count=sum(1 for step in record.steps if step.tool and step.tool.error),
            side_effect_count=sum(
                1 for step in record.steps if step.tool is not None and step.tool.side_effecting
            ),
            total_latency_ms=total_latency,
            duration_ms=_duration_ms(record.started_at, record.completed_at),
            avg_tool_latency_ms=sum(tool_latencies) / len(tool_latencies)
            if tool_latencies
            else 0.0,
            category_distribution=category_distribution,
            canonical_path=[step.canonical_id for step in record.steps if step.canonical_id],
        )


def classify_step(step: AERStep) -> str:
    if step.kind == "llm":
        intent = (step.intent or "").lower()
        if any(keyword in intent for keyword in ["classify", "decide", "decision", "triage", "plan", "route"]):
            return "decision_making"
        if step.inference:
            return "reasoning"
        return "reasoning"
    if step.kind == "tool" and step.tool is not None:
        if step.tool.error:
            return "error_recovery"
        if step.tool.side_effecting:
            if step.tool.tool_name in COMMUNICATION_TOOLS:
                return "communication"
            return "data_mutation"
        return "information_gathering"
    if step.evidence:
        return "evidence_collection"
    return "other"


def canonicalize_step(step: AERStep) -> str:
    if step.kind == "tool" and step.tool is not None:
        param_keys = ",".join(sorted(step.tool.parameters.keys()))
        return f"tool:{step.tool.tool_name}:{{{param_keys}}}"
    if step.kind == "llm":
        return f"llm:{_slug(step.intent or step.llm.model if step.llm else step.intent)}"
    if step.evidence:
        source = _slug(step.evidence[0].source)
        return f"observe:{source}"
    return f"{step.kind}:{_slug(step.intent or step.kind)}"


def produced_keys_for_step(step: AERStep) -> list[str]:
    if step.kind == "tool" and step.tool is not None:
        keys = [f"tool_result:{step.tool.tool_name}", step.tool.tool_name]
        if isinstance(step.tool.result, dict):
            keys.extend(str(key) for key in step.tool.result.keys())
        return sorted(set(keys))
    if step.kind == "llm" and step.llm is not None:
        return [f"llm_decision:{_slug(step.intent)}"]
    if step.evidence:
        return [f"evidence:{_slug(step.evidence[0].source)}"]
    return []


def _enrich_step_metadata(step: AERStep, previous_steps: list[AERStep]) -> None:
    if not step.category:
        step.category = classify_step(step)
    if not step.canonical_id:
        step.canonical_id = canonicalize_step(step)
    if not step.produces:
        step.produces = produced_keys_for_step(step)
    if step.depends_on:
        return

    produced_by: dict[str, int] = {}
    for previous in previous_steps:
        for key in previous.produces or produced_keys_for_step(previous):
            produced_by[key] = previous.step_id
        for key in previous.context.keys():
            produced_by.setdefault(str(key), previous.step_id)

    dependencies: set[int] = set()
    for key in step.context.keys():
        source_step = produced_by.get(str(key))
        if source_step is not None and source_step != step.step_id:
            dependencies.add(source_step)
    for evidence in step.evidence:
        if evidence.source.startswith("step:"):
            try:
                dependencies.add(int(evidence.source.split(":", 1)[1]))
            except ValueError:
                continue
    step.depends_on = sorted(dependencies)
