import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowRight, Bot, Boxes, Check, ChevronRight, CirclePause,
  CirclePlay, Clock3, FileCode2, FileOutput, GitBranch, KeyRound, LayoutDashboard,
  Menu, Pause, Play, Plus, RefreshCw, Search, ShieldCheck, Square, TerminalSquare,
  X, XCircle, Zap,
} from "lucide-react";
import type { Approval, ModelCatalog, Overview, Run, RunDetail, Task, Workflow } from "./types";
import { approvalSummary, filterRuns, relativeTime, terminalStatuses, titleCase } from "./utils";
import "./styles.css";

type View = "overview" | "runs" | "tasks" | "workflows" | "approvals";
type Modal = "new-run" | "publish" | "approval" | null;

const sampleWorkflow = {
  name: "daily-report",
  description: "Create, review, and deliver a verified report.",
  start: "draft",
  nodes: {
    draft: { type: "task", prompt: "Create the report", next: "approve" },
    approve: { type: "human", payload: { action: "deliver", target: "telegram" }, next: "done" },
    done: { type: "end" },
  },
};

async function request<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `Request failed with HTTP ${response.status}`);
    Object.assign(error, { status: response.status });
    throw error;
  }
  return body as T;
}

function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}><i />{titleCase(value)}</span>;
}

function EmptyState({ icon: Icon = Boxes, title, description, action }: {
  icon?: typeof Boxes; title: string; description: string; action?: React.ReactNode;
}) {
  return <div className="empty-state"><span className="empty-icon"><Icon size={20} /></span><h3>{title}</h3><p>{description}</p>{action}</div>;
}

function Skeleton({ rows = 4 }: { rows?: number }) {
  return <div className="skeleton-list" aria-label="Loading">{Array.from({ length: rows }, (_, index) => <div className="skeleton" key={index} />)}</div>;
}

