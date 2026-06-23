"""Tests for LangGraph integration adapters."""

from __future__ import annotations

from typing import Dict, Any, TypedDict
import pytest

pytest.importorskip("langgraph")

from langgraph.graph import StateGraph, START, END

from recorder.adapters.langgraph_adapter import LangGraphNodeInterceptor
from recorder.serializer.aer_schema import AgentExecutionRecord


class AgentState(TypedDict):
    input_text: str
    count: int
    output_text: str


def node_one(state: AgentState) -> Dict[str, Any]:
    return {"count": state["count"] + 1, "output_text": f"Processed: {state['input_text']}"}


def node_two(state: AgentState) -> Dict[str, Any]:
    return {"output_text": f"{state['output_text']} (step 2)"}


def test_langgraph_node_interceptor_recording() -> None:
    # 1. Initialize recording trace
    record = AgentExecutionRecord(run_id="lg-run-123", jira_ticket_id="GOM-3")
    interceptor = LangGraphNodeInterceptor(record)

    # 2. Wrap nodes using interceptor
    wrapped_one = interceptor.wrap_node("node_one", node_one)
    wrapped_two = interceptor.wrap_node("node_two", node_two)

    # 3. Construct state graph
    builder = StateGraph(AgentState)
    builder.add_node("node_one", wrapped_one)
    builder.add_node("node_two", wrapped_two)
    builder.add_edge(START, "node_one")
    builder.add_edge("node_one", "node_two")
    builder.add_edge("node_two", END)

    # Compile and execute the graph
    graph = builder.compile()
    initial_state: AgentState = {"input_text": "hello", "count": 0, "output_text": ""}
    final_state = graph.invoke(initial_state)

    # 4. Verify AER trace content
    record.validate()
    # Expect 2 decision steps from node execution
    assert len(record.steps) == 2
    
    step_1 = record.steps[0]
    assert step_1.kind == "decision"
    assert step_1.intent == "node:node_one"
    assert step_1.context["input_state"] == {"input_text": "hello", "count": 0, "output_text": ""}
    assert step_1.context["output_state"] == {"count": 1, "output_text": "Processed: hello"}

    step_2 = record.steps[1]
    assert step_2.kind == "decision"
    assert step_2.intent == "node:node_two"
    assert step_2.context["input_state"] == {"input_text": "hello", "count": 1, "output_text": "Processed: hello"}
    assert step_2.context["output_state"] == {"output_text": "Processed: hello (step 2)"}
