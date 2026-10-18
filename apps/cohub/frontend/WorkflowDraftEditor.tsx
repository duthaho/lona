import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronRight, Code2, FileDown, Plus, Save, Send, Trash2, X } from "lucide-react";
import { request } from "./api";
import { WorkflowCanvas } from "./WorkflowCanvas";
import type {
  DraftDiagnostic,
  Workflow,
  WorkflowDefinition,
  WorkflowDraft,
  WorkflowNode,
  WorkflowNodeType,
} from "./types";
import { addNode, removeNode, renameNode, setNodeType } from "./workflowDraft";
import { applyConnection, disconnectEdge, type WorkflowLayout } from "./workflowGraph";


type ValidationResult = { valid: boolean; revision: number; diagnostics: DraftDiagnostic[] };
type PublishResult = { draft: WorkflowDraft; workflow: Workflow };

function cloneDefinition(value: WorkflowDefinition): WorkflowDefinition {
  return JSON.parse(JSON.stringify(value)) as WorkflowDefinition;
}

function editableDefinition(value: unknown): WorkflowDefinition {
  const source = value && typeof value === "object" && !Array.isArray(value)
    ? value as Partial<WorkflowDefinition>
    : {};
  return {
    ...source,
    name: typeof source.name === "string" ? source.name : "untitled-workflow",
    description: typeof source.description === "string" ? source.description : "",
    start: typeof source.start === "string" ? source.start : "",
    nodes: source.nodes && typeof source.nodes === "object" && !Array.isArray(source.nodes) ? source.nodes : {},
  };
}

function JsonObjectField({ label, value, onChange }: {
  label: string;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value]);
  const apply = () => {
    try {
      const parsed = JSON.parse(text);
      if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("Must be a JSON object");
      onChange(parsed as Record<string, unknown>);
      setError("");
    } catch (caught) {
      setError((caught as Error).message);
    }
  };
  return <label>{label}<textarea value={text} onChange={(event) => setText(event.target.value)} onBlur={apply} rows={5} spellCheck={false} />{error && <small className="field-error">{error}</small>}</label>;
}

function TargetSelect({ value = "", nodeIds, currentId, onChange, label = "Next node" }: {
  value?: string;
  nodeIds: string[];
  currentId: string;
  onChange: (value: string | undefined) => void;
  label?: string;
}) {
  return <label>{label}<select value={value} onChange={(event) => onChange(event.target.value || undefined)}><option value="">None</option>{nodeIds.filter((id) => id !== currentId).map((id) => <option key={id} value={id}>{id}</option>)}</select></label>;
}

