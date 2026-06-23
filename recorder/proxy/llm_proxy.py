"""Recording proxy for LLM completions."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


LLMCallable = Callable[..., str]


class LLMProxy:
    def __init__(
        self,
        record: AgentExecutionRecord,
        llm: LLMCallable,
        *,
        model: str = "deterministic-demo",
    ) -> None:
        self.record = record
        self.llm = llm
        self.model = getattr(llm, "model", model)
        self.snapshots = StepSnapshotBuilder(record)

    def complete(
        self,
        prompt: str,
        *,
        system_message: str = "",
        intent: str = "llm completion",
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        started = perf_counter()
        response = self.llm(
            prompt,
            system_message=system_message,
            context=context or {},
            **kwargs,
        )
        latency_ms = int((perf_counter() - started) * 1000)
        token_count = len(prompt.split()) + len(str(response).split())
        combined_metadata = dict(metadata or {})
        runtime_metadata = getattr(self.llm, "last_metadata", None)
        if isinstance(runtime_metadata, dict):
            combined_metadata.update(runtime_metadata)
        self.snapshots.add_llm_step(
            prompt=prompt,
            response=str(response),
            system_message=system_message,
            model=getattr(self.llm, "model", self.model),
            intent=intent,
            context=context or {},
            latency_ms=latency_ms,
            token_count=token_count,
            metadata=combined_metadata,
        )
        return str(response)


def deterministic_llm(
    prompt: str,
    *,
    system_message: str = "",
    context: dict[str, Any] | None = None,
    **_: Any,
) -> str:
    """A local LLM substitute used by demos and tests."""

    context = context or {}
    lowered = f"{system_message}\n{prompt}".lower()
    if "polite" in lowered or "safe" in lowered:
        return "Recommend a safe response: summarize findings and avoid side effects."
    if "sql" in lowered or "database" in lowered:
        return "Inspect the ticket history, then query the database before deciding."
    if "email" in lowered or "owner" in lowered:
        owner = context.get("owner", "the owner")
        return f"Draft an email to {owner} explaining the issue."
    if "triage" in lowered:
        return "Classify as needs-investigation and gather supporting evidence."
    return "Continue with the recorded plan."