function ModalShell({ open, title, eyebrow, onClose, children, wide = false }: {
  open: boolean; title: string; eyebrow: string; onClose: () => void; children: React.ReactNode; wide?: boolean;
}) {
  if (!open) return null;
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
    <section className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <header className="modal-header"><div><span className="overline">{eyebrow}</span><h2 id="modal-title">{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></header>
      {children}
    </section>
  </div>;
}

function RunTable({ runs, onSelect }: { runs: Run[]; onSelect: (run: Run) => void }) {
  return <div className="table-wrap"><table><thead><tr><th>Run</th><th>Workflow</th><th>Status</th><th>Updated</th><th /></tr></thead>
    <tbody>{runs.map((run) => <tr key={run.id} onClick={() => onSelect(run)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && onSelect(run)}>
      <td><strong>{run.title || run.workflow_name}</strong><small>{run.id}</small></td>
      <td>{run.workflow_name}<small>Version {run.workflow_version || "—"}</small></td>
      <td><Status value={run.status} /></td><td>{relativeTime(run.updated_at || run.created_at)}</td><td><ChevronRight size={16} /></td>
    </tr>)}</tbody></table></div>;
}

function ApprovalItem({ approval, onReview }: { approval: Approval; onReview: (approval: Approval) => void }) {
  const summary = approvalSummary(approval.payload);
  return <button className="approval-row" onClick={() => onReview(approval)}>
    <span className="approval-icon">{approval.payload.kind === "hermes_tool" ? <TerminalSquare size={18} /> : <ShieldCheck size={18} />}</span>
    <span className="approval-copy"><span className="overline">{summary.kind}</span><strong>{summary.title}</strong><small>{summary.description}</small></span>
    <span className="approval-meta"><small>{relativeTime(approval.created_at)}</small><ChevronRight size={16} /></span>
  </button>;
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("cohubToken") || "");
  const [tokenDraft, setTokenDraft] = useState(token);
  const [view, setView] = useState<View>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [modelCatalog, setModelCatalog] = useState<ModelCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const [modal, setModal] = useState<Modal>(null);
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [runLoading, setRunLoading] = useState(false);
  const [selectedApproval, setSelectedApproval] = useState<Approval | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const [runFilter, setRunFilter] = useState("all");
  const [query, setQuery] = useState("");

  const notify = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2800);
  }, []);

  const loadData = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [overviewData, allRuns, allTasks, allWorkflows, pending] = await Promise.all([
        request<Overview>("/api/overview", token),
        request<{ runs: Run[] }>("/api/runs", token),
        request<{ tasks: Task[] }>("/api/tasks", token),
        request<{ workflows: Workflow[] }>("/api/workflows", token),
        request<{ approvals: Approval[] }>("/api/approvals", token),
      ]);
      setOverview(overviewData); setRuns(allRuns.runs); setTasks(allTasks.tasks);
      setWorkflows(allWorkflows.workflows); setApprovals(pending.approvals);
      setSelectedWorkflow((current) => current || allWorkflows.workflows[0] || null);
      try { setModelCatalog(await request<ModelCatalog>("/api/hermes/models", token)); }
      catch { setModelCatalog(null); }
      setAuthRequired(false); setError("");
    } catch (caught) {
      const apiError = caught as Error & { status?: number };
      if (apiError.status === 401) setAuthRequired(true);
      else setError(apiError.message);
    } finally { setLoading(false); }
  }, [token]);

  useEffect(() => { void loadData(); }, [loadData]);
  useEffect(() => {
    const timer = window.setInterval(() => void loadData(true), 10_000);
    return () => window.clearInterval(timer);
  }, [loadData]);

  const openRun = async (run: Run) => {
    setRunLoading(true); setSelectedRun({ ...run, steps: [], approvals: [], artifacts: [] });
    try { setSelectedRun(await request<RunDetail>(`/api/runs/${run.id}`, token)); }
    catch (caught) { notify((caught as Error).message); setSelectedRun(null); }
    finally { setRunLoading(false); }
  };

  const runAction = async (action: "tick" | "pause" | "resume" | "cancel") => {
    if (!selectedRun) return;
    try {
      const response = await request<RunDetail | { run: RunDetail }>(`/api/runs/${selectedRun.id}/${action}`, token, { method: "POST", body: "{}" });
      setSelectedRun("run" in response ? response.run : response);
      notify(action === "tick" ? "Advanced the next ready step" : `Run ${action}d`);
      await loadData(true);
    } catch (caught) { notify((caught as Error).message); }
  };

  const saveToken = (event: React.FormEvent) => {
    event.preventDefault(); localStorage.setItem("cohubToken", tokenDraft.trim()); setToken(tokenDraft.trim());
  };

  const navigate = (next: View) => { setView(next); setMobileNav(false); };
  const reviewApproval = (approval: Approval) => { setSelectedApproval(approval); setModal("approval"); };
  const filteredRuns = useMemo(() => filterRuns(runs, runFilter, query), [runs, runFilter, query]);
  const filteredTasks = useMemo(() => tasks.filter((task) => `${task.title} ${task.id} ${task.status}`.toLowerCase().includes(query.toLowerCase())), [tasks, query]);

  if (authRequired) return <main className="connection-page"><div className="connection-card"><div className="brand-lockup"><span className="brand-mark"><Bot size={20} /></span><strong>Cohub</strong></div><span className="connection-icon"><KeyRound size={26} /></span><h1>Connect to your control plane</h1><p>Enter the local API token generated by Lona. It stays in this browser and is only sent to this Cohub instance.</p><form onSubmit={saveToken}><label>API token<input autoFocus type="password" value={tokenDraft} onChange={(event) => setTokenDraft(event.target.value)} placeholder="Paste your Cohub token" /></label><button className="button primary" type="submit">Connect securely <ArrowRight size={16} /></button></form><small>Find it in your local Lona <code>.env</code> as <code>COHUB_API_TOKEN</code>.</small></div></main>;

  const navItems: Array<[View, string, typeof LayoutDashboard]> = [
    ["overview", "Overview", LayoutDashboard], ["runs", "Runs", Activity], ["tasks", "Tasks", Check],
    ["workflows", "Workflows", GitBranch], ["approvals", "Approvals", ShieldCheck],
  ];

  return <div className="app-shell">
    {mobileNav && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}>
      <div className="brand-lockup"><span className="brand-mark"><Bot size={19} /></span><div><strong>Cohub</strong><small>Personal coworker</small></div><button className="mobile-close" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
      <nav>{navItems.map(([name, label, Icon]) => <button key={name} className={view === name ? "active" : ""} onClick={() => navigate(name)}><Icon size={17} /><span>{label}</span>{name === "approvals" && approvals.length > 0 && <b>{approvals.length}</b>}</button>)}</nav>
      <div className="sidebar-footer"><span className="live-dot" /><div><strong>Local control plane</strong><small>Auto-refreshing every 10s</small></div></div>
    </aside>

    <main className="main-content">
      <header className="topbar"><div className="topbar-title"><button className="mobile-menu" onClick={() => setMobileNav(true)} aria-label="Open navigation"><Menu size={20} /></button><div><span className="breadcrumb">Workspace / {titleCase(view)}</span><h1>{titleCase(view)}</h1></div></div><div className="topbar-actions"><button className="icon-button" onClick={() => void loadData()} aria-label="Refresh"><RefreshCw size={17} /></button><button className="button primary" onClick={() => setModal("new-run")}><Plus size={16} /> New run</button></div></header>

      {error && <div className="error-banner"><AlertTriangle size={18} /><div><strong>Could not refresh Cohub</strong><span>{error}</span></div><button onClick={() => void loadData()}>Retry</button></div>}
      {loading && !overview ? <Skeleton rows={7} /> : <>
        {view === "overview" && <OverviewView overview={overview} runs={runs} approvals={approvals} onRun={openRun} onApproval={reviewApproval} onNavigate={navigate} onNewRun={() => setModal("new-run")} />}
        {view === "runs" && <section><PageIntro eyebrow="Execution history" title="Runs" description="Track every workflow execution from queue to delivery." action={<button className="button primary" onClick={() => setModal("new-run")}><Plus size={16} /> New run</button>} /><FilterBar query={query} onQuery={setQuery} status={runFilter} onStatus={setRunFilter} />{filteredRuns.length ? <div className="panel table-panel"><RunTable runs={filteredRuns} onSelect={openRun} /></div> : <EmptyState icon={Search} title="No matching runs" description="Try a different search or status filter." />}</section>}
        {view === "tasks" && <section><PageIntro eyebrow="Delegated goals" title="Tasks" description="The user intent behind each workflow run." /><div className="filter-bar"><label className="search"><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search tasks" /></label></div><div className="panel list-panel">{filteredTasks.length ? filteredTasks.map((task) => <div className="task-row" key={task.id}><span className="task-check"><Check size={15} /></span><div><strong>{task.title}</strong><small>{task.id} · Updated {relativeTime(task.updated_at)}</small></div><Status value={task.status} /></div>) : <EmptyState title="No matching tasks" description="Tasks appear when you delegate a new run." />}</div></section>}
        {view === "workflows" && <WorkflowsView workflows={workflows} selected={selectedWorkflow} onSelect={setSelectedWorkflow} onRun={(workflow) => { setSelectedWorkflow(workflow); setModal("new-run"); }} onPublish={() => setModal("publish")} />}
        {view === "approvals" && <section><PageIntro eyebrow="Human in the loop" title="Approvals" description="Review protected workflow actions and Hermes tool requests before they continue." /><div className="attention-note"><ShieldCheck size={18} /><div><strong>Decisions are payload-bound</strong><span>Every decision is checked against the exact SHA-256 payload you reviewed. Hermes tool approvals are always one-shot.</span></div></div><div className="panel approval-list">{approvals.length ? approvals.map((approval) => <ApprovalItem key={approval.id} approval={approval} onReview={reviewApproval} />) : <EmptyState icon={ShieldCheck} title="You're all caught up" description="No protected actions are waiting for your decision." />}</div></section>}
      </>}
    </main>

    <RunDrawer run={selectedRun} loading={runLoading} onClose={() => setSelectedRun(null)} onAction={runAction} />
    <NewRunModal open={modal === "new-run"} workflows={workflows} models={modelCatalog} preferred={selectedWorkflow?.name || ""} token={token} onClose={() => setModal(null)} onCreated={async (run) => { setModal(null); notify("Workflow run started"); await loadData(true); navigate("runs"); await openRun(run); }} />
    <PublishModal open={modal === "publish"} token={token} onClose={() => setModal(null)} onPublished={async () => { setModal(null); notify("Workflow published"); await loadData(true); }} />
    <ApprovalModal approval={selectedApproval} open={modal === "approval"} token={token} onClose={() => { setModal(null); setSelectedApproval(null); }} onResolved={async (decision) => { setModal(null); setSelectedApproval(null); notify(decision === "approve" ? "Approved once" : "Action denied"); await loadData(true); }} />
    <div className={`toast ${toast ? "toast-visible" : ""}`} role="status">{toast}</div>
  </div>;
}

