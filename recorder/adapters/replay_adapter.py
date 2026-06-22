"""Replay adapters that act as LangChain components but return recorded payloads."""

from __future__ import annotations

from typing import Any, List, Optional

from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, ChatMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool

from recorder.serializer.aer_schema import AgentExecutionRecord


class ReplayChatModel(BaseChatModel):
    """A LangChain ChatModel that returns recorded LLM responses from a trace."""

    _record: AgentExecutionRecord
    _step_pointer: int = 0

    def __init__(self, record: AgentExecutionRecord, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._record = record
        self._step_pointer = 0

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Find the next LLM step in the record
        llm_steps = [step for step in self._record.steps if step.kind == "llm"]
        if self._step_pointer < len(llm_steps):
            step = llm_steps[self._step_pointer]
            self._step_pointer += 1
            response_text = step.llm.response if step.llm else "Replay fallback response"
        else:
            response_text = "Replay end of trace response"

        message = ChatMessage(content=response_text, role="assistant")
        generation = ChatGeneration(message=message)
        return ChatResult(generations=[generation])

    @property
    def _llm_type(self) -> str:
        return "replay-chat-model"


class ReplayTool(BaseTool):
    """A LangChain Tool that returns recorded tool outputs from a trace."""

    _record: AgentExecutionRecord

    def __init__(
        self,
        name: str,
        description: str,
        record: AgentExecutionRecord,
        **kwargs: Any,
    ) -> None:
        super().__init__(name=name, description=description, **kwargs)
        self._record = record

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        # Merge positional and keyword arguments
        parameters = kwargs.copy()
        if args:
            for idx, arg in enumerate(args):
                parameters[f"arg_{idx}"] = arg

        # Try to find a matching tool step in the trace
        tool_steps = [
            step for step in self._record.steps 
            if step.kind == "tool" and step.tool and step.tool.tool_name == self.name
        ]
        
        # Match by parameters or return the first match that hasn't been consumed yet
        for step in tool_steps:
            if step.tool:
                # If side-effecting and mocked in the trace, return the mock payload
                if step.tool.mocked:
                    return {
                        "mocked": True,
                        "tool_name": self.name,
                        "reason": step.tool.metadata.get("mock_reason", "Blocked in replay"),
                        "parameters": parameters,
                    }
                return step.tool.result

        return {"status": "success", "info": "Replay default fallback output"}
