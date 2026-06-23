"""Base classes and interfaces for agent and tool adapters."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from recorder.proxy.mock_registry import MockRegistry
from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.serializer.step_snapshot import StepSnapshotBuilder


class BaseAdapter(ABC):
    """Base class for all Gommage adapters."""

    def __init__(self, record: AgentExecutionRecord) -> None:
        self.record = record
        self.snapshots = StepSnapshotBuilder(record)


class BaseLLMAdapter(BaseAdapter):
    """Abstract adapter for LLMs."""

    def __init__(self, record: AgentExecutionRecord) -> None:
        super().__init__(record)
        self._start_time: Optional[float] = None
        self._prompt: str = ""
        self._system_message: str = ""
        self._context: Optional[Dict[str, Any]] = None

    def capture_input(self, prompt: str, system_message: str = "", context: Optional[Dict[str, Any]] = None) -> None:
        self._start_time = time.perf_counter()
        self._prompt = prompt
        self._system_message = system_message
        self._context = context

    def capture_output(self, response: str, token_usage: Optional[Dict[str, int]] = None, model_name: str = "") -> None:
        latency_ms = int((time.perf_counter() - self._start_time) * 1000) if self._start_time else 0
        token_count = 0
        if token_usage:
            token_count = token_usage.get("total_tokens") or token_usage.get("total", 0)
        
        self.snapshots.add_llm_step(
            prompt=self._prompt,
            response=response,
            model=model_name,
            latency_ms=latency_ms,
            token_count=token_count,
            intent="llm completion",
        )
        self._start_time = None


class BaseToolAdapter(BaseAdapter):
    """Abstract adapter for Tools."""

    def __init__(self, record: AgentExecutionRecord, registry: Optional[MockRegistry] = None, safe_mode: bool = False) -> None:
        super().__init__(record)
        self.registry = registry or MockRegistry()
        self.safe_mode = safe_mode

    @abstractmethod
    def execute(self, tool_name: str, parameters: dict, context: Optional[Dict[str, Any]] = None) -> Any:
        pass