function PageIntro({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: React.ReactNode }) {
  return <div className="page-intro"><div><span className="overline">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>{action}</div>;
}

function FilterBar({ query, onQuery, status, onStatus }: { query: string; onQuery: (value: string) => void; status: string; onStatus: (value: string) => void }) {
  const statuses = ["all", "running", "waiting_for_human", "completed", "failed", "cancelled"];
  return <div className="filter-bar"><label className="search"><Search size={16} /><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search by title, workflow, or run ID" /></label><div className="filter-pills">{statuses.map((item) => <button className={status === item ? "active" : ""} key={item} onClick={() => onStatus(item)}>{titleCase(item)}</button>)}</div></div>;
}

function OverviewView({ overview, runs, approvals, onRun, onApproval, onNavigate, onNewRun }: { overview: Overview | null; runs: Run[]; approvals: Approval[]; onRun: (run: Run) => void; onApproval: (approval: Approval) => void; onNavigate: (view: View) => void; onNewRun: () => void }) {
  const counts = overview?.counts || {};
  const active = runs.filter((run) => !terminalStatuses.has(run.status)).slice(0, 5);
  const metrics = [
    ["Active", (counts.running || 0) + (counts.queued || 0), Activity, "executing now"],
    ["Waiting", counts.waiting_for_human || 0, Clock3, "need a decision"],
    ["Completed", counts.completed || 0, Check, "delivered"],
    ["Failed", counts.failed || 0, XCircle, "need recovery"],
  ] as const;
  return <section>
    {approvals.length > 0 && <button className="approval-banner" onClick={() => onNavigate("approvals")}><span><ShieldCheck size={19} /></span><div><strong>{approvals.length} decision{approvals.length === 1 ? "" : "s"} need your attention</strong><small>Review protected actions to unblock your coworker.</small></div><ArrowRight size={18} /></button>}
    <div className="welcome"><div><span className="overline">Command center</span><h2>Good day, Duthaho.</h2><p>See what's moving, make the decisions that matter, and inspect delivered work.</p></div><button className="button primary" onClick={onNewRun}><Zap size={16} /> Delegate a workflow</button></div>
    <div className="metric-grid">{metrics.map(([label, count, Icon, detail]) => <article className="metric-card" key={label}><div><span>{label}</span><Icon size={16} /></div><strong>{count}</strong><small>{detail}</small></article>)}</div>
    <div className="dashboard-grid"><article className="panel"><div className="panel-header"><div><span className="overline">Live queue</span><h3>Active runs</h3></div><button className="link-button" onClick={() => onNavigate("runs")}>View all <ArrowRight size={14} /></button></div>{active.length ? <div className="compact-list">{active.map((run) => <button key={run.id} onClick={() => onRun(run)}><span className="run-glyph"><Activity size={16} /></span><span><strong>{run.title || run.workflow_name}</strong><small>{run.workflow_name} · {relativeTime(run.updated_at)}</small></span><Status value={run.status} /></button>)}</div> : <EmptyState icon={CirclePause} title="Nothing is running" description="Delegate a workflow when you're ready." />}</article>
    <article className="panel"><div className="panel-header"><div><span className="overline">Review queue</span><h3>Needs attention</h3></div>{approvals.length > 0 && <button className="link-button" onClick={() => onNavigate("approvals")}>View all <ArrowRight size={14} /></button>}</div>{approvals.length ? <div className="compact-list approvals-compact">{approvals.slice(0, 4).map((approval) => <ApprovalItem key={approval.id} approval={approval} onReview={onApproval} />)}</div> : <EmptyState icon={ShieldCheck} title="No decisions pending" description="Protected work will appear here." />}</article></div>
  </section>;
}

