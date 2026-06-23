# Gommage Implementation Plan

This plan is for collaborators picking up work after the current MVP. It focuses on getting the Jira-native demo reliable first, then hardening the recording/replay system.

## Current Baseline

The repo already has:

- AER schema and JSON trace serialization.
- LLM/tool recording proxies.
- Side-effect detection and replay mocking.
- Replay editing and divergence tracking.
- Local browser UI and backend API.
- Forge Jira issue panel/context scaffold.
- Demo agents and deterministic tool doubles.
- Python tests for core behavior.

Primary entry points:

- Backend/API: `main.py ui`
- Replay engine: `replay/engine/replay_runner.py`
- Schema: `recorder/serializer/aer_schema.py`
- Forge app: `replay/ui/forge`
- Local UI: `replay/ui/web`

## Phase 1: Stabilize Jira-Native Demo

Goal: A judge can open a Jira issue, use `Gommage Replay`, record a trace, replay safely, edit a prompt, and create a linked fix issue.

Tasks:

1. Verify Forge Custom UI loads in Jira after the Vite relative asset-path fix.
2. Confirm the panel receives the issue key from Forge context.
3. Confirm `Record current issue` creates an AER trace using the Jira issue key.
4. Confirm the AER JSON gets attached to the Jira issue.
5. Confirm `Replay in Debug Mode` returns `side_effects_blocked: 1`.
6. Confirm prompt editing marks divergence and lowers replay fidelity for the edited step.
7. Confirm `Create linked fix issue` creates and links a Jira issue.
8. Add clear UI error states for:
   - missing `GOMMAGE_BACKEND_URL`
   - backend offline
   - ngrok URL expired
   - missing issue key
   - Jira attachment/comment permission failure

Verification:

```bash
cd replay/ui/forge
npm --prefix static/gommage-replay run build
npx forge lint
npx forge deploy
npx forge install --upgrade --product jira --site gommage.atlassian.net
```

Manual Jira flow:

1. Open a full issue page such as `/browse/GOM-1`.
2. Open `Gommage Replay`.
3. Click `Record current issue`.
4. Select the `email.send` step.
5. Click `Replay in Debug Mode`.
6. Confirm the email step is mocked/blocked.
7. Edit an LLM prompt and continue.
8. Create a linked fix issue.

## Phase 2: Replace Demo Fixtures With Real Jira Input

Goal: The demo agent should use the actual Jira issue summary, description, priority, reporter, and assignee.

Tasks:

1. Add a backend endpoint for Forge to pass issue details from Jira.
2. In the Forge resolver, fetch issue fields via Jira REST:
   - summary
   - description
   - priority
   - reporter
   - assignee
   - labels
3. Pass those fields into the backend `recordRun` call.
4. Update `run_jira_triage` so it can accept a Jira issue payload instead of only using `JiraToolset` fixtures.
5. Preserve synthetic fallback behavior for local demos.
6. Update tests for both synthetic and real-ish Jira payload paths.

Suggested backend shape:

```json
{
  "ticket_id": "GOM-1",
  "issue": {
    "summary": "...",
    "description": "...",
    "priority": "...",
    "reporter": "...",
    "assignee": "..."
  }
}
```

Verification:

- Record two different Jira issues and confirm the prompts/tool contexts differ.
- Confirm traces are filtered by issue key in the panel.

## Phase 3: Backend Hardening

Goal: Stop using the local UI server as the production-ish API backend.

Tasks:

1. Split API server from static local UI serving.
2. Add a proper backend app structure:
   - `GET /health`
   - `GET /api/runs?ticket_id=...`
   - `GET /api/runs/{run_id}`
   - `POST /api/record`
   - `POST /api/replay`
   - `POST /api/fix-issue`
3. Add request validation.
4. Add structured error responses.
5. Add CORS only if needed for local browser testing.
6. Add backend authentication for Forge calls.
7. Add config through environment variables.

Recommended minimal auth:

- Shared secret header from Forge to backend.
- Backend checks `X-Gommage-Token`.
- Forge stores token using `forge variables set`.