function NodeInspector({ definition, selectedId, onDefinition, onSelect, onDelete, onRename }: {
  definition: WorkflowDefinition;
  selectedId: string;
  onDefinition: (definition: WorkflowDefinition) => void;
  onSelect: (nodeId: string) => void;
  onDelete: (nodeId: string) => void;
  onRename: (oldId: string, newId: string) => void;
}) {
  const node = definition.nodes[selectedId];
  const nodeIds = Object.keys(definition.nodes);
  const [renameValue, setRenameValue] = useState(selectedId);
  const [routeName, setRouteName] = useState("");
  useEffect(() => setRenameValue(selectedId), [selectedId]);
  if (!node) return <div className="draft-placeholder">Select a node to edit its behavior.</div>;

  const updateNode = (patch: Partial<WorkflowNode>) => {
    const next = cloneDefinition(definition);
    next.nodes[selectedId] = { ...next.nodes[selectedId], ...patch };
    onDefinition(next);
  };
  const changeType = (type: WorkflowNodeType) => onDefinition(setNodeType(definition, selectedId, type));
  const commitRename = () => {
    try {
      const renamed = renameNode(definition, selectedId, renameValue);
      const nextId = renameValue.trim();
      onDefinition(renamed);
      onRename(selectedId, nextId);
      onSelect(nextId);
    } catch {
      setRenameValue(selectedId);
    }
  };
  const remove = () => onDelete(selectedId);

  return <div className="node-inspector">
    <div className="inspector-heading"><div><span className="overline">Node inspector</span><h3>{selectedId}</h3></div><button className="icon-button danger-icon" onClick={remove} disabled={nodeIds.length === 1} title="Remove node"><Trash2 size={15} /></button></div>
    <div className="editor-grid two"><label>Node ID<input value={renameValue} onChange={(event) => setRenameValue(event.target.value)} onBlur={commitRename} /></label><label>Type<select value={node.type} onChange={(event) => changeType(event.target.value as WorkflowNodeType)}><option value="task">AI task</option><option value="decision">Decision</option><option value="parallel">Parallel</option><option value="human">Human approval</option><option value="end">End</option></select></label></div>

    {node.type === "task" && <>
      <label>Prompt<textarea value={node.prompt || ""} onChange={(event) => updateNode({ prompt: event.target.value })} rows={5} placeholder="Describe the outcome Hermes should produce." /></label>
      <div className="editor-grid two"><TargetSelect value={node.next} nodeIds={nodeIds} currentId={selectedId} onChange={(next) => updateNode({ next })} /><label>Maximum attempts<input type="number" min={1} max={10} value={node.max_attempts || 1} onChange={(event) => updateNode({ max_attempts: Math.max(1, Number(event.target.value) || 1) })} /></label></div>
      <label className="check-row"><input type="checkbox" checked={Boolean(node.side_effect)} onChange={(event) => updateNode({ side_effect: event.target.checked, approval_payload: event.target.checked ? (node.approval_payload || {}) : undefined })} /><span><strong>Side-effecting task</strong><small>Require an exact Cohub approval before execution.</small></span></label>
      {node.side_effect && <JsonObjectField label="Approval payload" value={node.approval_payload || {}} onChange={(approval_payload) => updateNode({ approval_payload })} />}
      <JsonObjectField label="Output schema (optional)" value={node.output_schema || {}} onChange={(output_schema) => updateNode({ output_schema })} />
    </>}

    {node.type === "decision" && <div className="route-editor"><span className="field-label">Named routes</span>{Object.entries(node.routes || {}).map(([label, target]) => <div className="route-row" key={label}><input aria-label={`Route ${label}`} value={label} onChange={(event) => { const routes = { ...(node.routes || {}) }; delete routes[label]; routes[event.target.value] = target; updateNode({ routes }); }} /><select aria-label={`Target for ${label}`} value={target} onChange={(event) => updateNode({ routes: { ...(node.routes || {}), [label]: event.target.value } })}>{nodeIds.filter((id) => id !== selectedId).map((id) => <option key={id}>{id}</option>)}</select><button className="icon-button" onClick={() => { const routes = { ...(node.routes || {}) }; delete routes[label]; updateNode({ routes }); }}><X size={14} /></button></div>)}<div className="route-add"><input placeholder="Route label" value={routeName} onChange={(event) => setRouteName(event.target.value)} /><button className="button secondary" disabled={!routeName.trim() || nodeIds.length < 2} onClick={() => { const target = nodeIds.find((id) => id !== selectedId); if (target) updateNode({ routes: { ...(node.routes || {}), [routeName.trim()]: target } }); setRouteName(""); }}><Plus size={14} /> Add route</button></div></div>}

    {node.type === "parallel" && <><div><span className="field-label">Direct one-step branches</span><div className="branch-grid">{nodeIds.filter((id) => id !== selectedId).map((id) => <label className={`branch-option ${(node.branches || []).includes(id) ? "selected" : ""}`} key={id}><input type="checkbox" checked={(node.branches || []).includes(id)} onChange={(event) => updateNode({ branches: event.target.checked ? [...(node.branches || []), id] : (node.branches || []).filter((branch) => branch !== id) })} /><span>{id}</span></label>)}</div></div><TargetSelect value={node.next} nodeIds={nodeIds} currentId={selectedId} onChange={(next) => updateNode({ next })} label="Continue after all branches" /></>}

    {node.type === "human" && <><JsonObjectField label="Review payload" value={node.payload || {}} onChange={(payload) => updateNode({ payload })} /><TargetSelect value={node.next} nodeIds={nodeIds} currentId={selectedId} onChange={(next) => updateNode({ next })} /></>}
    {node.type === "end" && <p className="muted-copy">End nodes are terminal and have no continuation.</p>}
  </div>;
}