function WorkflowsView({ workflows, selected, onSelect, onRun, onPublish }: { workflows: Workflow[]; selected: Workflow | null; onSelect: (workflow: Workflow) => void; onRun: (workflow: Workflow) => void; onPublish: () => void }) {
  return <section><PageIntro eyebrow="Deterministic automation" title="Workflows" description="Published definitions are immutable; every run is pinned to an exact version." action={<button className="button primary" onClick={onPublish}><Plus size={16} /> Publish workflow</button>} />
    {!workflows.length ? <EmptyState icon={GitBranch} title="No workflows yet" description="Publish a JSON workflow definition to get started." action={<button className="button primary" onClick={onPublish}>Publish workflow</button>} /> : <div className="workflow-layout"><aside className="workflow-list">{workflows.map((workflow) => <button key={`${workflow.name}-${workflow.version}`} className={selected?.fingerprint === workflow.fingerprint ? "active" : ""} onClick={() => onSelect(workflow)}><span className="workflow-symbol"><GitBranch size={16} /></span><span><strong>{workflow.name}</strong><small>Version {workflow.version} · {Object.keys(workflow.definition.nodes).length} steps</small></span><ChevronRight size={15} /></button>)}</aside>{selected && <article className="panel workflow-detail"><header><div><span className="overline">Workflow version {selected.version}</span><h3>{selected.name}</h3><p>{selected.definition.description || "Deterministic workflow definition"}</p></div><button className="button primary" onClick={() => onRun(selected)}><Play size={15} /> Run</button></header><div className="fingerprint"><span>Fingerprint</span><code>{selected.fingerprint}</code></div><div className="workflow-canvas">{Object.entries(selected.definition.nodes).map(([id, node], index) => <div className="flow-item" key={id}>{index > 0 && <span className="flow-connector" />}<article className={`flow-node node-${node.type} ${selected.definition.start === id ? "node-start" : ""}`}><div><span className="node-type">{node.type}</span>{selected.definition.start === id && <b>Start</b>}</div><strong>{id}</strong><small>{node.next ? `Next → ${node.next}` : node.routes ? Object.entries(node.routes).map(([label, target]) => `${label} → ${target}`).join(" · ") : node.branches ? `Branches → ${node.branches.join(", ")}` : "Terminal step"}</small></article></div>)}</div></article>}</div>}
  </section>;
}

