# Gommage Project Status

## Done

- Implemented the core Agent Execution Record (AER) schema with JSON serialization, trace hashing, validation, LLM step payloads, tool step payloads, and evidence links.
- Built recording proxies for LLM calls and tool calls.
- Added side-effect detection through `MockRegistry`, including email/notification-style tools and mutating SQL.
- Implemented deterministic replay that returns recorded tool payloads and mocks side-effecting calls instead of executing them again.
- Added prompt editing and tool-result injection for replay branches.
- Added divergence tracking between original and edited traces.
- Added local JSON trace storage under `.gommage/traces`.
- Added a local Jira attachment adapter for demo-style trace attachment.
- Built deterministic demo agents and tools:
  - Jira triage agent
  - Confluence audit agent
  - Jira ticket tool
  - DB query tool
  - Email tool with real local outbox side effect under `.gommage/outbox`
- Added replay/evaluation metrics:
  - Replay Fidelity Score (RFS)
  - Mock Recall Rate (MRR)
- Added CLI commands:
  - `python main.py record-demo`
  - `python main.py replay <run_id>`
  - `python main.py eval`
  - `python main.py ui`
- Built a local browser UI for recording, inspecting, replaying, editing prompts/tool results, and confirming side-effect blocking.
- Added a Forge Jira app scaffold:
  - `jira:issuePanel`
  - `jira:issueContext`
  - Custom UI frontend
  - Forge resolver
  - Jira trace attachment flow
  - Linked fix issue creation flow
- Configured Forge backend integration through `GOMMAGE_BACKEND_URL`.
- Added ngrok-compatible egress permissions for local demos.
- Fixed Forge Custom UI asset loading by switching Vite to relative asset paths.
- Added tests for schema round-trip, proxies, side-effect detection, replay, editing, and email outbox behavior.
- Updated README with local and Jira-native setup instructions.

## Yet To Be Done

- Verify the deployed Forge panel end-to-end inside Jira after the latest relative asset-path fix.
- Confirm the panel receives the Jira issue key correctly from Forge context on both `jira:issuePanel` and `jira:issueContext`.
- Confirm `Record current issue` attaches the AER JSON to the Jira issue in the new Jira site.
- Confirm `Replay in Debug Mode` works from inside Jira against the ngrok-backed Python backend.
- Confirm prompt edits and tool-result injection update replay divergence in the Jira panel.
- Confirm `Create linked fix issue` creates a Jira issue, links it to the original issue, comments with trace evidence, and attaches the AER file.
- Add a cleaner production backend instead of using `python main.py ui` as both API server and local UI server.
- Add authentication or request signing between Forge and the Gommage backend.
- Replace deterministic demo LLM/tool doubles with adapters for real agent frameworks such as LangGraph, LangChain, CrewAI, or MCP tools.
- Add persistence beyond local JSON files, likely SQLite for demo and PostgreSQL for production.
- Add robust error states in the Forge UI for backend unavailable, invalid ngrok URL, missing issue key, and Jira attachment failures.
- Add CI to run Python tests, Forge lint, and Custom UI build.
- Add packaging/deployment docs for a clean hackathon demo machine.

## Bonus Ideas

- Add a timeline/graph view of the agent trajectory and replay branches.
- Add a side-by-side diff view for original vs edited prompts, tool results, and replay outputs.
- Add a replay scrubber with step-by-step continue controls.
- Add token/cost/latency analytics per step.
- Add search and filtering across traces by tool name, side-effect type, verdict, ticket key, or agent name.
- Add replay snapshots as Jira comments with collapsible step summaries.
- Add one-click export of a trace bundle for audit/compliance review.
- Add support for uploading screenshots or external artifacts as evidence links.
- Add a risk score for traces based on sensitive tool calls, missing evidence, low confidence, or policy violations.
- Add transport-layer verification for tool calls using process/file/network interception.
- Add a policy engine that blocks live side-effecting tools unless explicitly approved.
- Add a “safe prompt suggestion” helper that proposes a safer step-4 prompt after a risky replay.
- Add real Jira issue data ingestion so the demo agent reads the actual issue summary/description instead of synthetic ticket fixtures.
- Add Slack/email/Jira write adapters with replay-safe mocks.
- Add demo seed data and a scripted end-to-end scenario for judges.
- Add Docker Compose for the backend, database, and tunnel setup.
- Add marketplace-grade Forge app polish: app icon, onboarding screen, admin settings page, and install documentation.
