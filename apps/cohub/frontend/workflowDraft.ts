import type { WorkflowDefinition, WorkflowNode, WorkflowNodeType } from "./types";


function clone(workflow: WorkflowDefinition): WorkflowDefinition {
  return JSON.parse(JSON.stringify(workflow)) as WorkflowDefinition;
}

function nodeForType(type: WorkflowNodeType, next?: string): WorkflowNode {
  if (type === "task") return { type, prompt: "", ...(next ? { next } : {}) };
  if (type === "decision") return { type, routes: {} };
  if (type === "parallel") return { type, branches: [], ...(next ? { next } : {}) };
  if (type === "human") return { type, payload: {}, ...(next ? { next } : {}) };
  return { type: "end" };
}

export function blankWorkflow(): WorkflowDefinition {
  return {
    name: "untitled-workflow",
    description: "",
    start: "done",
    nodes: { done: { type: "end" } },
  };
}

export function addNode(
  workflow: WorkflowDefinition,
  nodeId: string,
  type: WorkflowNodeType,
): WorkflowDefinition {
  const id = nodeId.trim();
  if (!id) throw new Error("Node ID is required");
  if (workflow.nodes[id]) throw new Error(`Node already exists: ${id}`);
  const result = clone(workflow);
  result.nodes[id] = nodeForType(type);
  return result;
}

export function renameNode(
  workflow: WorkflowDefinition,
  oldId: string,
  newId: string,
): WorkflowDefinition {
  const id = newId.trim();
  if (!id) throw new Error("Node ID is required");
  if (!workflow.nodes[oldId]) throw new Error(`Node not found: ${oldId}`);
  if (id !== oldId && workflow.nodes[id]) throw new Error(`Node already exists: ${id}`);
  if (id === oldId) return workflow;

  const result = clone(workflow);
  const entries = Object.entries(result.nodes).map(([key, node]) => [key === oldId ? id : key, node] as const);
  result.nodes = Object.fromEntries(entries);
  if (result.start === oldId) result.start = id;
  for (const node of Object.values(result.nodes)) {
    if (node.next === oldId) node.next = id;
    if (node.routes) {
      node.routes = Object.fromEntries(Object.entries(node.routes).map(([label, target]) => [label, target === oldId ? id : target]));
    }
    if (node.branches) node.branches = node.branches.map((target) => target === oldId ? id : target);
  }
  return result;
}

export function removeNode(workflow: WorkflowDefinition, nodeId: string): WorkflowDefinition {
  if (!workflow.nodes[nodeId]) return workflow;
  const result = clone(workflow);
  delete result.nodes[nodeId];
  for (const node of Object.values(result.nodes)) {
    if (node.next === nodeId) delete node.next;
    if (node.routes) {
      node.routes = Object.fromEntries(Object.entries(node.routes).filter(([, target]) => target !== nodeId));
    }
    if (node.branches) node.branches = node.branches.filter((target) => target !== nodeId);
  }
  if (result.start === nodeId) result.start = Object.keys(result.nodes)[0] || "";
  return result;
}

export function setNodeType(
  workflow: WorkflowDefinition,
  nodeId: string,
  type: WorkflowNodeType,
): WorkflowDefinition {
  const result = clone(workflow);
  const current = result.nodes[nodeId];
  if (!current) throw new Error(`Node not found: ${nodeId}`);
  result.nodes[nodeId] = nodeForType(type, current.next);
  return result;
}