function NewRunModal({ open, workflows, models, preferred, token, onClose, onCreated }: { open: boolean; workflows: Workflow[]; models: ModelCatalog | null; preferred: string; token: string; onClose: () => void; onCreated: (run: Run) => void }) {
  const [workflow, setWorkflow] = useState("");
  const [title, setTitle] = useState("");
  const [input, setInput] = useState("{}");
  const [modelChoice, setModelChoice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) {
      setWorkflow(preferred || workflows[0]?.name || "");
      setModelChoice("");
    }
  }, [open, preferred, workflows]);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const parsed = JSON.parse(input);
      const selection = modelChoice ? JSON.parse(modelChoice) as [string, string] : null;
      const body = { workflow, title, input: parsed, ...(selection ? { provider: selection[0], model: selection[1] } : {}) };
      const run = await request<Run>("/api/runs", token, { method: "POST", body: JSON.stringify(body) });
      onCreated(run);
    } catch (caught) { setError((caught as Error).message); }
    finally { setBusy(false); }
  };
  const current = models?.current;
  return <ModalShell open={open} title="Delegate a workflow" eyebrow="New run" onClose={onClose}><form onSubmit={submit} className="form-stack">
    <label>Workflow<select value={workflow} onChange={(event) => setWorkflow(event.target.value)} required>{workflows.map((item) => <option key={item.fingerprint} value={item.name}>{item.name} · v{item.version}</option>)}</select></label>
    <label>Task title <span>Optional</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="What outcome do you want?" /></label>
    <label>Model<select aria-label="Model" value={modelChoice} onChange={(event) => setModelChoice(event.target.value)}>
      <option value="">Hermes default{current?.model ? ` · ${current.provider}/${current.model}` : ""}</option>
      {models?.providers.map((provider) => <optgroup key={provider.provider} label={provider.label}>{provider.models.map((model) => <option key={`${provider.provider}/${model.id}`} value={JSON.stringify([provider.provider, model.id])}>{model.id}{model.featured ? " · Featured" : ""}</option>)}</optgroup>)}
    </select><small className="field-help">{models ? "Override the Hermes default for every AI step in this run." : "Model catalog unavailable; this run will use the Hermes default."}</small></label>
    <label>Input <span>JSON object</span><textarea value={input} onChange={(event) => setInput(event.target.value)} rows={7} spellCheck={false} /></label>
    {error && <p className="form-error">{error}</p>}<footer><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy || !workflow}>{busy ? "Starting…" : "Start run"}<ArrowRight size={15} /></button></footer>
  </form></ModalShell>;
}

