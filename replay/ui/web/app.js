const state = {
  runs: [],
  record: null,
  replay: null,
  metrics: null,
  selectedStepId: null,
  edits: new Map(),
};

const els = {
  status: document.querySelector("#status-line"),
  recordForm: document.querySelector("#record-form"),
  ticketInput: document.querySelector("#ticket-input"),
  replayButton: document.querySelector("#replay-button"),
  refreshRuns: document.querySelector("#refresh-runs"),
  clearEdits: document.querySelector("#clear-edits"),
  runList: document.querySelector("#run-list"),
  stepList: document.querySelector("#step-list"),
  detailPanel: document.querySelector("#detail-panel"),
  traceLabel: document.querySelector("#trace-label"),
  metricRun: document.querySelector("#metric-run"),
  metricSteps: document.querySelector("#metric-steps"),
  metricSideEffects: document.querySelector("#metric-side-effects"),
  metricBlocked: document.querySelector("#metric-blocked"),
  metricRfs: document.querySelector("#metric-rfs"),
  metricMrr: document.querySelector("#metric-mrr"),
};

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.className = isError ? "error-text" : "";
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function formatNumber(value) {
  if (value === null || value === undefined) return "-";
  return Number(value).toFixed(2);
}

function renderMetrics() {
  const record = state.record;
  const replayMetrics = state.metrics;
  const sideEffects = record
    ? record.steps.filter((step) => step.tool?.side_effecting).length
    : 0;
  els.metricRun.textContent = record?.run_id ?? "None";
  els.metricSteps.textContent = record?.steps.length ?? "0";
  els.metricSideEffects.textContent = String(sideEffects);
  els.metricBlocked.textContent = String(replayMetrics?.side_effects_blocked ?? 0);
  els.metricRfs.textContent = replayMetrics ? formatNumber(replayMetrics.replay_fidelity) : "-";
  els.metricMrr.textContent = replayMetrics ? formatNumber(replayMetrics.mock_recall) : "-";
  els.replayButton.disabled = !record;
  els.clearEdits.disabled = state.edits.size === 0;
}

function renderRuns() {
  els.runList.replaceChildren();
  if (state.runs.length === 0) {
    els.runList.innerHTML = '<p class="muted">No runs recorded.</p>';
    return;
  }
  for (const run of state.runs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `run-item ${state.record?.run_id === run.run_id ? "active" : ""}`;
    button.innerHTML = `
      <span class="run-title">
        <strong>${escapeHtml(run.run_id)}</strong>
        <span class="badge">${escapeHtml(run.status ?? "unknown")}</span>
      </span>
      <span class="meta">${escapeHtml(run.ticket_id ?? "")}</span>
      <span class="badges">
        <span class="badge">${run.steps ?? 0} steps</span>
        <span class="badge ${run.side_effecting_tools ? "red" : ""}">${run.side_effecting_tools ?? 0} side effects</span>
      </span>
    `;
    button.addEventListener("click", () => loadRun(run.run_id));
    els.runList.append(button);
  }
}

function renderSteps() {
  els.stepList.replaceChildren();
  els.traceLabel.textContent = state.record ? state.record.jira_ticket_id : "";
  if (!state.record) {
    els.stepList.innerHTML = '<p class="muted">No trace loaded.</p>';
    return;
  }
  for (const step of state.record.steps) {
    const replayStep = state.replay?.replayed_steps?.find((item) => item.step_id === step.step_id);
    const active = state.selectedStepId === step.step_id;
    const badges = [];
    badges.push(`<span class="badge">${escapeHtml(step.kind)}</span>`);
    if (step.tool?.side_effecting) badges.push('<span class="badge red">side effect</span>');
    if (replayStep?.mocked) badges.push('<span class="badge yellow">mocked</span>');
    if (state.edits.has(step.step_id)) badges.push('<span class="badge green">edited</span>');
    const button = document.createElement("button");
    button.type = "button";
    button.className = `step-item ${active ? "active" : ""}`;
    button.innerHTML = `
      <span class="step-title">
        <strong>#${step.step_id} ${escapeHtml(step.intent || step.kind)}</strong>
      </span>
      <span class="badges">${badges.join("")}</span>
      <span class="meta">${escapeHtml(toolOrModel(step))}</span>
    `;
    button.addEventListener("click", () => {
      state.selectedStepId = step.step_id;
      render();
    });
    els.stepList.append(button);
  }
}

function toolOrModel(step) {
  if (step.tool) return step.tool.tool_name;
  if (step.llm) return step.llm.model;
  return "";
}

function renderDetail() {
  const step = state.record?.steps.find((item) => item.step_id === state.selectedStepId);
  if (!step) {
    els.detailPanel.className = "detail-panel empty-panel";
    els.detailPanel.textContent = "No step selected";
    return;
  }
  els.detailPanel.className = "detail-panel";
  const replayStep = state.replay?.replayed_steps?.find((item) => item.step_id === step.step_id);
  const header = `
    <section class="detail-block">
      <h3>Step ${step.step_id}</h3>
      <div class="badges">
        <span class="badge">${escapeHtml(step.kind)}</span>
        ${step.tool?.side_effecting ? '<span class="badge red">side effect</span>' : ""}
        ${replayStep?.side_effect_blocked ? '<span class="badge yellow">blocked in replay</span>' : ""}
        ${replayStep?.input_matches_original === false ? '<span class="badge green">diverged</span>' : ""}
      </div>
      <p>${escapeHtml(step.intent || "")}</p>
    </section>
  `;

  if (step.llm) {
    els.detailPanel.innerHTML = `${header}${renderLlmDetail(step, replayStep)}`;
    wirePromptEditor(step);
    return;
  }
  if (step.tool) {
    els.detailPanel.innerHTML = `${header}${renderToolDetail(step, replayStep)}`;
    wireToolEditor(step);
    return;
  }
  els.detailPanel.innerHTML = `${header}<pre>${escapeHtml(JSON.stringify(step, null, 2))}</pre>`;
}

