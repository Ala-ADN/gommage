"""LangGraph adapter for intercepting and recording state graph node execution."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel
from recorder.serializer.aer_schema import AERStep, AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


def _serialize_state(state: Any) -> Dict[str, Any]:
    """Helper to safely serialize LangGraph state dictionaries or Pydantic models."""
    if state is None:
        return {}
    if isinstance(state, BaseModel):
        return state.model_dump()
    if isinstance(state, dict):
        # return a shallow copy with serialization safety
        return {str(k): (v.model_dump() if isinstance(v, BaseModel) else v) for k, v in state.items()}
    return {"state": str(state)}


class LangGraphNodeInterceptor:
    """Interceptor for LangGraph node execution to capture state inputs/outputs."""

    def __init__(self, record: AgentExecutionRecord) -> None:
        self.record = record
        self.snapshots = StepSnapshotBuilder(record)

    def wrap_node(self, node_name: str, node_func: Callable[..., Any]) -> Callable[..., Any]:
        """Wraps a LangGraph node function to capture execution state and duration."""
        def wrapped_node(state: Any, *args: Any, **kwargs: Any) -> Any:
            input_state_serialized = _serialize_state(state)
            started = time.perf_counter()
            error: Optional[str] = None
            result: Any = None
            
            try:
                result = node_func(state, *args, **kwargs)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                latency_ms = int((time.perf_counter() - started) * 1000)
                output_state_serialized = _serialize_state(result) if error is None else {}
                
                # Record the node execution as a decision step in the AER trace
                step = AERStep(
                    step_id=self.record.next_step_id(),
                    kind="decision",
                    intent=f"node:{node_name}",
                    observation=error or f"Node {node_name} execution completed.",
                    context={
                        "input_state": input_state_serialized,
                        "output_state": output_state_serialized,
                        "latency_ms": latency_ms,
                    },
                )
                self.record.add_step(step)

            return result

        return wrapped_node