function PublishModal({ open, token, onClose, onPublished }: { open: boolean; token: string; onClose: () => void; onPublished: () => void }) {
  const [definition, setDefinition] = useState(JSON.stringify(sampleWorkflow, null, 2)); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { await request("/api/workflows", token, { method: "POST", body: JSON.stringify(JSON.parse(definition)) }); onPublished(); } catch (caught) { setError((caught as Error).message); } finally { setBusy(false); } };
  return <ModalShell open={open} title="Publish workflow" eyebrow="Immutable definition" onClose={onClose} wide><form onSubmit={submit} className="form-stack"><div className="code-label"><span>Workflow JSON</span><small>A new immutable version is created after validation.</small></div><textarea className="code-editor" value={definition} onChange={(event) => setDefinition(event.target.value)} rows={22} spellCheck={false} />{error && <p className="form-error">{error}</p>}<footer><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy}>{busy ? "Validating…" : "Validate & publish"}<FileCode2 size={15} /></button></footer></form></ModalShell>;
}

function ApprovalModal({ approval, open, token, onClose, onResolved }: { approval: Approval | null; open: boolean; token: string; onClose: () => void; onResolved: (decision: "approve" | "reject") => void }) {
  const [note, setNote] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  if (!approval) return null;
  const summary = approvalSummary(approval.payload); const hermes = approval.payload.kind === "hermes_tool";
  const resolve = async (decision: "approve" | "reject") => { setBusy(true); setError(""); try { await request(`/api/approvals/${approval.id}/${decision}`, token, { method: "POST", body: JSON.stringify({ payload_hash: approval.payload_hash, note }) }); onResolved(decision); } catch (caught) { setError((caught as Error).message); } finally { setBusy(false); } };
  return <ModalShell open={open} title="Review protected action" eyebrow={summary.kind} onClose={onClose}><div className="approval-review"><div className="review-summary"><span className="approval-icon large">{hermes ? <TerminalSquare size={22} /> : <ShieldCheck size={22} />}</span><div><h3>{summary.title}</h3><p>{summary.description}</p></div></div>{summary.command && <div className="command-preview"><span>Redacted command from Hermes</span><code>{summary.command}</code></div>}<div className="review-context"><div><span>Run</span><button onClick={onClose}>{approval.run_id}</button></div><div><span>Step</span><strong>{approval.step_id}</strong></div><div><span>Decision scope</span><strong>{hermes ? "Once" : "Exact payload"}</strong></div></div><details><summary>Technical details</summary><pre>{JSON.stringify(approval.payload, null, 2)}</pre><div className="hash"><span>SHA-256</span><code>{approval.payload_hash}</code></div></details><label>Decision note <span>Optional</span><textarea rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Add context for the audit trail" /></label>{error && <p className="form-error">{error}</p>}<footer><button className="button danger" disabled={busy} onClick={() => void resolve("reject")}><XCircle size={15} />{hermes ? "Deny tool action" : "Reject"}</button><button className="button primary" disabled={busy} onClick={() => void resolve("approve")}><ShieldCheck size={15} />{hermes ? "Approve once" : "Approve exact payload"}</button></footer></div></ModalShell>;
}

