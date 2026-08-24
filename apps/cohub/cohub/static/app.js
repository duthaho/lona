const state = { overview: null, token: localStorage.getItem("cohubToken") || "", activeView: "today", selectedWorkflow: "" };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]));
const titleCase = (value) => String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

async function api(path, options = {}) {
  const headers = {Accept: "application/json", ...(options.headers || {})};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed with HTTP ${response.status}`);
  return body;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 2600);
}

function status(value) {
  return `<span class="status ${escapeHtml(value)}">${escapeHtml(titleCase(value))}</span>`;
}

function empty(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function runRow(run) {
  return `<button class="list-row text-button run-link" data-run="${escapeHtml(run.id)}"><div><h4>${escapeHtml(run.workflow_name)}</h4><p>v${run.workflow_version} · ${escapeHtml(run.id)}</p></div>${status(run.status)}</button>`;
}

function approvalCard(approval) {
  const hermesTool = approval.payload?.kind === "hermes_tool";
  const eyebrow = hermesTool ? "HERMES TOOL APPROVAL" : "PROTECTED ACTION";
  const approveLabel = hermesTool ? "Approve once" : "Approve exact payload";
  return `<article class="approval-card">
    <p class="eyebrow">${eyebrow}</p><h3>${escapeHtml(approval.step_id)}</h3>
    <pre>${escapeHtml(JSON.stringify(approval.payload, null, 2))}</pre>
    <code>SHA-256 ${escapeHtml(approval.payload_hash)}</code>
    <div class="approval-actions">
      <button class="button primary approval-action" data-id="${escapeHtml(approval.id)}" data-hash="${escapeHtml(approval.payload_hash)}" data-decision="approve">${approveLabel}</button>
      <button class="button danger approval-action" data-id="${escapeHtml(approval.id)}" data-hash="${escapeHtml(approval.payload_hash)}" data-decision="reject">${hermesTool ? "Deny tool action" : "Reject"}</button>
    </div>
  </article>`;
}

function workflowEdges(node) {
  if (node.type === "decision") return Object.entries(node.routes || {}).map(([label, target]) => ({label, target}));
  if (node.type === "parallel") {
    const branches = (node.branches || []).map((target) => ({label: "branch", target}));
    return node.next ? [...branches, {label: "join", target: node.next}] : branches;
  }
  return node.next ? [{label: "next", target: node.next}] : [];
}

function renderWorkflowGraph(workflow) {
  const container = $("#workflowGraph");
  if (!workflow) {
    $("#workflowGraphTitle").textContent = "Select a workflow";
    container.innerHTML = empty("Choose View graph on a published workflow.");
    return;
  }
  const definition = workflow.definition;
  $("#workflowGraphTitle").textContent = `${workflow.name} · v${workflow.version}`;
  container.innerHTML = Object.entries(definition.nodes).map(([nodeId, node]) => {
    const edges = workflowEdges(node);
    const edgeText = edges.length ? edges.map((edge) => `${escapeHtml(edge.label)} → ${escapeHtml(edge.target)}`).join(" · ") : "Terminal node";
    return `<article class="graph-node ${escapeHtml(node.type)}${definition.start === nodeId ? " start" : ""}">
      <div><span>${escapeHtml(node.type.toUpperCase())}</span><strong>${escapeHtml(nodeId)}</strong></div>
      <p>${edgeText}</p>
    </article>`;
  }).join("");
}

async function refresh() {
  try {
    state.overview = await api("/api/overview");
    render();
  } catch (error) {
    toast(error.message);
  }
}

function render() {
  const data = state.overview;
  if (!data) return;
  const counts = data.counts;
  $("#metrics").innerHTML = [
    [counts.running, "Running now", "Active workflow executions"],
    [counts.waiting_for_human, "Needs judgment", "Protected actions waiting"],
    [counts.completed, "Delivered", "Completed runs"],
    [counts.failed, "Needs recovery", "Failed runs"],
  ].map(([number, label, detail]) => `<article class="metric"><span>${label}</span><strong>${number}</strong><span>${detail}</span></article>`).join("");
  $("#approval-badge").textContent = data.approvals.length;
  $("#today-approvals").innerHTML = data.approvals.length ? data.approvals.slice(0, 4).map((item) => `<div class="list-row"><div><h4>${escapeHtml(item.step_id)}</h4><p>${escapeHtml(item.payload.action || "Review required")}</p></div>${status("waiting_for_human")}</div>`).join("") : empty("Nothing needs your attention.");
  $("#today-runs").innerHTML = data.runs.length ? data.runs.slice(0, 6).map(runRow).join("") : empty("No runs yet. Delegate your first workflow.");
  $("#tasks-list").innerHTML = data.tasks.length ? data.tasks.map((task) => `<div class="list-row"><div><h4>${escapeHtml(task.title)}</h4><p>${escapeHtml(task.id)}</p></div>${status(task.status)}</div>`).join("") : empty("No tasks yet.");
  $("#runs-list").innerHTML = data.runs.length ? data.runs.map(runRow).join("") : empty("No workflow runs yet.");
  $("#workflows-grid").innerHTML = data.workflows.length ? data.workflows.map((workflow) => `<article class="workflow-card"><span class="version">VERSION ${workflow.version}</span><h3>${escapeHtml(workflow.name)}</h3><p>${escapeHtml(workflow.definition.description || "Deterministic workflow definition")}</p><code>${escapeHtml(workflow.fingerprint)}</code><div class="workflow-actions"><button class="button ghost workflow-view" data-workflow="${escapeHtml(workflow.name)}" data-version="${workflow.version}">View graph</button><button class="button ghost workflow-run" data-workflow="${escapeHtml(workflow.name)}">Run workflow</button></div></article>`).join("") : empty("Publish your first workflow definition.");
  if (!state.selectedWorkflow && data.workflows.length) state.selectedWorkflow = data.workflows[0].name;
  renderWorkflowGraph(data.workflows.find((workflow) => workflow.name === state.selectedWorkflow) || data.workflows[0]);
  $("#approvals-list").innerHTML = data.approvals.length ? data.approvals.map(approvalCard).join("") : empty("No approvals are waiting.");
  bindDynamicActions();
}

function bindDynamicActions() {
  $$(".run-link").forEach((element) => element.addEventListener("click", () => openRun(element.dataset.run)));
  $$(".workflow-run").forEach((element) => element.addEventListener("click", () => openRunDialog(element.dataset.workflow)));
  $$(".workflow-view").forEach((element) => element.addEventListener("click", () => {
    state.selectedWorkflow = element.dataset.workflow;
    const workflow = state.overview.workflows.find((item) => item.name === element.dataset.workflow && String(item.version) === element.dataset.version);
    renderWorkflowGraph(workflow);
    $("#workflowGraph").scrollIntoView({behavior: "smooth", block: "center"});
  }));
  $$(".approval-action").forEach((element) => element.addEventListener("click", async () => {
    try {
      await api(`/api/approvals/${element.dataset.id}/${element.dataset.decision}`, {method: "POST", body: JSON.stringify({payload_hash: element.dataset.hash})});
      toast(`Approval ${element.dataset.decision}d`);
      await refresh();
    } catch (error) { toast(error.message); }
  }));
}

async function openRun(runId) {
  try {
    const run = await api(`/api/runs/${runId}`);
    showView("runs");
    const detail = $("#run-detail");
    detail.classList.remove("hidden");
    detail.innerHTML = `<div class="panel-head"><div><p class="eyebrow">RUN DETAIL</p><h3>${escapeHtml(run.workflow_name)} · v${run.workflow_version}</h3></div>${status(run.status)}</div>
      <p><code>${escapeHtml(run.workflow_fingerprint)}</code></p>
      <div class="step-list">${run.steps.map((step, index) => `<div class="step"><span class="step-index">${index + 1}</span><div><strong>${escapeHtml(step.step_id)}</strong><p>${escapeHtml(step.reason || step.error || `Attempt ${step.attempt}`)}</p></div>${status(step.status)}</div>`).join("")}</div>
      <div class="dialog-actions"><button class="button ghost" id="tick-run">Run next step</button></div>`;
    $("#artifacts-list").innerHTML = run.artifacts.length ? run.artifacts.map((artifact) => `<div class="list-row"><div><h4>${escapeHtml(artifact.path)}</h4><p>${escapeHtml(artifact.sha256)}</p></div><span>${artifact.size} bytes</span></div>`).join("") : empty("This run has no artifacts yet.");
    $("#tick-run").addEventListener("click", async () => {
      try { await api(`/api/runs/${runId}/tick`, {method: "POST", body: "{}"}); await refresh(); await openRun(runId); } catch (error) { toast(error.message); }
    });
  } catch (error) { toast(error.message); }
}

function showView(name) {
  state.activeView = name;
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === name));
  $("#page-title").textContent = titleCase(name);
}

function openRunDialog(workflowName = "") {
  const select = $("#run-workflow");
  select.innerHTML = (state.overview?.workflows || []).map((workflow) => `<option value="${escapeHtml(workflow.name)}">${escapeHtml(workflow.name)} · v${workflow.version}</option>`).join("");
  if (workflowName) select.value = workflowName;
  $("#run-dialog").showModal();
}

$$('.nav-item').forEach((item) => item.addEventListener("click", () => showView(item.dataset.view)));
$$('[data-go]').forEach((item) => item.addEventListener("click", () => showView(item.dataset.go)));
$$('[data-open-run]').forEach((item) => item.addEventListener("click", () => openRunDialog()));
$("#new-run-button").addEventListener("click", () => openRunDialog());
$("#token-button").addEventListener("click", () => {
  const token = window.prompt("Cohub API token", state.token);
  if (token !== null) { state.token = token; localStorage.setItem("cohubToken", token); refresh(); }
});
$("#run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const input = JSON.parse($("#run-input").value);
    await api("/api/runs", {method: "POST", body: JSON.stringify({workflow: $("#run-workflow").value, title: $("#run-title").value, input})});
    $("#run-dialog").close(); toast("Workflow run started"); await refresh(); showView("runs");
  } catch (error) { $("#run-error").textContent = error.message; }
});
const sampleWorkflow = {name:"daily-report",description:"Create, approve, and deliver a verified report.",start:"draft",nodes:{draft:{type:"task",prompt:"Create the report",output_schema:{type:"object",required:["report"]},next:"approve"},approve:{type:"human",payload:{action:"deliver",target:"telegram"},next:"done"},done:{type:"end"}}};
$("#publish-workflow-button").addEventListener("click", () => { $("#workflow-json").value = JSON.stringify(sampleWorkflow, null, 2); $("#workflow-dialog").showModal(); });
$("#workflow-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await api("/api/workflows", {method:"POST", body: JSON.stringify(JSON.parse($("#workflow-json").value))}); $("#workflow-dialog").close(); toast("Workflow published"); await refresh(); }
  catch (error) { $("#workflow-error").textContent = error.message; }
});

refresh();
window.setInterval(refresh, 10000);
