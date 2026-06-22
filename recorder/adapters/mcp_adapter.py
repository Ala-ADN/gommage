"""MCP adapter for recording tool calls."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from recorder.adapters.base import BaseToolAdapter
from recorder.proxy.mock_registry import MockRegistry
from recorder.serializer.aer_schema import AgentExecutionRecord


class MCPAdapter(BaseToolAdapter):
    """Adapter for Model Context Protocol (MCP) tool calls."""

    def __init__(self, record: AgentExecutionRecord, registry: Optional[MockRegistry] = None, safe_mode: bool = False) -> None:
        super().__init__(record, registry, safe_mode)

    def execute(self, tool_name: str, parameters: dict, context: Optional[Dict[str, Any]] = None) -> Any:
        decision = self.registry.classify(tool_name, parameters)
        mocked = bool(self.safe_mode and decision.side_effecting)

        started = time.perf_counter()
        error: Optional[str] = None
        result: Any = None

        try:
            if mocked:
                result = {
                    "mocked": True,
                    "tool_name": tool_name,
                    "reason": decision.reason,
                    "parameters": parameters,
                }
            else:
                # In MCP, actual execution would be done by the client calling the server.
                # Since we don't have the server here, this adapter just records the request/response cycle.
                # The user of this adapter should pass the actual result if it was already executed,
                # or this adapter could be designed to wrap the client.
                # For this implementation, we assume the context contains the result.
                if context and "result" in context:
                    result = context["result"]
                else:
                    raise ValueError("MCP actual execution result missing in context.")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            
        latency_ms = int((time.perf_counter() - started) * 1000)

        self.snapshots.add_tool_step(
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            side_effecting=decision.side_effecting,
            mocked=mocked,
            latency_ms=latency_ms,
            error=error,
            intent="mcp tool call",
        )

        if error is not None:
            raise RuntimeError(error)
        return result