function RunDrawer({ run, loading, onClose, onAction }: { run: RunDetail | null; loading: boolean; onClose: () => void; onAction: (action: "tick" | "pause" | "resume" | "cancel") => void }) {
  if (!run) return null;
  return <><button className="drawer-scrim" onClick={onClose} aria-label="Close run inspector" /><aside className="run-drawer"><header><div><span className="overline">Run inspector</span><h2>{run.title || run.workflow_name}</h2><p>{run.id}</p></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header>{loading ? <Skeleton rows={6} /> : <div className="drawer-body"><div className="run-summary"><Status value={run.status} /><span>{run.workflow_name} · Version {run.workflow_version}</span><small>Updated {relativeTime(run.updated_at)}</small></div>{run.requested_model && <div className="model-summary"><Bot size={16} /><div><span>Selected model</span><strong>{run.requested_provider}/{run.requested_model}</strong></div>{run.usage && <small>{run.usage.total_tokens || 0} tokens</small>}</div>}{!terminalStatuses.has(run.status) && <div className="run-toolbar"><button className="button primary" onClick={() => onAction("tick")}><Play size={14} /> Run next step</button>{run.status === "paused" ? <button className="button secondary" onClick={() => onAction("resume")}><CirclePlay size={14} /> Resume</button> : <button className="button secondary" onClick={() => onAction("pause")}><Pause size={14} /> Pause</button>}<button className="icon-button danger-icon" onClick={() => onAction("cancel")} title="Cancel run"><Square size={14} /></button></div>}<section className="drawer-section"><div className="section-heading"><h3>Steps</h3><span>{run.steps.length}</span></div><div className="timeline">{run.steps.map((step, index) => <div className="timeline-item" key={step.step_id}><span className={`timeline-dot dot-${step.status}`}>{index + 1}</span><div><strong>{step.step_id}</strong><small>{step.error || step.reason || `Attempt ${step.attempt}`}</small></div><Status value={step.status} /></div>)}</div></section>{Boolean(run.external_executions?.length) && <section className="drawer-section"><div className="section-heading"><h3>Hermes execution</h3></div>{run.external_executions?.map((external) => <div className="external-card" key={external.external_run_id}><Bot size={17} /><div><strong>{external.reported_model ? `${external.reported_provider || external.provider}/${external.reported_model}` : external.requested_model ? `${external.requested_provider}/${external.requested_model}` : external.provider}</strong><code>{external.external_run_id}{external.usage?.total_tokens ? ` · ${external.usage.total_tokens} tokens` : ""}</code></div><Status value={external.status} /></div>)}</section>}<section className="drawer-section"><div className="section-heading"><h3>Artifacts</h3><span>{run.artifacts.length}</span></div>{run.artifacts.length ? run.artifacts.map((artifact) => <div className="artifact-row" key={artifact.path}><FileOutput size={17} /><div><strong>{artifact.path}</strong><small>{artifact.size} bytes · {artifact.sha256.slice(0, 12)}…</small></div></div>) : <p className="muted-copy">No artifacts were produced by this run.</p>}</section>{run.error && <div className="run-error"><AlertTriangle size={17} /><span>{run.error}</span></div>}</div>}</aside></>;
}

export default App;