export function WorkflowDraftEditor({ draft, token, onClose, onSaved, onPublished }: {
  draft: WorkflowDraft;
  token: string;
  onClose: () => void;
  onSaved: (draft: WorkflowDraft) => void;
  onPublished: (workflow: Workflow) => void;
}) {
  const [definition, setDefinition] = useState(() => editableDefinition(draft.definition));
  const [layout, setLayout] = useState<WorkflowLayout>(() => draft.layout || {});
  const [revision, setRevision] = useState(draft.revision);
  const [selectedId, setSelectedId] = useState(() => {
    const initial = editableDefinition(draft.definition);
    return initial.start || Object.keys(initial.nodes)[0] || "";
  });
  const [advanced, setAdvanced] = useState(false);
  const [jsonText, setJsonText] = useState(() => JSON.stringify(draft.definition, null, 2));
  const [diagnostics, setDiagnostics] = useState<DraftDiagnostic[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [newNodeId, setNewNodeId] = useState("");
  const [newNodeType, setNewNodeType] = useState<WorkflowNodeType>("task");
  const nodeIds = useMemo(() => Object.keys(definition.nodes), [definition.nodes]);

  const changeDefinition = (next: WorkflowDefinition) => {
    setDefinition(next);
    setDirty(true);
    setDiagnostics([]);
  };
  const changeLayout = (next: WorkflowLayout) => {
    setLayout(next);
    setDirty(true);
  };
  const renameLayoutNode = (oldId: string, newId: string) => {
    if (!layout[oldId] || oldId === newId) return;
    const next = { ...layout, [newId]: layout[oldId] };
    delete next[oldId];
    changeLayout(next);
  };
  const deleteNode = (nodeId: string) => {
    if (Object.keys(definition.nodes).length <= 1) {
      setMessage("A workflow draft must keep at least one node.");
      return;
    }
    const next = removeNode(definition, nodeId);
    const nextLayout = { ...layout };
    delete nextLayout[nodeId];
    changeDefinition(next);
    setLayout(nextLayout);
    setSelectedId(next.start || Object.keys(next.nodes)[0] || "");
  };
  const save = async () => {
    if (!dirty) return { ...draft, revision, definition, layout } as WorkflowDraft;
    const saved = await request<WorkflowDraft>(`/api/workflow-drafts/${draft.id}`, token, {
      method: "PUT",
      body: JSON.stringify({ revision, definition, layout }),
    });
    setRevision(saved.revision);
    setDirty(false);
    setMessage(`Saved revision ${saved.revision}`);
    onSaved(saved);
    return saved;
  };
  const runAction = async (action: "save" | "validate" | "publish") => {
    setBusy(true); setMessage("");
    try {
      const saved = await save();
      if (action === "save") return;
      const validation = await request<ValidationResult>(`/api/workflow-drafts/${draft.id}/validate`, token, { method: "POST", body: "{}" });
      setDiagnostics(validation.diagnostics);
      if (!validation.valid) { setMessage("Resolve validation issues before publishing."); return; }
      if (action === "validate") { setMessage("Draft is valid and ready to publish."); return; }
      const published = await request<PublishResult>(`/api/workflow-drafts/${draft.id}/publish`, token, { method: "POST", body: JSON.stringify({ revision: saved.revision }) });
      onSaved(published.draft);
      onPublished(published.workflow);
    } catch (caught) {
      const error = caught as Error & { status?: number };
      setMessage(error.status === 409 ? "This draft changed elsewhere. Close and reopen it before saving again." : error.message);
    } finally { setBusy(false); }
  };
  const applyJson = () => {
    try {
      const parsed = editableDefinition(JSON.parse(jsonText));
      changeDefinition(parsed);
      setSelectedId(parsed.start || Object.keys(parsed.nodes)[0] || "");
      setMessage("Advanced JSON applied. Save to persist it.");
    } catch (caught) { setMessage((caught as Error).message); }
  };
  const exportJson = () => {
    const blob = new Blob([JSON.stringify(definition, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = `${definition.name || "workflow"}.json`; link.click();
    URL.revokeObjectURL(link.href);
  };

  return <div className="modal-backdrop"><section className="modal modal-editor" role="dialog" aria-modal="true" aria-labelledby="draft-editor-title"><header className="modal-header"><div><span className="overline">Workflow draft · Revision {revision}</span><h2 id="draft-editor-title">{definition.name || "Untitled workflow"}</h2></div><div className="modal-header-actions">{dirty && <span className="unsaved-dot">Unsaved changes</span>}<button className="icon-button" onClick={onClose} aria-label="Close"><X size={18} /></button></div></header>
    <div className="draft-toolbar"><div className="segmented"><button className={!advanced ? "active" : ""} onClick={() => setAdvanced(false)}>Form editor</button><button className={advanced ? "active" : ""} onClick={() => { setJsonText(JSON.stringify(definition, null, 2)); setAdvanced(true); }}><Code2 size={14} /> Advanced JSON</button></div><div className="toolbar-actions"><button className="button secondary" onClick={() => runAction("validate")} disabled={busy}><Check size={15} /> Validate</button><button className="button secondary" onClick={() => runAction("save")} disabled={busy || !dirty}><Save size={15} /> Save draft</button><button className="button primary" onClick={() => runAction("publish")} disabled={busy}><Send size={15} /> Publish</button></div></div>
    {message && <div className={`editor-message ${diagnostics.length ? "warning" : ""}`}>{diagnostics.length ? <AlertTriangle size={15} /> : <Check size={15} />}<span>{message}</span></div>}
    {diagnostics.length > 0 && <div className="diagnostic-list">{diagnostics.map((item) => <button key={`${item.path}-${item.message}`} onClick={() => { const nodeId = item.path.startsWith("nodes.") ? item.path.slice(6) : ""; if (nodeId && definition.nodes[nodeId]) { setSelectedId(nodeId); setAdvanced(false); } }}><code>{item.path}</code><span>{item.message}</span><ChevronRight size={14} /></button>)}</div>}
    {advanced ? <div className="advanced-editor"><div className="advanced-heading"><p>Import or edit the canonical executable definition. Canvas layout is stored separately.</p><button className="button secondary" onClick={exportJson}><FileDown size={14} /> Export JSON</button></div><textarea aria-label="Workflow JSON" value={jsonText} onChange={(event) => setJsonText(event.target.value)} rows={24} spellCheck={false} /><button className="button primary" onClick={applyJson}>Apply JSON</button></div> : <div className="draft-editor-layout"><aside className="draft-node-list"><div className="draft-basics"><label>Workflow name<input value={definition.name} onChange={(event) => changeDefinition({ ...definition, name: event.target.value })} /></label><label>Description<textarea value={definition.description || ""} onChange={(event) => changeDefinition({ ...definition, description: event.target.value })} rows={3} /></label><label>Start node<select value={definition.start} onChange={(event) => changeDefinition({ ...definition, start: event.target.value })}>{nodeIds.map((id) => <option key={id}>{id}</option>)}</select></label></div><div className="section-heading"><h3>Nodes</h3><span>{nodeIds.length}</span></div><div className="draft-node-buttons">{nodeIds.map((id) => <button className={selectedId === id ? "active" : ""} key={id} onClick={() => setSelectedId(id)}><span className={`node-badge node-${definition.nodes[id].type}`}>{definition.nodes[id].type}</span><strong>{id}</strong><ChevronRight size={14} /></button>)}</div><div className="add-node"><input aria-label="New node ID" placeholder="new-node" value={newNodeId} onChange={(event) => setNewNodeId(event.target.value)} /><select aria-label="New node type" value={newNodeType} onChange={(event) => setNewNodeType(event.target.value as WorkflowNodeType)}><option value="task">AI task</option><option value="decision">Decision</option><option value="parallel">Parallel</option><option value="human">Human approval</option><option value="end">End</option></select><button className="button secondary" onClick={() => { try { const next = addNode(definition, newNodeId, newNodeType); changeDefinition(next); setSelectedId(newNodeId.trim()); setNewNodeId(""); } catch (caught) { setMessage((caught as Error).message); } }} disabled={!newNodeId.trim()}><Plus size={14} /> Add node</button></div></aside><section className="draft-canvas-panel"><WorkflowCanvas definition={definition} layout={layout} selectedId={selectedId} onSelect={setSelectedId} onDefinitionConnection={(connection) => changeDefinition(applyConnection(definition, connection))} onDeleteEdge={(edge) => changeDefinition(disconnectEdge(definition, edge))} onDeleteNode={deleteNode} onLayout={changeLayout} onMessage={setMessage} /></section><main className="draft-inspector-panel"><NodeInspector definition={definition} selectedId={selectedId} onDefinition={changeDefinition} onSelect={setSelectedId} onDelete={deleteNode} onRename={renameLayoutNode} /></main></div>}
  </section></div>;
}
