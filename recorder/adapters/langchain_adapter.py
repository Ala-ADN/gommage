"""LangChain adapter for recording and replaying LLM and Tool steps."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.tools import BaseTool

from recorder.proxy.mock_registry import MockRegistry
from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


class GommageLangChainCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler to capture LLM inputs and telemetry into AER."""

    def __init__(
        self,
        record: AgentExecutionRecord,
        model_name: str = "langchain-llm",
    ) -> None:
        self.record = record
        self.snapshots = StepSnapshotBuilder(record)
        self.model_name = model_name
        self._llm_starts: Dict[UUID, float] = {}
        self._llm_prompts: Dict[UUID, str] = {}

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        self._llm_starts[run_id] = time.perf_counter()
        # Combine multiple prompts if present, usually it is a single prompt
        self._llm_prompts[run_id] = "\n".join(prompts)

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        start_time = self._llm_starts.pop(run_id, None)
        prompt = self._llm_prompts.pop(run_id, "")
        latency_ms = 0
        if start_time is not None:
            latency_ms = int((time.perf_counter() - start_time) * 1000)

        # Retrieve token usage from response details
        token_count = None
        llm_output = response.llm_output or {}
        token_usage = llm_output.get("token_usage") or {}
        if isinstance(token_usage, dict):
            token_count = token_usage.get("total_tokens") or token_usage.get("total")

        if not token_count:
            token_count = len(prompt.split())

        for generations in response.generations:
            for generation in generations:
                # Add step to AER
                self.snapshots.add_llm_step(
                    prompt=prompt,
                    response=generation.text,
                    model=self.model_name,
                    latency_ms=latency_ms,
                    token_count=token_count,
                    intent="langchain llm completion",
                )


class GommageLangChainToolWrapper(BaseTool):
    """Wraps a LangChain BaseTool to record or mock its execution based on AER and MockRegistry."""

    _wrapped_tool: BaseTool
    _record: AgentExecutionRecord
    _registry: MockRegistry
    _safe_mode: bool
    _snapshots: StepSnapshotBuilder

    def __init__(
        self,
        tool: BaseTool,
        record: AgentExecutionRecord,
        *,
        registry: Optional[MockRegistry] = None,
        safe_mode: bool = False,
    ) -> None:
        registry = registry or MockRegistry()
        super().__init__(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
        )
        self._wrapped_tool = tool
        self._record = record
        self._registry = registry
        self._safe_mode = safe_mode
        self._snapshots = StepSnapshotBuilder(record)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        # Merge positional args and kwargs into standard parameter dict
        parameters = kwargs.copy()
        if args:
            # Simple fallback key mapping
            for idx, arg in enumerate(args):
                parameters[f"arg_{idx}"] = arg

        decision = self._registry.classify(self.name, parameters)
        started = time.perf_counter()
        error: Optional[str] = None
        mocked = bool(self._safe_mode and decision.side_effecting)

        try:
            if mocked:
                result: Any = {
                    "mocked": True,
                    "tool_name": self.name,
                    "reason": decision.reason,
                    "parameters": parameters,
                }
            else:
                result = self._wrapped_tool._run(*args, **kwargs)
        except Exception as exc:
            result = None
            error = f"{type(exc).__name__}: {exc}"
        
        latency_ms = int((time.perf_counter() - started) * 1000)

        self._snapshots.add_tool_step(
            tool_name=self.name,
            parameters=parameters,
            result=result,
            side_effecting=decision.side_effecting,
            mocked=mocked,
            latency_ms=latency_ms,
            error=error,
            intent="langchain tool call",
        )

        if error is not None:
            raise RuntimeError(error)
        return result
