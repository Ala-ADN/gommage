# Gommage Adapters Guide

This guide details how to wrap real agents using **LangChain** and **LangGraph** to record traces to the Agent Execution Record (AER) model, and replay them safely using Gommage replay adapters.

## 1. Core Adapter Interfaces

All adapters interface with an [AgentExecutionRecord](file:///home/ala-adn/code/gommage/recorder/serializer/aer_schema.py) instance representing a single execution trajectory.

## 2. LangChain Integration (Phase 5a)

To record a standard LangChain agent execution, wrap its tools with `GommageLangChainToolWrapper` and register `GommageLangChainCallbackHandler` as an LLM callback.

### Recording Mode

```python
from langchain_openai import ChatOpenAI
from langchain.agents import initialize_agent, AgentType
from recorder.serializer.aer_schema import AgentExecutionRecord
from recorder.adapters.langchain_adapter import (
    GommageLangChainCallbackHandler,
    GommageLangChainToolWrapper,
)

# 1. Initialize the AER run trace
record = AgentExecutionRecord(run_id="run_123", jira_ticket_id="GOM-101")

# 2. Setup your live tools and wrap them
live_tools = [MySlackTool(), MyDatabaseTool()]
wrapped_tools = [
    GommageLangChainToolWrapper(tool, record, safe_mode=False)
    for tool in live_tools
]

# 3. Setup callback handler for LLM telemetry
llm_callback = GommageLangChainCallbackHandler(record, model_name="gpt-4")
llm = ChatOpenAI(temperature=0, callbacks=[llm_callback])

# 4. Initialize agent and execute
agent = initialize_agent(
    wrapped_tools,
    llm,
    agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
)
agent.run("Post summary to Slack.")

# 5. Export trace
record.complete()
print(record.to_json())
```

### Replay Mode

During replay, substitute the live chat model and tools with Gommage replay adapters:

```python
from recorder.adapters.replay_adapter import ReplayChatModel, ReplayTool

# 1. Load the recorded trace
record = AgentExecutionRecord.from_json(saved_trace_json)

# 2. Swap live components with replay mock adapters
replay_llm = ReplayChatModel(record)
replay_tools = [
    ReplayTool(name=t.name, description=t.description, record=record)
    for t in live_tools
]

# 3. Re-run identical agent code using replay adapters
agent = initialize_agent(
    replay_tools,
    replay_llm,
    agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
)
agent.run("Post summary to Slack.")  # Returns identical output without sending slack messages
```

---

## 3. LangGraph Integration (Phase 5b)

To instrument a state-machine agent using **LangGraph**, wrap individual graph nodes with `LangGraphNodeInterceptor` to log state transitions.

```python
from langgraph.graph import StateGraph, START, END
from recorder.adapters.langgraph_adapter import LangGraphNodeInterceptor

# 1. Initialize record and interceptor
record = AgentExecutionRecord(run_id="graph_run_1", jira_ticket_id="GOM-102")
interceptor = LangGraphNodeInterceptor(record)

# 2. Standard nodes
def fetch_info(state):
    return {"data": "from_source"}

def send_alert(state):
    # Side-effecting code
    return {"alert_sent": True}

# 3. Wrap nodes to capture input/output state transitions
wrapped_fetch = interceptor.wrap_node("fetch_info", fetch_info)
wrapped_alert = interceptor.wrap_node("send_alert", send_alert)

# 4. Compile the Graph
builder = StateGraph(dict)
builder.add_node("fetch", wrapped_fetch)
builder.add_node("alert", wrapped_alert)
builder.add_edge(START, "fetch")
builder.add_edge("fetch", "alert")
builder.add_edge("alert", END)

graph = builder.compile()
graph.invoke({"data": "", "alert_sent": False})

# The trace now contains steps of kind 'decision' mapping each node's exact state changes.
```
