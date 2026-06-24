# Gommage Jira Debug Demo Script

## Setup

1. Seed varied deterministic traces:

   ```bash
   .venv/bin/python main.py seed-triage-demo --count 20
   ```

2. Build the Forge Custom UI:

   ```bash
   cd replay/ui/forge/static/gommage-replay
   npm run build
   ```

3. Start the local debug UI:

   ```bash
   .venv/bin/python main.py ui --port 8010
   ```

## Recording Outline

1. Open the dashboard and show the trace table filters, sortable columns, latency cards, sparklines, radar distribution, activity stream, and aggregate Sankey flow.
2. Select a high-impact trace such as a `DEMO-102-*` incident run.
3. In the issue trace view, step through the timeline and call out the LLM/tool/idle timing split.
4. Open the thinking viewer and show the intent, observation, inference, and side-effect markers.
5. Edit a side-effecting tool parameter, replay, and show the sandbox overlay write plus the unrecorded-tool-call prompt.
6. Create or preview the linked fix issue after replay evidence is available.

## Captured Screenshots

- `docs/demo_artifacts/trace-dashboard.png`
- `docs/demo_artifacts/trace-inspector.png`
