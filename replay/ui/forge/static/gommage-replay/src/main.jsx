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

function formatMs(value) {
  const ms = Number(value || 0);
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)}s`;
  return `${Math.round(ms)}ms`;
}

function stepLabel(step) {
  return step.tool?.tool_name || step.llm?.model || step.kind;
}

function flowNodeKey(step, depth) {
  if (step.kind === "tool" && step.tool) {
    return `${depth}:tool:${step.tool.tool_name}`;
  }
  if (step.kind === "llm") {
    return `${depth}:llm:${step.intent || step.llm?.model || "LLM"}`;
  }
  return `${depth}:${step.kind}:${step.intent || step.kind}`;
}

function shortLabel(value, max = 24) {
  if (!value) return "";
  return value.length > max ? `${value.slice(0, max - 3)}...` : value;
}

function timestampDiffMs(start, end) {
  if (!start || !end) return 0;
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return 0;
  return Math.max(0, endMs - startMs);
}

function stepTiming(steps, step) {
  const index = steps.findIndex((item) => item.step_id === step.step_id);
  const nextStep = index >= 0 ? steps[index + 1] : null;
  const llmMs = Number(step.llm?.latency_ms || 0);
  const toolMs = Number(step.tool?.latency_ms || 0);
  const timestampGapMs = timestampDiffMs(step.timestamp, nextStep?.timestamp);
  const idleMs = Math.max(0, timestampGapMs - llmMs - toolMs);
  return { llmMs, toolMs, idleMs, timestampGapMs };
}

function timingLabel(timing) {
  return `LLM ${formatMs(timing.llmMs)} - Tool ${formatMs(timing.toolMs)} - Idle ${formatMs(timing.idleMs)}`;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function sortJson(value) {
  if (Array.isArray(value)) return value.map(sortJson);
  if (isPlainObject(value)) {
    return Object.keys(value)
      .sort()
      .reduce((acc, key) => {
        acc[key] = sortJson(value[key]);
        return acc;
      }, {});
  }
  return value;
}

function stableStringify(value) {
  if (value === undefined) return "undefined";
  return JSON.stringify(sortJson(value), null, 2);
}

function inlineValue(value) {
  const text = stableStringify(value);
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
}

function diffContexts(before, after) {
  const lines = [];
  walkDiff("", before || {}, after || {}, lines);
  return lines;
}

function walkDiff(path, before, after, lines) {
  if (stableStringify(before) === stableStringify(after)) return;
  if (isPlainObject(before) && isPlainObject(after)) {
    const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
    keys.forEach((key) => {
      const nextPath = path ? `${path}.${key}` : key;
      if (!(key in after)) {
        lines.push({ type: "remove", text: `- ${nextPath}: ${inlineValue(before[key])}` });
      } else if (!(key in before)) {
        lines.push({ type: "add", text: `+ ${nextPath}: ${inlineValue(after[key])}` });
      } else {
        walkDiff(nextPath, before[key], after[key], lines);
      }
    });
    return;
  }
  lines.push({ type: "remove", text: `- ${path || "context"}: ${inlineValue(before)}` });
  lines.push({ type: "add", text: `+ ${path || "context"}: ${inlineValue(after)}` });
}

const RADAR_CATEGORIES = [
  ["information_gathering", "Info"],
  ["decision_making", "Decision"],
  ["communication", "Comms"],
  ["data_mutation", "Mutation"],
  ["evidence_collection", "Evidence"],
  ["error_recovery", "Recovery"],
];

function parseTime(value) {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function durationMs(startedAt, completedAt) {
  const started = parseTime(startedAt);
  const completed = parseTime(completedAt);
  if (!started || !completed || completed < started) return 0;
  return completed - started;
}

function stepLatency(step) {
  return Number(step.llm?.latency_ms || 0) + Number(step.tool?.latency_ms || 0);
}

function classifyDashboardStep(step) {
  if (step.category) return step.category;
  if (step.kind === "llm") {
    const intent = String(step.intent || "").toLowerCase();
    return /(classify|decide|decision|triage|plan|route)/.test(intent) ? "decision_making" : "reasoning";
  }
  if (step.tool) {
    if (step.tool.error) return "error_recovery";
    if (step.tool.side_effecting) {
      return ["email.send", "slack.post", "sms.send"].includes(step.tool.tool_name)
        ? "communication"
        : "data_mutation";
    }
    return "information_gathering";
  }
  return step.evidence?.length ? "evidence_collection" : "other";
}

function canonicalDashboardStep(step) {
  if (step.canonical_id) return step.canonical_id;
  if (step.tool) {
    return `tool:${step.tool.tool_name}:{${Object.keys(step.tool.parameters || {}).sort().join(",")}}`;
  }
  return `${step.kind}:${String(step.intent || step.kind).toLowerCase().replace(/[^a-z0-9]+/g, "_")}`;
}

function indexFromRecord(record) {
  const steps = record?.steps || [];
  const categoryDistribution = {};
  steps.forEach((step) => {
    const category = classifyDashboardStep(step);
    categoryDistribution[category] = (categoryDistribution[category] || 0) + 1;
  });
  const toolSteps = steps.filter((step) => step.tool);
  const totalLatency = steps.reduce((total, step) => total + stepLatency(step), 0);
  return {
    run_id: record.run_id,
    ticket_id: record.jira_ticket_id,
    agent_name: record.agent_name,
    status: record.status,
    started_at: record.started_at,
    completed_at: record.completed_at,
    steps: steps.length,
    step_count: steps.length,
    llm_call_count: steps.filter((step) => step.llm).length,
    tool_call_count: toolSteps.length,
    tool_error_count: toolSteps.filter((step) => step.tool?.error).length,
    side_effecting_tools: toolSteps.filter((step) => step.tool?.side_effecting).length,
    side_effect_count: toolSteps.filter((step) => step.tool?.side_effecting).length,
    total_latency_ms: totalLatency,
    duration_ms: durationMs(record.started_at, record.completed_at) || totalLatency,
    avg_tool_latency_ms: toolSteps.length
      ? toolSteps.reduce((total, step) => total + Number(step.tool?.latency_ms || 0), 0) / toolSteps.length
      : 0,
    category_distribution: categoryDistribution,
    canonical_path: steps.map(canonicalDashboardStep),
  };
}

function indexFromSummary(run) {
  return {
    ...run,
    ticket_id: run.ticket_id || run.jira_ticket_id,
    steps: Number(run.steps ?? run.step_count ?? 0),
    step_count: Number(run.step_count ?? run.steps ?? 0),
    llm_call_count: Number(run.llm_call_count || 0),
    tool_call_count: Number(run.tool_call_count || 0),
    tool_error_count: Number(run.tool_error_count || 0),
    side_effecting_tools: Number(run.side_effecting_tools ?? run.side_effect_count ?? 0),
    side_effect_count: Number(run.side_effect_count ?? run.side_effecting_tools ?? 0),
    total_latency_ms: Number(run.total_latency_ms || 0),
    duration_ms: Number(run.duration_ms || durationMs(run.started_at, run.completed_at)),
    avg_tool_latency_ms: Number(run.avg_tool_latency_ms || 0),
    category_distribution: run.category_distribution || {},
    canonical_path: run.canonical_path || [],
  };
}

function mergeIndexes(runs, records) {
  const byRunId = new Map((runs || []).map((run) => [run.run_id, indexFromSummary(run)]));
  (records || []).forEach((record) => {
    if (record?.run_id) byRunId.set(record.run_id, indexFromRecord(record));
  });
  return Array.from(byRunId.values()).filter((item) => item.run_id && !item.error);
}

function summarizeDashboard(indexes) {
  const totalRuns = indexes.length;
  const totalTools = indexes.reduce((total, run) => total + run.tool_call_count, 0);
  const totalErrors = indexes.reduce((total, run) => total + run.tool_error_count, 0);
  const totalLatency = indexes.reduce((total, run) => total + (run.duration_ms || run.total_latency_ms || 0), 0);
  const totalToolDuration = indexes.reduce((total, run) => total + run.avg_tool_latency_ms * run.tool_call_count, 0);
  return {
    runs: totalRuns,
    avgLatency: totalRuns ? totalLatency / totalRuns : 0,
    toolCalls: totalTools,
    toolErrorRate: totalTools ? totalErrors / totalTools : 0,
    avgToolDuration: totalTools ? totalToolDuration / totalTools : 0,
    sideEffects: indexes.reduce((total, run) => total + run.side_effect_count, 0),
  };
}

function sparklineValues(indexes, accessor) {
  return indexes
    .slice()
    .sort((a, b) => parseTime(a.started_at) - parseTime(b.started_at))
    .slice(-20)
    .map(accessor);
}

function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function buildSankey(records) {
  const nodes = new Map();
  const links = new Map();
  let maxDepth = 1;

  function touchNode(id, label, depth, kind) {
    const existing = nodes.get(id) || { id, label, depth, kind, value: 0, runIds: [] };
    existing.value += 1;
    nodes.set(id, existing);
    maxDepth = Math.max(maxDepth, depth);
    return existing;
  }

  records.forEach((record) => {
    if (!record?.steps?.length) return;
    touchNode("0:start", "START", 0, "start").runIds.push(record.run_id);
    let previous = "0:start";

    record.steps.forEach((step, index) => {
      const depth = index + 1;
      const id = flowNodeKey(step, depth);
      const label = step.tool?.tool_name || step.intent || step.kind;
      touchNode(id, label, depth, step.kind).runIds.push(record.run_id);
      const linkKey = `${previous}->${id}`;
      links.set(linkKey, {
        source: previous,
        target: id,
        value: (links.get(linkKey)?.value || 0) + 1,
      });
      previous = id;
    });

    const endId = `${record.steps.length + 1}:end`;
    touchNode(endId, "END", record.steps.length + 1, "end").runIds.push(record.run_id);
    const linkKey = `${previous}->${endId}`;
    links.set(linkKey, {
      source: previous,
      target: endId,
      value: (links.get(linkKey)?.value || 0) + 1,
    });
  });

  return { nodes: Array.from(nodes.values()), links: Array.from(links.values()), maxDepth };
}

function App() {
  const [ticketScope, setTicketScope] = useState("");
  const [status, setStatus] = useState("Loading Jira context");
  const [error, setError] = useState("");
  const [issueContext, setIssueContext] = useState(null);
  const [activeRunTicket, setActiveRunTicket] = useState(null);
  const [runs, setRuns] = useState([]);
  const [record, setRecord] = useState(null);
  const [replay, setReplay] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [selectedStepId, setSelectedStepId] = useState(null);
  const [edits, setEdits] = useState(new Map());
  const [fixSummary, setFixSummary] = useState("");
  const [rawForgeContext, setRawForgeContext] = useState(null);
  const [aggregateRecords, setAggregateRecords] = useState([]);
  const [sandboxFork, setSandboxFork] = useState(null);

  const selectedStep = useMemo(
    () => record?.steps.find((step) => step.step_id === selectedStepId),
    [record, selectedStepId],
  );
  const previousSelectedStep = useMemo(() => {
    if (!record || selectedStepId === null) return null;
    const index = record.steps.findIndex((step) => step.step_id === selectedStepId);
    return index > 0 ? record.steps[index - 1] : null;
  }, [record, selectedStepId]);
  const isIssueContext = Boolean(issueContext?.issueKey);
  const actionTargetIssue = issueContext?.issueKey || activeRunTicket || null;
  const actionTargetProject =
    issueContext?.projectKey ||
    (activeRunTicket ? activeRunTicket.split("-").slice(0, -1).join("-") : null);
  const boardLabel = issueContext?.boardId
    ? `${issueContext.boardType ? `${issueContext.boardType} board` : "Board"} ${issueContext.boardId}`
    : "Jira dashboard";

  async function guarded(action) {
    setError("");
    try {
      await action();
    } catch (caught) {
      setError(caught.message || String(caught));
      setStatus("Action failed");
    }
  }

  async function loadRuns(contextOverride = issueContext, ticketId) {
    const scopeIssueKey = ticketId || contextOverride?.issueKey;
    if (scopeIssueKey) {
      setStatus(`Loading traces for ${scopeIssueKey}`);
      const data = await invoke("listRuns", { issueKey: scopeIssueKey });
      setRuns(data.runs || []);
      setStatus(`Loaded traces for ${scopeIssueKey}`);
      return;
    }
    setStatus(`Loading traces for ${boardLabel}`);
    const data = await invoke("listRuns", {});
    setRuns(data.runs || []);
    setStatus(`Loaded traces for ${boardLabel}`);
  }

  async function recordRun() {
    if (!issueContext?.issueKey) {
      setStatus("Jira issue context is not available yet");
      setError("Open Gommage Replay from a full Jira issue page and refresh.");
      return;
    }
    setStatus(`Recording ${issueContext.issueKey}`);
    const data = await invoke("recordRun", { issueKey: issueContext.issueKey });
    setRecord(data.record);
    setReplay(null);
    setMetrics(null);
    setSelectedStepId(data.record.steps[0]?.step_id ?? null);
    setEdits(new Map());
    setSandboxFork(null);
    await loadRuns();
    setStatus(`Recorded ${data.record.run_id}`);
  }

  async function loadRun(runId) {
    setStatus(`Loading ${runId}`);
    const data = await invoke("getRun", { runId });
    const runTicket = data.summary?.ticket_id || data.record?.ticket_id || null;
    setActiveRunTicket(runTicket);
    setRecord(data.record);
    setReplay(null);
    setMetrics(null);
    setSelectedStepId(data.record.steps[0]?.step_id ?? null);
    setEdits(new Map());
    setSandboxFork(null);
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
    setStatus(
      data.result?.mode === "sandbox_overlay"
        ? `Replayed ${record.run_id} in sandbox overlay`
        : `Replayed ${record.run_id}`,
    );
  }

  function selectRelativeStep(offset) {
    if (!record?.steps?.length) return;
    const currentIndex = Math.max(
      0,
      record.steps.findIndex((step) => step.step_id === selectedStepId),
    );
    const nextIndex = Math.min(record.steps.length - 1, Math.max(0, currentIndex + offset));
    setSelectedStepId(record.steps[nextIndex].step_id);
  }

  async function forkFromSelectedStep() {
    if (!record || selectedStepId === null) return;
    const next = new Map(edits);
    next.set(selectedStepId, {
      ...(next.get(selectedStepId) || {}),
      step_id: selectedStepId,
      note: "Forked into sandbox overlay from Jira issue panel",
    });
    setEdits(next);
    setSandboxFork({ stepId: selectedStepId, runId: record.run_id });
    await replayRun(next);
  }

  async function createFixIssue() {
    if (!record) return;
    if (!actionTargetIssue || !actionTargetProject) {
      setStatus("Board context lacks issue target");
      setError("Open the trace for a specific issue and use Create linked fix issue from that issue context.");
      return;
    }
    setStatus("Creating linked Jira issue");
    const data = await invoke("createFixIssue", {
      issueKey: actionTargetIssue,
      projectKey: actionTargetProject,
      runId: record.run_id,
      edits: Array.from(edits.values()),
      replayMetrics: metrics,
      summary: fixSummary || `Fix agent prompt for ${actionTargetIssue}`,
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

  function applyToolParameterEdit(step, rawJson) {
    let toolParameters;
    try {
      toolParameters = JSON.parse(rawJson);
    } catch (caught) {
      setError(`Invalid JSON: ${caught.message}`);
      return;
    }
    const next = new Map(edits);
    next.set(step.step_id, {
      ...(next.get(step.step_id) || {}),
      step_id: step.step_id,
      tool_parameters: toolParameters,
      note: "Tool parameters edited in Jira issue panel",
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
        board:
          context.extension?.board,
        action:
          context.extension?.action,
      });
      setIssueContext(data);
      setActiveRunTicket(data?.issueKey || null);
      await loadRuns(data);
    });
  }, []);

  useEffect(() => {
    if (isIssueContext || !runs.length) {
      setAggregateRecords([]);
      return;
    }

    let cancelled = false;
    guarded(async () => {
      const records = await Promise.all(
        runs
          .filter((run) => !run.error)
          .slice(0, 20)
          .map((run) => invoke("getRun", { runId: run.run_id }).then((data) => data.record)),
      );
      if (!cancelled) {
        setAggregateRecords(records.filter(Boolean));
      }
    });

    return () => {
      cancelled = true;
    };
  }, [isIssueContext, runs]);

  const sideEffects = record?.steps.filter((step) => step.tool?.side_effecting).length || 0;
  const aggregateStats = useMemo(() => summarizeRecords(aggregateRecords), [aggregateRecords]);
  const dashboardIndexes = useMemo(() => mergeIndexes(runs, aggregateRecords), [runs, aggregateRecords]);
  const replayStep = selectedStep
    ? replay?.replayed_steps?.find((step) => step.step_id === selectedStep.step_id)
    : null;

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>{isIssueContext ? "Gommage Replay" : "Gommage Demo Dashboard"}</h1>
          <p>{issueContext?.issueKey || boardLabel}</p>
        </div>
        <div className="actions">
          <button className="primary" disabled={!isIssueContext} onClick={() => guarded(recordRun)}>
            Record current issue
          </button>
          {!isIssueContext ? (
            <>
              <input
                value={ticketScope}
                placeholder="Filter by ticket (optional, e.g. PROJ-42)"
                onChange={(event) => setTicketScope(event.target.value)}
              />
              <button
                onClick={() =>
                  guarded(() =>
                    loadRuns(
                      issueContext,
                      ticketScope.trim() ? ticketScope.trim().toUpperCase() : null,
                    ),
                  )
                }
              >
                Apply scope
              </button>
            </>
          ) : null}
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

      {!isIssueContext ? (
        <>
          <DashboardBoard
            indexes={dashboardIndexes}
            aggregateStats={aggregateStats}
            onSelectRun={(runId) => guarded(() => loadRun(runId))}
          />
          <SankeyPanel records={aggregateRecords} runCount={runs.length} onSelectRun={(runId) => guarded(() => loadRun(runId))} />
        </>
      ) : record ? (
        <>
          <ReplayControls
            record={record}
            replay={replay}
            selectedStepId={selectedStepId}
            sandboxFork={sandboxFork}
            onSelectStep={setSelectedStepId}
            onStep={selectRelativeStep}
            onReplay={() => guarded(() => replayRun())}
            onFork={() => guarded(forkFromSelectedStep)}
          />
          <TraceGraphPanel
            record={record}
            replay={replay}
            selectedStepId={selectedStepId}
            onSelectStep={setSelectedStepId}
          />
          <ThinkingViewer
            steps={record.steps || []}
            selectedStepId={selectedStepId}
            onSelectStep={setSelectedStepId}
          />
        </>
      ) : null}

      <section className="workspace">
        <aside className="panel">
          <PanelHeader
            title={isIssueContext ? "Traces for issue" : "All traces"}
            action={() => guarded(() => loadRuns())}
            actionLabel="Refresh"
          />
          <div className="list">
            {runs.length ? (
              runs.map((run) => (
                <button
                  className={`row ${record?.run_id === run.run_id ? "active" : ""}`}
                  key={run.run_id}
                  onClick={() => guarded(() => loadRun(run.run_id))}
                >
                  <strong>{run.run_id}</strong>
                  <span>{run.ticket_id || "No ticket id"}</span>
                  <span>
                    {run.steps} steps - {run.side_effecting_tools} side effects
                  </span>
                </button>
              ))
            ) : (
              <p className="empty">{isIssueContext ? "No traces attached to this issue yet." : "No traces available yet."}</p>
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
              previousStep={previousSelectedStep}
              steps={record?.steps || []}
              replayStep={replayStep}
              edit={edits.get(selectedStep.step_id)}
              replayMode={replay?.mode}
              sandboxWrites={replay?.sandbox_writes || []}
              onPromptEdit={applyPromptEdit}
              onToolParameterEdit={applyToolParameterEdit}
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
                placeholder={`Fix agent prompt for ${actionTargetIssue || "this issue"}`}
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

function summarizeRecords(records) {
  const totals = records.reduce(
    (acc, item) => {
      const steps = item?.steps || [];
      acc.runs += 1;
      acc.steps += steps.length;
      acc.toolCalls += steps.filter((step) => step.kind === "tool").length;
      acc.sideEffects += steps.filter((step) => step.tool?.side_effecting).length;
      acc.toolErrors += steps.filter((step) => step.tool?.error).length;
      if (item?.metadata?.demo_story === "proj-4421-silent-closure") {
        acc.silentClosures += 1;
      }
      return acc;
    },
    { runs: 0, steps: 0, toolCalls: 0, sideEffects: 0, toolErrors: 0, silentClosures: 0 },
  );
  return {
    ...totals,
    avgSteps: totals.runs ? totals.steps / totals.runs : 0,
  };
}

function DashboardBoard({ indexes, aggregateStats, onSelectRun }) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortKey, setSortKey] = useState("started_at");
  const [sortDirection, setSortDirection] = useState("desc");
  const summary = useMemo(() => summarizeDashboard(indexes), [indexes]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const rows = indexes.filter((item) => {
      const matchesStatus = statusFilter === "all" || item.status === statusFilter;
      const matchesQuery =
        !needle ||
        [item.run_id, item.ticket_id, item.agent_name, item.status]
          .filter(Boolean)
          .some((value) => String(value).toLowerCase().includes(needle));
      return matchesStatus && matchesQuery;
    });
    rows.sort((a, b) => {
      const left = sortValue(a, sortKey);
      const right = sortValue(b, sortKey);
      const order = left > right ? 1 : left < right ? -1 : 0;
      return sortDirection === "asc" ? order : -order;
    });
    return rows;
  }, [indexes, query, statusFilter, sortDirection, sortKey]);
  const statuses = Array.from(new Set(indexes.map((item) => item.status).filter(Boolean))).sort();

  function setSort(nextKey) {
    if (sortKey === nextKey) {
      setSortDirection((value) => (value === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextKey);
    setSortDirection("desc");
  }

  return (
    <section className="dashboard-board">
      <section className="metric-grid dashboard-metrics" aria-label="Dashboard metrics">
        <DashboardMetric
          label="Avg latency"
          value={formatMs(summary.avgLatency)}
          values={sparklineValues(indexes, (item) => item.duration_ms || item.total_latency_ms)}
        />
        <DashboardMetric
          label="Tool execs"
          value={summary.toolCalls}
          values={sparklineValues(indexes, (item) => item.tool_call_count)}
        />
        <DashboardMetric
          label="Tool error rate"
          value={formatPercent(summary.toolErrorRate)}
          values={sparklineValues(indexes, (item) =>
            item.tool_call_count ? item.tool_error_count / item.tool_call_count : 0,
          )}
        />
        <DashboardMetric
          label="Avg tool duration"
          value={formatMs(summary.avgToolDuration)}
          values={sparklineValues(indexes, (item) => item.avg_tool_latency_ms)}
        />
      </section>

      <section className="dashboard-grid">
        <section className="panel trace-table-panel">
          <header className="panel-header trace-table-header">
            <h2>Trace list</h2>
            <div className="table-controls">
              <input
                value={query}
                placeholder="Filter run, ticket, agent"
                onChange={(event) => setQuery(event.target.value)}
              />
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="all">All statuses</option>
                {statuses.map((status) => (
                  <option key={status} value={status}>{status}</option>
                ))}
              </select>
            </div>
          </header>
          <div className="trace-table-scroll">
            <table className="trace-table">
              <thead>
                <tr>
                  <SortHeader label="Run" column="run_id" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                  <SortHeader label="Ticket" column="ticket_id" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                  <SortHeader label="Status" column="status" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                  <SortHeader label="Steps" column="steps" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                  <SortHeader label="Tools" column="tool_call_count" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                  <SortHeader label="Errors" column="tool_error_count" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                  <SortHeader label="Duration" column="duration_ms" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                  <SortHeader label="Started" column="started_at" sortKey={sortKey} direction={sortDirection} onSort={setSort} />
                </tr>
              </thead>
              <tbody>
                {filtered.map((row) => (
                  <tr key={row.run_id} onClick={() => onSelectRun(row.run_id)}>
                    <td><strong>{row.run_id}</strong><span>{row.agent_name || "unknown"}</span></td>
                    <td>{row.ticket_id || "-"}</td>
                    <td><StatusBadge status={row.status} /></td>
                    <td>{row.steps}</td>
                    <td>{row.tool_call_count}</td>
                    <td>{row.tool_error_count}</td>
                    <td>{formatMs(row.duration_ms || row.total_latency_ms)}</td>
                    <td>{formatDate(row.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!filtered.length ? <p className="empty table-empty">No traces match the current filters.</p> : null}
          </div>
        </section>

        <section className="panel insight-panel">
          <PanelHeader title="Radar / stats" />
          <RadarChart indexes={indexes} />
          <div className="insight-stats">
            <Metric label="Runs" value={summary.runs} />
            <Metric label="Side effects" value={summary.sideEffects} />
            <Metric label="Avg steps" value={formatMetric(aggregateStats.avgSteps)} />
            <Metric label="Silent closures" value={aggregateStats.silentClosures} />
          </div>
        </section>

        <section className="panel activity-panel">
          <PanelHeader title="Activity stream" />
          <ActivityStream indexes={indexes} />
        </section>
      </section>
    </section>
  );
}

function DashboardMetric({ label, value, values }) {
  return (
    <div className="dashboard-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <Sparkline values={values} />
    </div>
  );
}

function Sparkline({ values }) {
  const width = 140;
  const height = 34;
  const clean = values.map((value) => Number(value || 0));
  const max = Math.max(1, ...clean);
  const points = clean.length
    ? clean.map((value, index) => {
        const x = clean.length === 1 ? width : (index / (clean.length - 1)) * width;
        const y = height - (value / max) * (height - 4) - 2;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ")
    : "";
  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} role="img">
      <polyline points={points || `0,${height - 2} ${width},${height - 2}`} />
    </svg>
  );
}

function sortValue(row, key) {
  if (key === "started_at") return parseTime(row.started_at);
  if (key === "ticket_id") return row.ticket_id || "";
  return row[key] ?? "";
}

function SortHeader({ label, column, sortKey, direction, onSort }) {
  const active = sortKey === column;
  return (
    <th>
      <button type="button" onClick={() => onSort(column)}>
        {label}{active ? (direction === "asc" ? " ^" : " v") : ""}
      </button>
    </th>
  );
}

function StatusBadge({ status }) {
  const value = status || "unknown";
  const tone = value === "completed" ? "green" : value === "failed" ? "red" : value === "partial" ? "yellow" : "";
  return <Badge tone={tone}>{value}</Badge>;
}

function formatDate(value) {
  const time = parseTime(value);
  if (!time) return "-";
  return new Date(time).toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function RadarChart({ indexes }) {
  const totals = RADAR_CATEGORIES.map(([key]) =>
    indexes.reduce((total, item) => total + Number(item.category_distribution?.[key] || 0), 0),
  );
  const max = Math.max(1, ...totals);
  const center = 120;
  const radius = 82;
  const points = totals.map((value, index) => {
    const angle = -Math.PI / 2 + (index / totals.length) * Math.PI * 2;
    const scaled = (value / max) * radius;
    return {
      x: center + Math.cos(angle) * scaled,
      y: center + Math.sin(angle) * scaled,
      labelX: center + Math.cos(angle) * (radius + 30),
      labelY: center + Math.sin(angle) * (radius + 30),
      label: RADAR_CATEGORIES[index][1],
      value,
    };
  });
  const polygon = points.map((point) => `${point.x},${point.y}`).join(" ");

  return (
    <div className="radar-wrap">
      <svg className="radar-chart" viewBox="0 0 240 240" role="img">
        {[0.33, 0.66, 1].map((scale) => (
          <polygon
            className="radar-grid"
            key={scale}
            points={RADAR_CATEGORIES.map((_, index) => {
              const angle = -Math.PI / 2 + (index / RADAR_CATEGORIES.length) * Math.PI * 2;
              return `${center + Math.cos(angle) * radius * scale},${center + Math.sin(angle) * radius * scale}`;
            }).join(" ")}
          />
        ))}
        {points.map((point, index) => (
          <line className="radar-axis" key={RADAR_CATEGORIES[index][0]} x1={center} y1={center} x2={point.labelX} y2={point.labelY} />
        ))}
        <polygon className="radar-shape" points={polygon} />
        {points.map((point) => (
          <text className="radar-label" key={point.label} x={point.labelX} y={point.labelY}>
            {point.label}
          </text>
        ))}
      </svg>
      <div className="radar-legend">
        {RADAR_CATEGORIES.map(([key, label], index) => (
          <span key={key}>{label}: {totals[index]}</span>
        ))}
      </div>
    </div>
  );
}

function ActivityStream({ indexes }) {
  const events = indexes
    .slice()
    .sort((a, b) => parseTime(b.started_at) - parseTime(a.started_at))
    .slice(0, 8)
    .map((item) => {
      const hasError = item.tool_error_count > 0 || item.status === "failed";
      const hasSideEffect = item.side_effect_count > 0;
      return {
        runId: item.run_id,
        tone: hasError ? "red" : hasSideEffect ? "yellow" : "green",
        text: `${item.run_id} ${item.status || "completed"} on ${item.ticket_id || "unknown ticket"}`,
        detail: `${item.steps} steps - ${item.side_effect_count} side effects - ${formatMs(item.duration_ms || item.total_latency_ms)}`,
      };
    });
  return (
    <ol className="activity-list">
      {events.map((event) => (
        <li key={event.runId}>
          <Badge tone={event.tone}>{event.tone === "red" ? "alert" : event.tone === "yellow" ? "side effect" : "clean"}</Badge>
          <span><strong>{event.text}</strong>{event.detail}</span>
        </li>
      ))}
      {!events.length ? <li className="empty">No trace activity loaded.</li> : null}
    </ol>
  );
}

function SankeyPanel({ records, runCount, onSelectRun }) {
  const sankey = useMemo(() => buildSankey(records), [records]);
  const [zoom, setZoom] = useState(1);
  const margin = 64;
  const nodeWidth = 30;
  const columnGap = 150;
  const width = Math.max(1680, margin * 2 + (sankey.maxDepth + 1) * columnGap + 280);
  const columns = new Map();

  sankey.nodes.forEach((node) => {
    const list = columns.get(node.depth) || [];
    list.push(node);
    columns.set(node.depth, list);
  });

  const positioned = new Map();
  const maxColumnRows = Math.max(1, ...Array.from(columns.values()).map((column) => column.length));
  const height = Math.max(760, margin * 2 + maxColumnRows * 92);
  columns.forEach((column, depth) => {
    const total = column.reduce((sum, node) => sum + node.value, 0) || 1;
    const gap = 34;
    const usableHeight = height - margin * 2 - gap * Math.max(0, column.length - 1);
    let y = margin;
    column
      .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
      .forEach((node) => {
        const nodeHeight = Math.max(52, (node.value / total) * usableHeight);
        const x = margin + depth * columnGap;
        positioned.set(node.id, { ...node, x, y, width: nodeWidth, height: nodeHeight });
        y += nodeHeight + gap;
      });
  });

  const maxLink = Math.max(1, ...sankey.links.map((link) => link.value));

  return (
    <section className="panel viz-panel">
      <header className="panel-header">
        <h2>Aggregate Sankey flow</h2>
        <div className="zoom-controls" aria-label="Sankey zoom controls">
          <button type="button" onClick={() => setZoom((value) => Math.max(0.6, Number((value - 0.15).toFixed(2))))}>
            -
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button type="button" onClick={() => setZoom((value) => Math.min(1.8, Number((value + 0.15).toFixed(2))))}>
            +
          </button>
          <button type="button" onClick={() => setZoom(1)}>
            Reset
          </button>
        </div>
      </header>
      <p className="viz-note">
        Fuses {records.length ? records.length : 0} loaded traces{runCount > 20 ? " (first 20 shown)" : ""} by step order and behavior.
      </p>
      {records.length ? (
        <div className="sankey-scroll" role="region" aria-label="Scrollable aggregate flow">
          <svg
            className="sankey-viz"
            width={width * zoom}
            height={height * zoom}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
          >
            <g className="sankey-links">
              {sankey.links.map((link) => {
                const source = positioned.get(link.source);
                const target = positioned.get(link.target);
                if (!source || !target) return null;
                const sourceX = source.x + nodeWidth;
                const sourceY = source.y + source.height / 2;
                const targetX = target.x;
                const targetY = target.y + target.height / 2;
                const curve = Math.max(64, (targetX - sourceX) / 2);
                return (
                  <path
                    key={`${link.source}-${link.target}`}
                    d={`M ${sourceX} ${sourceY} C ${sourceX + curve} ${sourceY}, ${targetX - curve} ${targetY}, ${targetX} ${targetY}`}
                    strokeWidth={Math.max(1.5, (link.value / maxLink) * 9)}
                  >
                    <title>{`${source.label} -> ${target.label}: ${link.value} trace${link.value === 1 ? "" : "s"}`}</title>
                  </path>
                );
              })}
            </g>
            <g className="sankey-nodes">
              {Array.from(positioned.values()).map((node) => (
                <g
                  className="clickable-node"
                  key={node.id}
                  transform={`translate(${node.x}, ${node.y})`}
                  onClick={() => node.runIds?.[0] && onSelectRun?.(node.runIds[0])}
                >
                  <rect className={`viz-node ${node.kind}`} width={nodeWidth} height={node.height} rx="7" />
                  <text
                    className="node-label"
                    x={node.x < width - 360 ? 44 : -14}
                    y={Math.max(18, node.height / 2 - 8)}
                    textAnchor={node.x < width - 360 ? "start" : "end"}
                  >
                    {shortLabel(node.label, 34)}
                  </text>
                  <text
                    className="node-count"
                    x={node.x < width - 360 ? 44 : -14}
                    y={Math.max(38, node.height / 2 + 13)}
                    textAnchor={node.x < width - 360 ? "start" : "end"}
                  >
                    {node.value} visit{node.value === 1 ? "" : "s"}
                  </text>
                  <title>{`${node.label}: ${node.value} visit${node.value === 1 ? "" : "s"}`}</title>
                </g>
              ))}
            </g>
          </svg>
        </div>
      ) : (
        <p className="empty">Load traces to render aggregate flow.</p>
      )}
    </section>
  );
}

function ReplayControls({ record, replay, selectedStepId, sandboxFork, onSelectStep, onStep, onReplay, onFork }) {
  const [playing, setPlaying] = useState(false);
  const steps = record?.steps || [];
  const currentIndex = Math.max(0, steps.findIndex((step) => step.step_id === selectedStepId));
  const currentStep = steps[currentIndex];
  const unrecordedCount = replay?.replayed_steps?.filter((step) => step.unrecorded_tool_call).length || 0;

  useEffect(() => {
    if (!playing || !steps.length) return undefined;
    const timer = window.setInterval(() => {
      const index = steps.findIndex((step) => step.step_id === selectedStepId);
      if (index >= steps.length - 1) {
        setPlaying(false);
        return;
      }
      onSelectStep(steps[index + 1].step_id);
    }, 900);
    return () => window.clearInterval(timer);
  }, [onSelectStep, playing, selectedStepId, steps]);

  return (
    <section className="panel replay-controls">
      <div>
        <h2>Replay controls</h2>
        <p>
          Step {currentIndex + 1} of {steps.length}{currentStep ? ` - ${currentStep.intent || currentStep.kind}` : ""}
        </p>
      </div>
      <div className="transport">
        <button type="button" onClick={() => onStep(-1)} disabled={currentIndex <= 0}>Prev</button>
        <button type="button" onClick={() => setPlaying((value) => !value)} disabled={!steps.length}>
          {playing ? "Pause" : "Play"}
        </button>
        <button type="button" onClick={() => onStep(1)} disabled={currentIndex >= steps.length - 1}>Next</button>
        <button type="button" onClick={onReplay} disabled={!record}>Replay</button>
        <button type="button" onClick={onFork} disabled={!record || selectedStepId === null}>Fork sandbox</button>
      </div>
      <div className="replay-mode">
        <Badge tone={replay?.mode === "sandbox_overlay" ? "yellow" : "green"}>
          {replay?.mode === "sandbox_overlay" ? "sandbox overlay" : "record replay"}
        </Badge>
        {sandboxFork ? <span>Fork armed at step #{sandboxFork.stepId}</span> : null}
        {replay?.sandbox_writes?.length ? <span>{replay.sandbox_writes.length} captured write(s)</span> : null}
        {unrecordedCount ? <span>{unrecordedCount} divergent tool prompt(s)</span> : null}
      </div>
    </section>
  );
}

function ThinkingViewer({ steps, selectedStepId, onSelectStep }) {
  const reasoningSteps = steps.filter(
    (step) => step.intent || step.observation || step.inference || step.tool?.side_effecting,
  );
  return (
    <section className="panel thinking-panel">
      <PanelHeader title="Thinking viewer" />
      <ol className="thinking-list">
        {reasoningSteps.map((step) => (
          <li key={step.step_id} className={selectedStepId === step.step_id ? "active" : ""}>
            <button type="button" onClick={() => onSelectStep(step.step_id)}>
              <strong>Step {step.step_id} - {step.intent || step.kind}</strong>
              {step.observation ? <span>Observation: {step.observation}</span> : null}
              {step.inference ? <span>Inference: {step.inference}</span> : null}
              {step.tool?.side_effecting ? <Badge tone="red">side effect: {step.tool.tool_name}</Badge> : null}
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TraceGraphPanel({ record, replay, selectedStepId, onSelectStep }) {
  const steps = record.steps || [];
  const timings = steps.map((step) => stepTiming(steps, step));
  const totalLlm = timings.reduce((total, timing) => total + timing.llmMs, 0);
  const totalTool = timings.reduce((total, timing) => total + timing.toolMs, 0);
  const totalIdle = timings.reduce((total, timing) => total + timing.idleMs, 0);
  const hasTimestampGap = timings.some((timing) => timing.timestampGapMs > 0);
  const maxStepTotal = Math.max(
    1,
    ...timings.map((timing) => timing.llmMs + timing.toolMs + timing.idleMs),
  );
  const width = 1280;
  const height = Math.max(260, 124 + steps.length * 108);
  const centerX = 360;
  const replayX = 860;
  const startY = 58;
  const stepGap = 108;
  const recordedNodeWidth = 320;
  const recordedNodeHeight = 72;
  const replayNodeWidth = 292;
  const replayNodeHeight = 58;
  const replayByStep = new Map((replay?.replayed_steps || []).map((step) => [step.step_id, step]));

  return (
    <section className="panel viz-panel">
      <PanelHeader title="Trajectory graph" />
      <div className="trace-summary">
        <Metric label="LLM time" value={formatMs(totalLlm)} />
        <Metric label="Tool time" value={formatMs(totalTool)} />
        <Metric label="Idle time" value={formatMs(totalIdle)} />
        <Metric label="Selected" value={selectedStepId ? `#${selectedStepId}` : "None"} />
      </div>
      <div className="timeline-heading">
        <p className="viz-note">
          {hasTimestampGap
            ? "Horizontal swimlane shows LLM think-time, tool execution, and idle time for each step."
            : "Idle time is unavailable for this trace because timestamps are missing or too coarse."}
        </p>
        <div className="timeline-legend">
          <span><i className="llm" />LLM</span>
          <span><i className="tool" />Tool</span>
          <span><i className="side-effect" />Side effect</span>
          <span><i className="idle" />Idle</span>
        </div>
      </div>
      <div className="step-timeline" role="list" aria-label="Step latency timeline">
        {steps.map((step, index) => {
          const timing = timings[index];
          const selected = selectedStepId === step.step_id;
          const stepTotal = timing.llmMs + timing.toolMs + timing.idleMs;
          const label = step.tool?.tool_name || step.intent || step.kind;
          return (
            <button
              className={`timeline-card ${selected ? "selected" : ""} ${step.tool?.error ? "error" : ""}`}
              key={step.step_id}
              onClick={() => onSelectStep(step.step_id)}
              role="listitem"
              type="button"
            >
              <strong>#{step.step_id} {shortLabel(step.intent || step.kind, 36)}</strong>
              <span>{shortLabel(label, 36)}</span>
              <span className="latency-bar" title={timingLabel(timing)}>
                {timing.llmMs ? (
                  <i className="llm" style={{ width: `${Math.max(3, (timing.llmMs / maxStepTotal) * 100)}%` }} />
                ) : null}
                {timing.toolMs ? (
                  <i
                    className={step.tool?.side_effecting ? "side-effect" : "tool"}
                    style={{ width: `${Math.max(3, (timing.toolMs / maxStepTotal) * 100)}%` }}
                  />
                ) : null}
                {timing.idleMs ? (
                  <i className="idle" style={{ width: `${Math.max(3, (timing.idleMs / maxStepTotal) * 100)}%` }} />
                ) : null}
                {!stepTotal ? <i className="empty-latency" /> : null}
              </span>
              <span className="timeline-meta">{timingLabel(timing)}</span>
            </button>
          );
        })}
      </div>
      <p className="viz-note">Solid path is the recorded trajectory. Dashed branch shows replay status when debug replay has run.</p>
      <svg className="trace-viz" viewBox={`0 0 ${width} ${height}`} role="img">
        <defs>
          <marker id="trace-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" />
          </marker>
        </defs>
        <text className="lane-label" x={centerX} y="18">Recorded</text>
        <text className="lane-label" x={replayX} y="18">Replay</text>
        <circle className="start-node" cx={centerX} cy={startY} r="18" />
        <text className="start-label" x={centerX} y={startY + 4}>START</text>
        {steps.map((step, index) => {
          const y = startY + (index + 1) * stepGap;
          const previousY = index === 0 ? startY + 20 : startY + index * stepGap + recordedNodeHeight / 2;
          const replayStep = replayByStep.get(step.step_id);
          const selected = selectedStepId === step.step_id;
          const label = shortLabel(step.tool?.tool_name || step.intent || step.kind, 42);
          return (
            <g key={step.step_id}>
              <path className="trace-edge" d={`M ${centerX} ${previousY} L ${centerX} ${y - recordedNodeHeight / 2 - 8}`} />
              <g
                className={`trace-node ${selected ? "selected" : ""} ${step.kind}`}
                transform={`translate(${centerX - recordedNodeWidth / 2}, ${y - recordedNodeHeight / 2})`}
                onClick={() => onSelectStep(step.step_id)}
              >
                <rect width={recordedNodeWidth} height={recordedNodeHeight} rx="12" />
                <text className="trace-kind" x="18" y="23">{step.kind.toUpperCase()}</text>
                <text className="trace-label" x="18" y="49">{label}</text>
                <title>{`${step.intent || stepLabel(step)} - ${timingLabel(stepTiming(steps, step))}`}</title>
              </g>
              {replayStep ? (
                <>
                  <path className="replay-edge" d={`M ${centerX + recordedNodeWidth / 2 + 8} ${y} C ${centerX + 250} ${y}, ${replayX - 250} ${y}, ${replayX - replayNodeWidth / 2 - 8} ${y}`} />
                  <g className={`replay-node ${replayStep.side_effect_blocked ? "blocked" : ""}`} transform={`translate(${replayX - replayNodeWidth / 2}, ${y - replayNodeHeight / 2})`}>
                    <rect width={replayNodeWidth} height={replayNodeHeight} rx="12" />
                    <text x="16" y="23">{replayStep.side_effect_blocked ? "Blocked safely" : replayStep.mocked ? "Mocked response" : "Replayed"}</text>
                    <text className="trace-label" x="16" y="43">{replayStep.input_matches_original === false ? "Diverged input" : "Matched original input"}</text>
                  </g>
                </>
              ) : null}
            </g>
          );
        })}
      </svg>
    </section>
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

