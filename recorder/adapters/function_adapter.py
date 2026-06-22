"""Function decorator adapter for arbitrary Python functions."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable, Optional

from recorder.adapters.base import BaseToolAdapter
from recorder.proxy.mock_registry import MockRegistry
from recorder.serializer.aer_schema import AgentExecutionRecord


class FunctionToolAdapter(BaseToolAdapter):
    """Adapter to wrap arbitrary Python functions."""

    def __init__(self, record: AgentExecutionRecord, registry: Optional[MockRegistry] = None, safe_mode: bool = False) -> None:
        super().__init__(record, registry, safe_mode)

    def execute(self, tool_name: str, parameters: dict, context: Optional[dict[str, Any]] = None, **kwargs) -> Any:
        # Not used directly in decorator implementation, but satisfies abstract class
        pass


def gommage_tool(name: Optional[str] = None, record: Optional[AgentExecutionRecord] = None, safe_mode: bool = False, replay_mode: bool = False, registry: Optional[MockRegistry] = None) -> Callable:
    """Decorator to wrap a Python function as a Gommage tool."""
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if record is None:
                return func(*args, **kwargs)
                
            if replay_mode:
                tool_steps = [
                    step for step in record.steps 
                    if step.kind == "tool" and step.tool and step.tool.tool_name == tool_name
                ]
                for step in tool_steps:
                    if step.tool:
                        if step.tool.mocked:
                            return {
                                "mocked": True,
                                "tool_name": tool_name,
                                "reason": step.tool.metadata.get("mock_reason", "Blocked in replay"),
                                "parameters": kwargs,
                            }
                        return step.tool.result
                return {"status": "success", "info": "Replay default fallback output"}

            adapter = FunctionToolAdapter(record, registry, safe_mode)
            decision = adapter.registry.classify(tool_name, kwargs)
            mocked = bool(adapter.safe_mode and decision.side_effecting)
            
            started = time.perf_counter()
            error: Optional[str] = None
            
            try:
                if mocked:
                    result: Any = {
                        "mocked": True,
                        "tool_name": tool_name,
                        "reason": decision.reason,
                        "parameters": kwargs,
                    }
                else:
                    result = func(*args, **kwargs)
            except Exception as exc:
                result = None
                error = f"{type(exc).__name__}: {exc}"
            
            latency_ms = int((time.perf_counter() - started) * 1000)
            
            adapter.snapshots.add_tool_step(
                tool_name=tool_name,
                parameters=kwargs,
                result=result,
                side_effecting=decision.side_effecting,
                mocked=mocked,
                latency_ms=latency_ms,
                error=error,
                intent="function tool call",
            )
            
            if error is not None:
                raise RuntimeError(error)
            return result
        return wrapper
    return decorator
