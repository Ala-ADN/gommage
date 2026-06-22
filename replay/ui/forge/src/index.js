const Resolver = require("@forge/resolver");
const api = require("@forge/api");
const { route } = require("@forge/api");

const resolver = new Resolver();

function backendBaseUrl() {
  const value = process.env.GOMMAGE_BACKEND_URL;
  if (!value) {
    throw new Error("GOMMAGE_BACKEND_URL is not configured. Set it with `forge variables set GOMMAGE_BACKEND_URL https://...`.");
  }
  return value.replace(/\/$/, "");
}

async function backendFetch(path, options = {}) {
  const response = await fetch(`${backendBaseUrl()}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(body.message || body.error || `Gommage backend returned ${response.status}`);
  }
  return body;
}

function issueKeyFromRequest(payload, context) {
  return (
    payload.issueKey ||
    context.extension?.issue?.key ||
    context.extension?.issueKey ||
    context.extension?.platformContext?.issueKey ||
    context.extension?.platformContext?.issue?.key
  );
}

function projectKeyFromRequest(payload, context, issueKey) {
  return (
    payload.projectKey ||
    context.extension?.project?.key ||
    context.extension?.projectKey ||
    context.extension?.platformContext?.projectKey ||
    issueKey?.split("-").slice(0, -1).join("-")
  );
}

function adfDoc(paragraphs) {
  return {
    type: "doc",
    version: 1,
    content: paragraphs.map((text) => ({
      type: "paragraph",
      content: [{ type: "text", text }],
    })),
  };
}

function summarizeTrace(record, replayMetrics) {
  const sideEffecting = record.steps
    .filter((step) => step.tool?.side_effecting)
    .map((step) => `${step.step_id}: ${step.tool.tool_name}`);
  return [
    `Gommage trace: ${record.run_id}`,
    `Origin ticket: ${record.jira_ticket_id}`,
    `Agent: ${record.agent_name}`,
    `Steps: ${record.steps.length}`,
    `Side-effecting tool calls: ${sideEffecting.length ? sideEffecting.join(", ") : "none"}`,
    replayMetrics
      ? `Replay: RFS=${replayMetrics.replay_fidelity}, MRR=${replayMetrics.mock_recall}, blocked=${replayMetrics.side_effects_blocked}`
      : "Replay: not run yet",
  ];
}

async function attachTrace(issueKey, record) {
  const form = new FormData();
  const blob = new Blob([JSON.stringify(record, null, 2)], {
    type: "application/json",
  });
  form.append("file", blob, `${record.run_id}.gommage-aer.json`);

  const response = await api.asApp().requestJira(
    route`/rest/api/3/issue/${issueKey}/attachments`,
    {
      method: "POST",
      headers: {
        "X-Atlassian-Token": "no-check",
      },
      body: form,
    },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Failed to attach trace to ${issueKey}: ${response.status} ${text}`);
  }
  return response.json();
}

async function addIssueComment(issueKey, paragraphs) {
  const response = await api.asApp().requestJira(
    route`/rest/api/3/issue/${issueKey}/comment`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body: adfDoc(paragraphs) }),
    },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Failed to comment on ${issueKey}: ${response.status} ${text}`);
  }
  return response.json();
}

async function createLinkedFixIssue({ issueKey, projectKey, record, replayMetrics, summary, issueType }) {
  const response = await api.asApp().requestJira(route`/rest/api/3/issue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fields: {
        project: { key: projectKey },
        issuetype: { name: issueType || "Task" },
        summary: summary || `Fix agent prompt for ${issueKey}`,
        description: adfDoc([
          "Created from Gommage Replay.",
          ...summarizeTrace(record, replayMetrics),
        ]),
      },
    }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Failed to create Jira fix issue: ${response.status} ${text}`);
  }
  const created = await response.json();

  const linkResponse = await api.asApp().requestJira(route`/rest/api/3/issueLink`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: { name: "Relates" },
      inwardIssue: { key: issueKey },
      outwardIssue: { key: created.key },
    }),
  });
  const linkWarning = linkResponse.ok ? null : await linkResponse.text();

  await addIssueComment(created.key, summarizeTrace(record, replayMetrics));
  await attachTrace(created.key, record);

  return {
    key: created.key,
    id: created.id,
    self: created.self,
    linkWarning,
  };
}

resolver.define("getIssueContext", ({ payload, context }) => {
  const issueKey = issueKeyFromRequest(payload, context);
  const projectKey = projectKeyFromRequest(payload, context, issueKey);
  return { issueKey, projectKey, context: context.extension };
});

resolver.define("listRuns", async ({ payload, context }) => {
  const issueKey = issueKeyFromRequest(payload, context);
  return backendFetch(`/api/runs?ticket_id=${encodeURIComponent(issueKey)}`);
});

resolver.define("recordRun", async ({ payload, context }) => {
  const issueKey = issueKeyFromRequest(payload, context);
  const data = await backendFetch("/api/record", {
    method: "POST",
    body: JSON.stringify({ ticket_id: issueKey }),
  });
  const warnings = [];
  try {
    await attachTrace(issueKey, data.record);
    await addIssueComment(issueKey, [
      `Gommage recorded trace ${data.record.run_id}.`,
      "Open the Gommage Replay panel to replay in debug mode.",
    ]);
  } catch (error) {
    warnings.push(error.message);
  }
  return { ...data, warnings };
});

resolver.define("getRun", async ({ payload }) => {
  return backendFetch(`/api/runs/${encodeURIComponent(payload.runId)}`);
});

resolver.define("replayRun", async ({ payload }) => {
  return backendFetch("/api/replay", {
    method: "POST",
    body: JSON.stringify({
      run_id: payload.runId,
      edits: payload.edits || [],
    }),
  });
});

resolver.define("createFixIssue", async ({ payload, context }) => {
  const issueKey = issueKeyFromRequest(payload, context);
  const projectKey = projectKeyFromRequest(payload, context, issueKey);
  const run = await backendFetch(`/api/runs/${encodeURIComponent(payload.runId)}`);
  const replay = payload.replayMetrics
    ? { metrics: payload.replayMetrics }
    : await backendFetch("/api/replay", {
        method: "POST",
        body: JSON.stringify({ run_id: payload.runId, edits: payload.edits || [] }),
      });
  const fixIssue = await createLinkedFixIssue({
    issueKey,
    projectKey,
    record: run.record,
    replayMetrics: replay.metrics,
    summary: payload.summary,
    issueType: payload.issueType,
  });
  return { fixIssue };
});

exports.handler = resolver.getDefinitions();
