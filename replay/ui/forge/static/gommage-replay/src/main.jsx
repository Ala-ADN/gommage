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

function buildSankey(records) {
  const nodes = new Map();
  const links = new Map();
  let maxDepth = 1;

  function touchNode(id, label, depth, kind) {
    const existing = nodes.get(id) || { id, label, depth, kind, value: 0 };
    existing.value += 1;
    nodes.set(id, existing);
    maxDepth = Math.max(maxDepth, depth);
    return existing;
  }

  records.forEach((record) => {
    if (!record?.steps?.length) return;
    touchNode("0:start", "START", 0, "start");
    let previous = "0:start";

    record.steps.forEach((step, index) => {
      const depth = index + 1;
      const id = flowNodeKey(step, depth);
      const label = step.tool?.tool_name || step.intent || step.kind;
      touchNode(id, label, depth, step.kind);
      const linkKey = `${previous}->${id}`;
      links.set(linkKey, {
        source: previous,
        target: id,
        value: (links.get(linkKey)?.value || 0) + 1,
      });
      previous = id;
    });

    const endId = `${record.steps.length + 1}:end`;
    touchNode(endId, "END", record.steps.length + 1, "end");
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
  const replayStep = selectedStep
    ? replay?.replayed_steps?.find((step) => step.step_id === selectedStep.step_id)
    : null;

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <h1>Gommage Replay</h1>
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
        <SankeyPanel records={aggregateRecords} runCount={runs.length} />
      ) : record ? (
        <TraceGraphPanel
          record={record}
          replay={replay}
          selectedStepId={selectedStepId}
          onSelectStep={setSelectedStepId}
        />
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

function SankeyPanel({ records, runCount }) {
  const sankey = useMemo(() => buildSankey(records), [records]);
  const width = 920;
  const height = 310;
  const margin = 28;
  const nodeWidth = 14;
  const columns = new Map();

  sankey.nodes.forEach((node) => {
    const list = columns.get(node.depth) || [];
    list.push(node);
    columns.set(node.depth, list);
  });

  const positioned = new Map();
  columns.forEach((column, depth) => {
    const total = column.reduce((sum, node) => sum + node.value, 0) || 1;
    const gap = 12;
    const usableHeight = height - margin * 2 - gap * Math.max(0, column.length - 1);
    let y = margin;
    column
      .sort((a, b) => b.value - a.value || a.label.localeCompare(b.label))
      .forEach((node) => {
        const nodeHeight = Math.max(20, (node.value / total) * usableHeight);
        const x =
          margin + (depth / Math.max(1, sankey.maxDepth)) * (width - margin * 2 - nodeWidth);
        positioned.set(node.id, { ...node, x, y, height: nodeHeight });
        y += nodeHeight + gap;
      });
  });

  const maxLink = Math.max(1, ...sankey.links.map((link) => link.value));

  return (
    <section className="panel viz-panel">
      <PanelHeader title="Aggregate Sankey flow" />
      <p className="viz-note">
        Fuses {records.length ? records.length : 0} loaded traces{runCount > 20 ? " (first 20 shown)" : ""} by step order and behavior.
      </p>
      {records.length ? (
        <svg className="sankey-viz" viewBox={`0 0 ${width} ${height}`} role="img">
          <g className="sankey-links">
            {sankey.links.map((link) => {
              const source = positioned.get(link.source);
              const target = positioned.get(link.target);
              if (!source || !target) return null;
              const sourceX = source.x + nodeWidth;
              const sourceY = source.y + source.height / 2;
              const targetX = target.x;
              const targetY = target.y + target.height / 2;
              const curve = Math.max(40, (targetX - sourceX) / 2);
              return (
                <path
                  key={`${link.source}-${link.target}`}
                  d={`M ${sourceX} ${sourceY} C ${sourceX + curve} ${sourceY}, ${targetX - curve} ${targetY}, ${targetX} ${targetY}`}
                  strokeWidth={Math.max(2, (link.value / maxLink) * 18)}
                >
                  <title>{`${source.label} -> ${target.label}: ${link.value} trace${link.value === 1 ? "" : "s"}`}</title>
                </path>
              );
            })}
          </g>
          <g className="sankey-nodes">
            {Array.from(positioned.values()).map((node) => (
              <g key={node.id} transform={`translate(${node.x}, ${node.y})`}>
                <rect className={`viz-node ${node.kind}`} width={nodeWidth} height={node.height} rx="5" />
                <text x={node.x < width / 2 ? 22 : -8} y={node.height / 2} textAnchor={node.x < width / 2 ? "start" : "end"}>
                  {shortLabel(node.label)}
                </text>
                <title>{`${node.label}: ${node.value} visit${node.value === 1 ? "" : "s"}`}</title>
              </g>
            ))}
          </g>
        </svg>
      ) : (
        <p className="empty">Load traces to render aggregate flow.</p>
      )}
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
  const width = 980;
  const height = Math.max(180, 92 + steps.length * 82);
  const centerX = 230;
  const replayX = 590;
  const startY = 44;
  const stepGap = 82;
  const replayByStep = new Map((replay?.replayed_steps || []).map((step) => [step.step_id, step]));

  return (
    <section className="panel viz-panel">
      <PanelHeader title="Trace inspector" />
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
              <strong>#{step.step_id} {shortLabel(step.intent || step.kind, 28)}</strong>
              <span>{shortLabel(label, 28)}</span>
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
          const previousY = index === 0 ? startY + 18 : startY + index * stepGap + 28;
          const replayStep = replayByStep.get(step.step_id);
          const selected = selectedStepId === step.step_id;
          const label = shortLabel(step.tool?.tool_name || step.intent || step.kind, 30);
          return (
            <g key={step.step_id}>
              <path className="trace-edge" d={`M ${centerX} ${previousY} L ${centerX} ${y - 30}`} />
              <g
                className={`trace-node ${selected ? "selected" : ""} ${step.kind}`}
                transform={`translate(${centerX - 110}, ${y - 28})`}
                onClick={() => onSelectStep(step.step_id)}
              >
                <rect width="220" height="56" rx="12" />
                <text className="trace-kind" x="14" y="18">{step.kind.toUpperCase()}</text>
                <text className="trace-label" x="14" y="39">{label}</text>
                <title>{`${step.intent || stepLabel(step)} - ${timingLabel(stepTiming(steps, step))}`}</title>
              </g>
              {replayStep ? (
                <>
                  <path className="replay-edge" d={`M ${centerX + 116} ${y} C ${centerX + 210} ${y}, ${replayX - 190} ${y}, ${replayX - 104} ${y}`} />
                  <g className={`replay-node ${replayStep.side_effect_blocked ? "blocked" : ""}`} transform={`translate(${replayX - 104}, ${y - 22})`}>
                    <rect width="208" height="44" rx="12" />
                    <text x="14" y="18">{replayStep.side_effect_blocked ? "Blocked safely" : replayStep.mocked ? "Mocked response" : "Replayed"}</text>
                    <text className="trace-label" x="14" y="34">{replayStep.input_matches_original === false ? "Diverged input" : "Matched original input"}</text>
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

function StepInspector({ step, previousStep, steps, replayStep, edit, onPromptEdit, onToolResultEdit }) {
  const [prompt, setPrompt] = useState(edit?.prompt || step.llm?.prompt || "");
  const [toolResult, setToolResult] = useState(
    JSON.stringify(edit?.tool_result ?? step.tool?.result ?? {}, null, 2),
  );
  const timing = stepTiming(steps, step);
  const diffLines = diffContexts(previousStep?.context || {}, step.context || {});

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
          <Badge>LLM {formatMs(timing.llmMs)}</Badge>
          <Badge>Tool {formatMs(timing.toolMs)}</Badge>
          <Badge>Idle {formatMs(timing.idleMs)}</Badge>
          {step.tool?.side_effecting ? <Badge tone="red">side effect</Badge> : null}
          {step.tool?.error ? <Badge tone="red">error</Badge> : null}
          {replayStep?.side_effect_blocked ? <Badge tone="yellow">blocked in replay</Badge> : null}
          {replayStep?.input_matches_original === false ? <Badge tone="green">diverged</Badge> : null}
        </div>
        {step.observation ? <p><strong>Observation:</strong> {step.observation}</p> : null}
        {step.inference ? <p><strong>Inference:</strong> {step.inference}</p> : null}
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
