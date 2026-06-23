const Resolver = require("@forge/resolver").default;
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

function issueKeyFromRequest(payload = {}, context = {}) {
  return (
    payload.issueKey ||
    payload.ticketId ||
    context.extension?.issue?.key ||
    context.extension?.issueKey ||
    context.extension?.platformContext?.issueKey ||
    context.extension?.platformContext?.issue?.key
  );
}

function projectKeyFromRequest(payload = {}, context = {}, issueKey) {
  return (
    payload.projectKey ||
    payload.boardProjectKey ||
    context.extension?.project?.key ||
    context.extension?.projectKey ||
    context.extension?.platformContext?.projectKey ||
    issueKey?.split("-").slice(0, -1).join("-")
  );
}

function boardFromRequest(payload = {}, context = {}) {
  return payload.board || context.extension?.board || null;
}

function issueContextFromRequest(payload = {}, context = {}) {
  const issueKey = issueKeyFromRequest(payload, context);
  const projectKey = projectKeyFromRequest(payload, context, issueKey);
  const board = boardFromRequest(payload, context);
  return {
    issueKey,
    projectKey,
    boardId: board?.id || null,
    boardType: board?.type || null,
    action: context.extension?.action || payload.action || null,
    location: context.extension?.location || null,
  };
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

function adfToPlainText(node) {
  if (!node) {
    return "";
  }
  if (typeof node === "string") {
    return node;
  }
  if (Array.isArray(node)) {
    return node.map(adfToPlainText).filter(Boolean).join("\n");
  }
  const ownText = node.text || "";
  const childText = node.content ? adfToPlainText(node.content) : "";
  return [ownText, childText].filter(Boolean).join(node.type === "paragraph" ? "\n" : "");
}

function displayUser(user) {
  if (!user) {
    return "";
  }
  return user.emailAddress || user.displayName || user.accountId || "";
}

async function fetchIssueDetails(issueKey) {
  const response = await api.asApp().requestJira(
    route`/rest/api/3/issue/${issueKey}?fields=summary,description,priority,reporter,assignee,labels,status,issuetype`,
    {
      method: "GET",
      headers: { "Accept": "application/json" },
    },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Failed to read Jira issue ${issueKey}: ${response.status} ${text}`);
  }
  const issue = await response.json();
  const fields = issue.fields || {};
  const reporter = displayUser(fields.reporter);
  const assignee = displayUser(fields.assignee);
  return {
    ticket_id: issue.key || issueKey,
    key: issue.key || issueKey,
    id: issue.id,
    summary: fields.summary || "Untitled Jira issue",
    description: adfToPlainText(fields.description),
    priority: fields.priority?.name || "medium",
    reporter,
    assignee,
    owner: assignee || reporter || "support-team@example.com",
    labels: fields.labels || [],
    status: fields.status?.name,
    issue_type: fields.issuetype?.name,
    source: "jira",
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

function fallbackFixBrief(record, replayMetrics, summary) {
  return {
    summary: summary || `Fix agent prompt for ${record.jira_ticket_id}`,
    description_paragraphs: [
      "Created from Gommage Replay.",
      ...summarizeTrace(record, replayMetrics),
      "Recommended change: require explicit ticket and database evidence before any side-effecting notification tool call.",
    ],
    comment_paragraphs: summarizeTrace(record, replayMetrics),
    source: "forge-fallback",
  };
}

function cleanParagraphs(value, fallback) {
  const paragraphs = Array.isArray(value)
    ? value.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  return paragraphs.length ? paragraphs : fallback;
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

async function createLinkedFixIssue({ issueKey, projectKey, record, replayMetrics, summary, issueType, brief }) {
  const fixBrief = brief || fallbackFixBrief(record, replayMetrics, summary);
  const descriptionParagraphs = cleanParagraphs(
    fixBrief.description_paragraphs,
    fallbackFixBrief(record, replayMetrics, summary).description_paragraphs,
  );
  const commentParagraphs = cleanParagraphs(
    fixBrief.comment_paragraphs,
    fallbackFixBrief(record, replayMetrics, summary).comment_paragraphs,
  );
  const response = await api.asApp().requestJira(route`/rest/api/3/issue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      fields: {
        project: { key: projectKey },
        issuetype: { name: issueType || "Task" },
        summary: fixBrief.summary || summary || `Fix agent prompt for ${issueKey}`,
        description: adfDoc(descriptionParagraphs),
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

  await addIssueComment(created.key, [
    ...commentParagraphs,
    `Brief source: ${fixBrief.source || "unknown"}`,
  ]);
  await attachTrace(created.key, record);

  return {
    key: created.key,
    id: created.id,
    self: created.self,
    linkWarning,
  };
}

resolver.define("getIssueContext", ({ payload, context }) => {
  return issueContextFromRequest(payload, context);
});

resolver.define("listRuns", async ({ payload, context }) => {
  const issueKey = issueKeyFromRequest(payload, context);
  if (!issueKey) {
    return backendFetch("/api/runs");
  }
  return backendFetch(`/api/runs?ticket_id=${encodeURIComponent(issueKey)}`);
});

resolver.define("recordRun", async ({ payload, context }) => {
  const issueKey = issueKeyFromRequest(payload, context);
  if (!issueKey) {
    throw new Error("Could not determine Jira issue key from Forge context.");
  }
  const issue = await fetchIssueDetails(issueKey);
  const data = await backendFetch("/api/record", {
    method: "POST",
    body: JSON.stringify({ ticket_id: issueKey, issue }),
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
  if (!issueKey || !projectKey) {
    throw new Error("Could not determine Jira issue/project key from Forge context.");
  }
  const run = await backendFetch(`/api/runs/${encodeURIComponent(payload.runId)}`);
  const replay = payload.replayMetrics
    ? { metrics: payload.replayMetrics }
    : await backendFetch("/api/replay", {
        method: "POST",
        body: JSON.stringify({ run_id: payload.runId, edits: payload.edits || [] }),
      });
  let brief = fallbackFixBrief(run.record, replay.metrics, payload.summary);
  try {
    const briefResponse = await backendFetch("/api/fix-brief", {
      method: "POST",
      body: JSON.stringify({
        run_id: payload.runId,
        edits: payload.edits || [],
        replay_metrics: replay.metrics,
        summary: payload.summary,
      }),
    });
    brief = briefResponse.brief || brief;
  } catch (error) {
    brief.warning = error.message;
  }
  const fixIssue = await createLinkedFixIssue({
    issueKey,
    projectKey,
    record: run.record,
    replayMetrics: replay.metrics,
    summary: payload.summary,
    issueType: payload.issueType,
    brief,
  });
  return { fixIssue };
});

exports.handler = resolver.getDefinitions();
