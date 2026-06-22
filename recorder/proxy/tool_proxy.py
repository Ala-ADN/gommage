"""Recording proxy for agent tool calls."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from recorder.proxy.mock_registry import MockRegistry
from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


ToolCallable = Callable[..., Any]


class ToolProxy:
    def __init__(
        self,
        record: AgentExecutionRecord,
        *,
        registry: MockRegistry | None = None,
        safe_mode: bool = False,
    ) -> None:
        self.record = record
        self.registry = registry or MockRegistry()
        self.safe_mode = safe_mode
        self.snapshots = StepSnapshotBuilder(record)

    def call(
        self,
        tool_name: str,
        tool: ToolCallable,
        parameters: dict[str, Any] | None = None,
        *,
        intent: str = "tool call",
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        parameters = parameters or {}
        decision = self.registry.classify(tool_name, parameters)
        started = perf_counter()
        error: str | None = None
        mocked = bool(self.safe_mode and decision.side_effecting)

        try:
            if mocked:
                result: Any = {
                    "mocked": True,
                    "tool_name": tool_name,
                    "reason": decision.reason,
                    "parameters": parameters,
                }
            else:
                result = tool(**parameters)
        except Exception as exc:  # noqa: BLE001 - trace capture should preserve failures.
            result = None
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = int((perf_counter() - started) * 1000)

        self.snapshots.add_tool_step(
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            side_effecting=decision.side_effecting,
            mocked=mocked,
            intent=intent,
            context=context or {},
            latency_ms=latency_ms,
            error=error,
            metadata={**(metadata or {}), "mock_reason": decision.reason},
        )

        if error is not None:
            raise RuntimeError(error)
        return result

    def wrap(
        self,
        tool_name: str,
        tool: ToolCallable,
        *,
        intent: str = "tool call",
    ) -> ToolCallable:
        def wrapped(**parameters: Any) -> Any:
            return self.call(tool_name, tool, parameters, intent=intent)

        return wrapped
