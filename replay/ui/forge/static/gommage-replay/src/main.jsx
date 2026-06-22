import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { invoke, view } from "@forge/bridge";
import "./styles.css";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Gommage Replay render failure", error, info);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <main className="app diagnostic-screen">
        <h1>Gommage Replay</h1>
        <p>The panel loaded, but the React app failed to render.</p>
        <pre>{this.state.error.message || String(this.state.error)}</pre>
      </main>
    );
  }
}

function formatMetric(value) {
  if (value === undefined || value === null) return "-";
  return Number(value).toFixed(2);
}

function stepLabel(step) {
  return step.tool?.tool_name || step.llm?.model || step.kind;
}

function App() {
  const [status, setStatus] = useState("Loading Jira context");
  const [error, setError] = useState("");
  const [issueContext, setIssueContext] = useState(null);
  const [runs, setRuns] = useState([]);
  const [record, setRecord] = useState(null);
  const [replay, setReplay] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [selectedStepId, setSelectedStepId] = useState(null);
  const [edits, setEdits] = useState(new Map());
  const [fixSummary, setFixSummary] = useState("");
  const [rawForgeContext, setRawForgeContext] = useState(null);

  const selectedStep = useMemo(
    () => record?.steps.find((step) => step.step_id === selectedStepId),
    [record, selectedStepId],
  );

  async function guarded(action) {
    setError("");
    try {
      await action();
    } catch (caught) {
      setError(caught.message || String(caught));
      setStatus("Action failed");
    }
  }

  async function loadRuns(contextOverride = issueContext) {
    if (!contextOverride?.issueKey) return;
    setStatus(`Loading traces for ${contextOverride.issueKey}`);
    const data = await invoke("listRuns", { issueKey: contextOverride.issueKey });
    setRuns(data.runs || []);
    setStatus(`Loaded traces for ${contextOverride.issueKey}`);
  }

  async function recordRun() {
    setStatus(`Recording ${issueContext.issueKey}`);
    const data = await invoke("recordRun", { issueKey: issueContext.issueKey });
    setRecord(data.record);
    setReplay(null);
    setMetrics(null);
    setSelectedStepId(data.record.steps[0]?.step_id ?? null);
    setEdits(new Map());
    await loadRuns();
    setStatus(`Recorded ${data.record.run_id}`);
  }

  async function loadRun(runId) {
    setStatus(`Loading ${runId}`);
    const data = await invoke("getRun", { runId });
    setRecord(data.record);
    setReplay(null);
    setMetrics(null);
    setSelectedStepId(data.record.steps[0]?.step_id ?? null);
    setEdits(new Map());
    setStatus(`Loaded ${runId}`);
  }

  async function replayRun(nextEdits = edits) {
    if (!record) return;
    setStatus(`Replaying ${record.run_id}`);
    const data = await invoke("replayRun", {
      runId: record.run_id,
      edits: Array.from(nextEdits.values()),
    });
    setReplay(data.result);
    setMetrics(data.metrics);
    setStatus(`Replayed ${record.run_id}`);
  }

  async function createFixIssue() {
    if (!record) return;
    setStatus("Creating linked Jira issue");
    const data = await invoke("createFixIssue", {
      issueKey: issueContext.issueKey,
      projectKey: issueContext.projectKey,
      runId: record.run_id,
      edits: Array.from(edits.values()),
      replayMetrics: metrics,
      summary: fixSummary || `Fix agent prompt for ${issueContext.issueKey}`,
    });
    setStatus(`Created ${data.fixIssue.key}`);
  }

  function applyPromptEdit(step, prompt) {
    const next = new Map(edits);
    next.set(step.step_id, {
      step_id: step.step_id,
      prompt,
      note: "Prompt edited in Jira issue panel",
    });
    setEdits(next);
    guarded(() => replayRun(next));
  }

  function applyToolResultEdit(step, rawJson) {
    let toolResult;
    try {
      toolResult = JSON.parse(rawJson);
    } catch (caught) {
      setError(`Invalid JSON: ${caught.message}`);
      return;
    }
    const next = new Map(edits);
    next.set(step.step_id, {
      step_id: step.step_id,
      tool_result: toolResult,
      note: "Tool result injected in Jira issue panel",
    });
    setEdits(next);
    guarded(() => replayRun(next));
  }

  useEffect(() => {
    guarded(async () => {
      const context = await view.getContext();
      setRawForgeContext(context);
      const data = await invoke("getIssueContext", {
        issueKey:
          context.extension?.issue?.key ||
          context.extension?.issueKey ||
          context.extension?.platformContext?.issueKey,
        projectKey:
          context.extension?.project?.key ||
          context.extension?.projectKey ||
          context.extension?.platformContext?.projectKey,
      });
      setIssueContext(data);
      await loadRuns(data);
    });
  }, []);

  const sideEffects = record?.steps.filter((step) => step.tool?.side_effecting).length || 0;
  const replayStep = selectedStep
    ? replay?.replayed_steps?.find((step) => step.step_id === selectedStep.step_id)
    : null;

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>Gommage Replay</h1>
          <p>{issueContext?.issueKey || "Jira issue panel"}</p>
        </div>
        <div className="actions">
          <button className="primary" onClick={() => guarded(recordRun)}>
            Record current issue
          </button>
          <button disabled={!record} onClick={() => guarded(() => replayRun())}>
            Replay in Debug Mode
          </button>
          <button disabled={!record || !metrics} onClick={() => guarded(createFixIssue)}>
            Create linked fix issue
          </button>
        </div>
      </header>

      <section className="status-line">
        <span>{status}</span>
        {error ? <strong>{error}</strong> : null}
      </section>

      <section className="metrics">
        <Metric label="Run" value={record?.run_id || "None"} />
        <Metric label="Steps" value={record?.steps.length || 0} />
        <Metric label="Side effects" value={sideEffects} />
        <Metric label="Blocked" value={metrics?.side_effects_blocked || 0} />
        <Metric label="RFS" value={formatMetric(metrics?.replay_fidelity)} />
        <Metric label="MRR" value={formatMetric(metrics?.mock_recall)} />
      </section>

      <section className="workspace">
        <aside className="panel">
          <PanelHeader title="Attached traces" action={() => guarded(() => loadRuns())} actionLabel="Refresh" />
          <div className="list">
            {runs.length ? (
              runs.map((run) => (
                <button
                  className={`row ${record?.run_id === run.run_id ? "active" : ""}`}
                  key={run.run_id}
                  onClick={() => guarded(() => loadRun(run.run_id))}
                >
                  <strong>{run.run_id}</strong>
                  <span>{run.steps} steps · {run.side_effecting_tools} side effects</span>
                </button>
              ))
            ) : (
              <p className="empty">No traces attached to this issue yet.</p>
            )}
          </div>
        </aside>

        <section className="panel">
          <PanelHeader title="Trajectory" />
          <div className="list">
            {record?.steps.map((step) => {
              const result = replay?.replayed_steps?.find((item) => item.step_id === step.step_id);
              return (
                <button
                  className={`row ${selectedStepId === step.step_id ? "active" : ""}`}
                  key={step.step_id}
                  onClick={() => setSelectedStepId(step.step_id)}
                >
                  <strong>#{step.step_id} {step.intent || step.kind}</strong>
                  <span>{stepLabel(step)}</span>
                  <span className="badges">
                    {step.tool?.side_effecting ? <Badge tone="red">side effect</Badge> : null}
                    {result?.mocked ? <Badge tone="yellow">mocked</Badge> : null}
                    {edits.has(step.step_id) ? <Badge tone="green">edited</Badge> : null}
                  </span>
                </button>
              );
            }) || <p className="empty">Select or record a trace.</p>}
          </div>
        </section>

        <section className="panel inspector">
          <PanelHeader title="Debugger" />
          {selectedStep ? (
            <StepInspector
              step={selectedStep}
              replayStep={replayStep}
              edit={edits.get(selectedStep.step_id)}
              onPromptEdit={applyPromptEdit}
              onToolResultEdit={applyToolResultEdit}
            />
          ) : (
            <p className="empty">No step selected.</p>
          )}
          {record ? (
            <div className="fix-box">
              <label htmlFor="fix-summary">Fix issue summary</label>
              <input
                id="fix-summary"
                value={fixSummary}
                placeholder={`Fix agent prompt for ${issueContext?.issueKey || "this issue"}`}
                onChange={(event) => setFixSummary(event.target.value)}
              />
            </div>
          ) : null}
          {!record && rawForgeContext ? (
            <details className="diagnostics">
              <summary>Diagnostics</summary>
              <pre>{JSON.stringify(rawForgeContext.extension || rawForgeContext, null, 2)}</pre>
            </details>
          ) : null}
        </section>
      </section>
    </main>
  );
}