Verification:

- Backend returns `200` on `/health`.
- Forge calls fail cleanly with wrong/missing token.
- Local UI still works in dev mode.

## Phase 4: Persistent Storage

Goal: Make traces queryable and durable beyond local JSON files.

Tasks:

1. Add SQLite storage for demo.
2. Keep `LocalTraceStore` as a file fallback.
3. Define a storage interface:
   - `save(record)`
   - `load(run_id)`
   - `list(ticket_id=None)`
   - `attach_metadata(run_id, metadata)`
4. Add migrations or a simple schema initializer.
5. Add indexed fields:
   - run id
   - Jira ticket id
   - agent name
   - status
   - started/completed time
   - side-effect count
6. Add tests for storage round trips and filtering.

Verification:

- Existing replay tests pass against file storage and SQLite storage.
- `GET /api/runs?ticket_id=...` uses indexed filtering.

## Phase 5: Real Agent/Tool Integration

Goal: Move from deterministic demo doubles to realistic agent instrumentation by first implementing **LangChain** integration, and then upgrading it to **LangGraph** once the baseline is verified.

### 1. Proposed Architecture & Directory Structure

To support real frameworks without bloating core schema logic, we introduce a dedicated adapters package:

*   `recorder/adapters/base.py`: Declares abstract base classes and interfaces for LLM and Tool adapters.
*   `recorder/adapters/langchain_adapter.py`: Implements LangChain-specific wrappers for LLM (`BaseChatModel`), Tools (`BaseTool`), and Callback handlers.
*   `recorder/adapters/langgraph_adapter.py`: Extends telemetry to support LangGraph state transitions, utilizing the underlying LangChain callbacks.
*   `recorder/adapters/replay_adapter.py`: Wraps tools/LLMs in "replay mode" using recorded payloads.

### 2. Interface Definitions & Implementation Tasks

