"""Helpers that append normalized step snapshots to an AER."""

from __future__ import annotations

from typing import Any

from recorder.serializer.aer_schema import (
    AERStep,
    AgentExecutionRecord,
    EvidenceLink,
    LLMExchange,
    ToolCall,
)


class StepSnapshotBuilder:
    def __init__(self, record: AgentExecutionRecord) -> None:
        self.record = record

    def add_llm_step(
        self,
        *,
        prompt: str,
        response: str,
        system_message: str = "",
        model: str = "deterministic-demo",
        intent: str = "llm completion",
        observation: str = "",
        inference: str = "",
        context: dict[str, Any] | None = None,
        latency_ms: int = 0,
        token_count: int | None = None,
        metadata: dict[str, Any] | None = None,
        evidence: list[EvidenceLink] | None = None,
    ) -> AERStep:
        step = AERStep(
            step_id=self.record.next_step_id(),
            kind="llm",
            intent=intent,
            observation=observation or response,
            inference=inference,
            context=context or {},
            llm=LLMExchange(
                prompt=prompt,
                response=response,
                system_message=system_message,
                model=model,
                latency_ms=latency_ms,
                token_count=token_count,
                metadata=metadata or {},
            ),
            evidence=evidence or [],
        )
        return self.record.add_step(step)

    def add_tool_step(
        self,
        *,
        tool_name: str,
        parameters: dict[str, Any],
        result: Any,
        side_effecting: bool,
        mocked: bool = False,
        intent: str = "tool call",
        observation: str = "",
        inference: str = "",
        context: dict[str, Any] | None = None,
        latency_ms: int = 0,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        evidence: list[EvidenceLink] | None = None,
    ) -> AERStep:
        step = AERStep(
            step_id=self.record.next_step_id(),
            kind="tool",
            intent=intent,
            observation=observation or (error or "tool call completed"),
            inference=inference,
            context=context or {},
            tool=ToolCall(
                tool_name=tool_name,
                parameters=parameters,
                result=result,
                side_effecting=side_effecting,
                mocked=mocked,
                latency_ms=latency_ms,
                error=error,
                metadata=metadata or {},
            ),
            evidence=evidence or [],
        )
        return self.record.add_step(step)