function StepInspector({
  step,
  previousStep,
  steps,
  replayStep,
  edit,
  replayMode,
  sandboxWrites,
  onPromptEdit,
  onToolParameterEdit,
  onToolResultEdit,
}) {
  const effectiveToolResult = edit?.tool_result ?? replayStep?.output ?? step.tool?.result ?? {};
  const [prompt, setPrompt] = useState(edit?.prompt || step.llm?.prompt || "");
  const [toolParameters, setToolParameters] = useState(
    JSON.stringify(edit?.tool_parameters ?? step.tool?.parameters ?? {}, null, 2),
  );
  const [toolResult, setToolResult] = useState(
    JSON.stringify(effectiveToolResult, null, 2),
  );
  const [unrecordedChoice, setUnrecordedChoice] = useState("");
  const timing = stepTiming(steps, step);
  const diffLines = diffContexts(previousStep?.context || {}, step.context || {});
  const sandboxWrite = sandboxWrites.find((write) => write.step_id === step.step_id);
  const unrecordedCall = replayStep?.unrecorded_tool_call;

  useEffect(() => {
    setPrompt(edit?.prompt || step.llm?.prompt || "");
    setToolParameters(JSON.stringify(edit?.tool_parameters ?? step.tool?.parameters ?? {}, null, 2));
    setToolResult(JSON.stringify(effectiveToolResult, null, 2));
    setUnrecordedChoice("");
  }, [step.step_id, edit, step.llm?.prompt, step.tool?.parameters, effectiveToolResult]);

  return (
    <div className="inspector-body">
      <section>
        <h3>Step {step.step_id}</h3>
        <p>{step.intent}</p>
        <div className="badges">
          <Badge>{step.kind}</Badge>
          <Badge>LLM {formatMs(timing.llmMs)}</Badge>
          <Badge>Tool {formatMs(timing.toolMs)}</Badge>
          <Badge>Idle {formatMs(timing.idleMs)}</Badge>
          {step.tool?.side_effecting ? <Badge tone="red">side effect</Badge> : null}
          {step.tool?.error ? <Badge tone="red">error</Badge> : null}
          {replayStep?.side_effect_blocked ? <Badge tone="yellow">blocked in replay</Badge> : null}
          {replayStep?.input_matches_original === false ? <Badge tone="green">diverged</Badge> : null}
          {replayStep?.sandboxed ? <Badge tone="yellow">sandboxed</Badge> : null}
        </div>
        {step.observation ? <p><strong>Observation:</strong> {step.observation}</p> : null}
        {step.inference ? <p><strong>Inference:</strong> {step.inference}</p> : null}
        {replayMode === "sandbox_overlay" ? (
          <p><strong>Replay mode:</strong> sandbox overlay captures Jira and external writes without mutating live systems.</p>
        ) : null}
      </section>

      {step.llm ? (
        <section>
          <details>
            <summary>System message</summary>
            <pre>{step.llm.system_message || "No system message recorded."}</pre>
          </details>
          <h3>Prompt editor</h3>
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} />
          <button onClick={() => onPromptEdit(step, prompt)}>Rewrite prompt and continue</button>
          <details open>
            <summary>Recorded response</summary>
            <pre>{step.llm.response}</pre>
          </details>
        </section>
      ) : null}

      {step.tool ? (
        <section>
          <details open>
            <summary>Tool parameters</summary>
            <pre>{stableStringify(step.tool.parameters)}</pre>
          </details>
          <details>
            <summary>Recorded result</summary>
            <pre>{stableStringify(step.tool.result)}</pre>
          </details>
          <h3>Tool parameters editor</h3>
          <textarea value={toolParameters} onChange={(event) => setToolParameters(event.target.value)} />
          <button onClick={() => onToolParameterEdit(step, toolParameters)}>
            Change parameters and continue
          </button>
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
          <pre>{stableStringify(replayStep.output)}</pre>
        </section>
      ) : null}

      {sandboxWrite ? (
        <section>
          <h3>Sandbox overlay write</h3>
          <pre>{stableStringify(sandboxWrite)}</pre>
        </section>
      ) : null}

      {unrecordedCall ? (
        <UnrecordedToolCallPrompt
          call={unrecordedCall}
          choice={unrecordedChoice}
          onChoose={setUnrecordedChoice}
        />
      ) : null}

      <section>
        <h3>
          Context diff: {previousStep ? `Step ${previousStep.step_id} -> Step ${step.step_id}` : `Start -> Step ${step.step_id}`}
        </h3>
        {diffLines.length ? (
          <div className="diff-view">
            {diffLines.map((line, index) => (
              <span className={`diff-line ${line.type}`} key={`${line.type}-${index}`}>
                {line.text}
              </span>
            ))}
          </div>
        ) : (
          <p className="empty inline-empty">No context changes recorded for this step.</p>
        )}
      </section>
    </div>
  );
}

function UnrecordedToolCallPrompt({ call, choice, onChoose }) {
  return (
    <section className="unrecorded-call">
      <h3>Unrecorded tool call</h3>
      <p>{call.tool_name} has divergent parameters in this replay fork.</p>
      <pre>{stableStringify(call.parameters)}</pre>
      <div className="choice-row">
        <button type="button" onClick={() => onChoose("manual_response")}>Provide manual response</button>
        <button type="button" onClick={() => onChoose("execute_live_unsafe")}>Execute live unsafe</button>
        <button type="button" onClick={() => onChoose("abort_replay")}>Abort replay</button>
      </div>
      {choice ? (
        <p className="choice-note">
          {choice === "manual_response"
            ? "Manual response selected. Use Injected result above to continue the fork."
            : choice === "execute_live_unsafe"
              ? "Live execution is intentionally not automatic from the replay panel."
              : "Abort selected. Run replay again without this fork edit to return to the recorded path."}
        </p>
      ) : null}
    </section>
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