#### Task 5.1: Core Adapter Interfaces (`recorder/adapters/base.py`)
Define clean abstractions that wrap live execution, capture telemetry, and populate the [AgentExecutionRecord](file:///home/ala-adn/code/gommage/recorder/serializer/aer_schema.py).

*   **`BaseLLMAdapter`**:
    *   `capture_input(prompt: str, system_message: str = "", context: dict = None)`
    *   `capture_output(response: str, token_usage: dict = None, model_name: str = "")`
    *   Methods to track latency and record the step via [StepSnapshotBuilder](file:///home/ala-adn/code/gommage/recorder/serializer/step_snapshot.py).
*   **`BaseToolAdapter`**:
    *   `execute(tool_name: str, parameters: dict, context: dict = None) -> Any`
    *   Side-effect classification via [MockRegistry](file:///home/ala-adn/code/gommage/recorder/proxy/mock_registry.py).
    *   Telemetry capture (latency, error capture, returned results).

#### Task 5.2: LangChain Integration (Phase 5a)
Implement the baseline adapters using standard LangChain callback hooks:
*   **`LangChainLLMCallbackHandler` / `GommageChatModelWrapper`**:
    *   Implement as a custom LangChain `BaseCallbackHandler` or model wrapper targeting `on_llm_start` and `on_llm_end`.
    *   Extract prompt/messages, output content, latency, and token usage.
*   **`GommageLangChainToolWrapper`**:
    *   Inherits from LangChain's `BaseTool` or wraps an existing `BaseTool`.
    *   Delegates call to `ToolProxy.call` in [tool_proxy.py](file:///home/ala-adn/code/gommage/recorder/proxy/tool_proxy.py) or performs equivalent tracking.

#### Task 5.3: LangGraph Upgrade (Phase 5b)
Once the LangChain baseline works, layer on LangGraph tracking:
*   **`LangGraphNodeInterceptor`**:
    *   Implement as a decorator or custom graph configuration to intercept LangGraph nodes.
    *   Map the input state of the node and the resulting state update to the AER sequence, while using the underlying LangChain adapters for internal LLM/tool steps.

#### Task 5.4: Generic Function & MCP Adapter Support
*   Add a standard Python function/decorator adapter (`@gommage_tool`) to wrap arbitrary Python functions.
*   Add a simple JSON-RPC schema handler for Model Context Protocol (MCP) tool shapes, converting `call_tool` request/response cycles into AER steps.

#### Task 5.5: Replay Adapters (`recorder/adapters/replay_adapter.py`)
Add replay adapters that allow replaying agent executions without executing live APIs or side-effecting code:
*   **`ReplayChatModel`**: Implements LangChain's `BaseChatModel`. When invoked, reads the current step from the replay record and returns the recorded (or edited) response directly.
*   **`ReplayTool`**: Implements LangChain's `BaseTool`. Returns the cached tool result (or mocked side-effect payload) matching the parameters.

#### Task 5.6: Add "Wrapping a Real Agent" Documentation
Create `docs/adapters_guide.md` with explicit Python examples showing:
1. Initializing an [AgentExecutionRecord](file:///home/ala-adn/code/gommage/recorder/serializer/aer_schema.py).
2. Wrapping a LangChain or LangGraph agent.
3. Executing in Recording Mode vs Replay Mode.

### 3. Verification

We will verify end-to-end integration via two test scripts:
1. **LangChain Verification (`tests/test_langchain_integration.py`)**:
   - Construct a small LangChain agent using a mocked `ChatOpenAI` double and a side-effecting Tool.
   - Assert AER trace matches expected schema, and replay successfully blocks side-effects.
2. **LangGraph Verification (`tests/test_langgraph_integration.py`)**:
   - Wrap a LangGraph `StateGraph` agent, running it through recording and replay paths.
   - Verify state transition mapping and nested step capture are correct.

## Phase 6: UI Polish

Goal: Make the Jira panel clear and demo-ready.

Tasks:

1. Add a compact first-run empty state with a `Record current issue` button.
2. Add colored badges for:
   - LLM step
   - read-only tool
   - side-effecting tool
   - mocked in replay
   - edited
   - diverged
3. Add side-by-side original vs replay output diff.
4. Add a step timeline or branch graph.
5. Add loading states for each action.
6. Add copy/download trace buttons.
7. Add better mobile/narrow-panel layout.

Verification:

- Panel is usable on a standard Jira issue page width.
- Long prompts/tool payloads scroll without breaking layout.
- Empty/error states are understandable without reading docs.

## Phase 7: Evaluation And Demo Script

Goal: Make the project easy to judge and reproduce.

Tasks:

1. Add scripted demo scenarios:
   - side-effect trap
   - SQL divergence
   - compliance audit
2. Add a single command to seed demo traces.
3. Add a demo checklist for presenters.
4. Add metrics output for each scenario:
   - RFS
   - MRR
   - side effects blocked
   - divergence count
5. Add screenshots or GIFs after the Jira UI stabilizes.

Verification:

```bash
python main.py eval
```

Expected:

- RFS near `1.00` for unedited replay.
- MRR `1.00`.
- At least one side-effecting call blocked.

## Suggested Ownership

- Jira/Forge owner:
  - `replay/ui/forge`
  - Jira REST calls
  - app install/deploy
  - issue attachment and linked issue creation

- Backend/API owner:
  - `replay/ui/server.py`
  - future backend split
  - auth
  - endpoint validation

- Replay/core owner:
  - `recorder`
  - `replay/engine`
  - side-effect detection
  - storage interface

- Demo/evaluation owner:
  - `agent`
  - `evaluation`
  - demo scenarios
  - README/demo script

## Immediate Next Steps

1. Redeploy Forge after the Vite relative asset-path fix.
2. Confirm the panel no longer stops at `Loading Forge Custom UI bundle...`.
3. If it still fails, run:

```bash
npx forge logs --tail
```

4. Once UI loads, test the full Jira flow on `gommage.atlassian.net`.
5. Start Phase 2 by passing real Jira issue fields into the backend record call.