function Metric({ label, value }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PanelHeader({ title, action, actionLabel }) {
  return (
    <header className="panel-header">
      <h2>{title}</h2>
      {action ? <button onClick={action}>{actionLabel}</button> : null}
    </header>
  );
}

function Badge({ children, tone }) {
  return <em className={tone ? `badge ${tone}` : "badge"}>{children}</em>;
}

function StepInspector({ step, replayStep, edit, onPromptEdit, onToolResultEdit }) {
  const [prompt, setPrompt] = useState(edit?.prompt || step.llm?.prompt || "");
  const [toolResult, setToolResult] = useState(
    JSON.stringify(edit?.tool_result ?? step.tool?.result ?? {}, null, 2),
  );

  useEffect(() => {
    setPrompt(edit?.prompt || step.llm?.prompt || "");
    setToolResult(JSON.stringify(edit?.tool_result ?? step.tool?.result ?? {}, null, 2));
  }, [step.step_id, edit, step.llm?.prompt, step.tool?.result]);

  return (
    <div className="inspector-body">
      <section>
        <h3>Step {step.step_id}</h3>
        <p>{step.intent}</p>
        <div className="badges">
          <Badge>{step.kind}</Badge>
          {step.tool?.side_effecting ? <Badge tone="red">side effect</Badge> : null}
          {replayStep?.side_effect_blocked ? <Badge tone="yellow">blocked in replay</Badge> : null}
        </div>
      </section>

      {step.llm ? (
        <section>
          <h3>Prompt</h3>
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} />
          <button onClick={() => onPromptEdit(step, prompt)}>Rewrite prompt and continue</button>
          <h3>Recorded response</h3>
          <pre>{step.llm.response}</pre>
        </section>
      ) : null}

      {step.tool ? (
        <section>
          <h3>Tool parameters</h3>
          <pre>{JSON.stringify(step.tool.parameters, null, 2)}</pre>
          <h3>Recorded result</h3>
          <pre>{JSON.stringify(step.tool.result, null, 2)}</pre>
          <h3>Injected result</h3>
          <textarea value={toolResult} onChange={(event) => setToolResult(event.target.value)} />
          <button onClick={() => onToolResultEdit(step, toolResult)}>
            Inject result and continue
          </button>
        </section>
      ) : null}

      {replayStep ? (
        <section>
          <h3>Replay output</h3>
          <pre>{JSON.stringify(replayStep.output, null, 2)}</pre>
        </section>
      ) : null}
    </div>
  );
}

const root = document.getElementById("root");

try {
  createRoot(root).render(
    <ErrorBoundary>
      <App />
    </ErrorBoundary>,
  );
} catch (error) {
  root.innerHTML = `
    <main class="app diagnostic-screen">
      <h1>Gommage Replay</h1>
      <p>The panel loaded, but the app could not start.</p>
      <pre>${String(error?.message || error)}</pre>
    </main>
  `;
}