function renderLlmDetail(step, replayStep) {
  const edit = state.edits.get(step.step_id);
  const prompt = edit?.prompt ?? step.llm.prompt;
  return `
    <section class="detail-block">
      <h3>Prompt</h3>
      <textarea id="prompt-editor">${escapeHtml(prompt)}</textarea>
      <div class="editor-actions">
        <button id="apply-prompt" type="button">Apply edit and replay</button>
      </div>
    </section>
    <section class="detail-block">
      <h3>Recorded response</h3>
      <pre>${escapeHtml(step.llm.response)}</pre>
    </section>
    ${replayStep ? renderReplayOutput(replayStep) : ""}
  `;
}

function renderToolDetail(step, replayStep) {
  const edit = state.edits.get(step.step_id);
  const result = edit?.tool_result ?? step.tool.result;
  return `
    <section class="detail-block">
      <h3>Parameters</h3>
      <pre>${escapeHtml(JSON.stringify(step.tool.parameters, null, 2))}</pre>
    </section>
    <section class="detail-block">
      <h3>Recorded result</h3>
      <pre>${escapeHtml(JSON.stringify(step.tool.result, null, 2))}</pre>
    </section>
    <section class="detail-block">
      <h3>Injected result</h3>
      <textarea id="tool-result-editor">${escapeHtml(JSON.stringify(result, null, 2))}</textarea>
      <div class="editor-actions">
        <button id="apply-tool-result" type="button">Apply injection and replay</button>
      </div>
    </section>
    ${replayStep ? renderReplayOutput(replayStep) : ""}
  `;
}

function renderReplayOutput(replayStep) {
  return `
    <section class="detail-block">
      <h3>Replay output</h3>
      <pre>${escapeHtml(JSON.stringify(replayStep.output, null, 2))}</pre>
    </section>
  `;
}

function wirePromptEditor(step) {
  document.querySelector("#apply-prompt").addEventListener("click", async () => {
    const prompt = document.querySelector("#prompt-editor").value;
    state.edits.set(step.step_id, { step_id: step.step_id, prompt, note: "UI prompt edit" });
    await replayCurrentRun();
  });
}

function wireToolEditor(step) {
  document.querySelector("#apply-tool-result").addEventListener("click", async () => {
    const raw = document.querySelector("#tool-result-editor").value;
    let toolResult;
    try {
      toolResult = JSON.parse(raw);
    } catch (error) {
      setStatus(`Invalid JSON: ${error.message}`, true);
      return;
    }
    state.edits.set(step.step_id, {
      step_id: step.step_id,
      tool_result: toolResult,
      note: "UI tool result injection",
    });
    await replayCurrentRun();
  });
}

function render() {
  renderMetrics();
  renderRuns();
  renderSteps();
  renderDetail();
}

async function loadRuns() {
  const data = await requestJson("/api/runs");
  state.runs = data.runs;
  render();
}

async function loadRun(runId) {
  setStatus(`Loading ${runId}`);
  const data = await requestJson(`/api/runs/${encodeURIComponent(runId)}`);
  state.record = data.record;
  state.replay = null;
  state.metrics = null;
  state.edits.clear();
  state.selectedStepId = data.record.steps[0]?.step_id ?? null;
  setStatus(`Loaded ${runId}`);
  render();
}

async function recordRun(ticketId) {
  setStatus(`Recording ${ticketId}`);
  const data = await requestJson("/api/record", {
    method: "POST",
    body: JSON.stringify({ ticket_id: ticketId }),
  });
  state.record = data.record;
  state.replay = null;
  state.metrics = null;
  state.edits.clear();
  state.selectedStepId = data.record.steps[0]?.step_id ?? null;
  await loadRuns();
  setStatus(`Recorded ${data.record.run_id}`);
  render();
}

async function replayCurrentRun() {
  if (!state.record) return;
  setStatus(`Replaying ${state.record.run_id}`);
  const data = await requestJson("/api/replay", {
    method: "POST",
    body: JSON.stringify({
      run_id: state.record.run_id,
      edits: Array.from(state.edits.values()),
    }),
  });
  state.replay = data.result;
  state.metrics = data.metrics;
  setStatus(`Replayed ${state.record.run_id}`);
  render();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

els.recordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await recordRun(els.ticketInput.value.trim() || "DEMO-101");
  } catch (error) {
    setStatus(error.message, true);
  }
});

els.replayButton.addEventListener("click", async () => {
  try {
    await replayCurrentRun();
  } catch (error) {
    setStatus(error.message, true);
  }
});

els.refreshRuns.addEventListener("click", async () => {
  try {
    await loadRuns();
    setStatus("Runs refreshed");
  } catch (error) {
    setStatus(error.message, true);
  }
});

els.clearEdits.addEventListener("click", async () => {
  state.edits.clear();
  try {
    await replayCurrentRun();
  } catch (error) {
    setStatus(error.message, true);
  }
});

loadRuns()
  .then(() => setStatus("Ready"))
  .catch((error) => setStatus(error.message, true));
