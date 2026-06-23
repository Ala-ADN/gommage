"""Tests for LangChain integration adapters."""

from __future__ import annotations

from typing import Type
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("langchain_core")

from pydantic import BaseModel, Field

from langchain_core.outputs import LLMResult, RunInfo
from langchain_core.outputs.generation import Generation
from langchain_core.tools import BaseTool

from recorder.adapters.langchain_adapter import (
    GommageLangChainCallbackHandler,
    GommageLangChainToolWrapper,
)
from recorder.adapters.replay_adapter import ReplayChatModel, ReplayTool
from recorder.serializer.aer_schema import AgentExecutionRecord
from replay.engine.replay_runner import ReplayRunner


class DummyToolInput(BaseModel):
    message: str = Field(description="The message to send")


class DummySlackTool(BaseTool):
    name: str = "send_slack_message"
    description: str = "Sends a slack message to a channel"
    args_schema: Type[BaseModel] = DummyToolInput

    def _run(self, message: str) -> str:
        return f"Successfully sent Slack message: {message}"


def test_langchain_callback_handler_recording() -> None:
    from langchain_core.language_models.fake import FakeListLLM
    
    # 1. Initialize record
    record = AgentExecutionRecord(run_id="lc-run-123", jira_ticket_id="GOM-1")
    handler = GommageLangChainCallbackHandler(record, model_name="fake-llm-test")

    # 2. Setup a Fake LangChain LLM and bind the callback
    llm = FakeListLLM(responses=["It is like magic toys."], callbacks=[handler])
    
    # Execute the LLM naturally via LangChain
    llm.invoke("Explain quantum computing to a 5-year-old.")

    # 3. Assert AER record updated correctly from actual callback hooks
    record.validate()
    assert len(record.steps) == 1
    step = record.steps[0]
    assert step.kind == "llm"
    assert step.llm is not None
    assert step.llm.prompt == "Explain quantum computing to a 5-year-old."
    assert step.llm.response == "It is like magic toys."
    assert step.llm.model == "fake-llm-test"
    # Note: FakeListLLM does not emit token usage by default, so our fallback calculates it
    assert step.llm.token_count == 6


def test_langchain_tool_wrapper_recording_and_replay() -> None:
    # 1. Recording phase
    record = AgentExecutionRecord(run_id="lc-tool-run-123", jira_ticket_id="GOM-2")
    live_tool = DummySlackTool()
    
    # Wrap tool with safe_mode=False for recording
    wrapped = GommageLangChainToolWrapper(live_tool, record, safe_mode=False)
    
    # Run wrapped tool
    result = wrapped.run({"message": "Hello Team!"})
    assert result == "Successfully sent Slack message: Hello Team!"

    # Validate AER steps
    record.validate()
    assert len(record.steps) == 1
    step = record.steps[0]
    assert step.kind == "tool"
    assert step.tool is not None
    assert step.tool.tool_name == "send_slack_message"
    assert step.tool.parameters == {"message": "Hello Team!"}
    assert step.tool.side_effecting is True
    assert step.tool.mocked is False

    # 2. Replay phase using ReplayTool
    replay_tool = ReplayTool(
        name="send_slack_message",
        description="Sends a slack message",
        record=record,
    )
    # The replay tool should return the cached value directly
    replay_result = replay_tool.run({"message": "Hello Team!"})
    assert replay_result == "Successfully sent Slack message: Hello Team!"
