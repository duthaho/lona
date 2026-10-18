import type { WorkflowDefinition, WorkflowNodeType } from "./types";


export type CanvasPosition = { x: number; y: number };
export type WorkflowLayout = Record<string, CanvasPosition>;
export type CanvasNodeData = {
  label: string;
  nodeType: WorkflowNodeType;
  start: boolean;
  detail: string;
};
export type CanvasNode = { id: string; position: CanvasPosition; data: CanvasNodeData; type: "workflow" };
export type EdgeSemantic = { kind: "next" | "route" | "branch"; route?: string };
export type CanvasEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle: string;
  label: string;
  semantic: EdgeSemantic;
};
export type CanvasConnection = { source: string | null; target: string | null; sourceHandle?: string | null };


function clone(workflow: WorkflowDefinition): WorkflowDefinition {
  return JSON.parse(JSON.stringify(workflow)) as WorkflowDefinition;
}

function nodeDetail(type: WorkflowNodeType): string {
  if (type === "task") return "Hermes task";
  if (type === "decision") return "Route by result";
  if (type === "parallel") return "Parallel branches";
  if (type === "human") return "Human approval";
  return "Terminal";
}

export function defaultCanvasPosition(index: number): CanvasPosition {
  return { x: 40 + (index % 3) * 290, y: 40 + Math.floor(index / 3) * 190 };
}

export function graphFromWorkflow(workflow: WorkflowDefinition, layout: WorkflowLayout = {}): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  const ids = Object.keys(workflow.nodes);
  const known = new Set(ids);
  const nodes = ids.map((id, index): CanvasNode => ({
    id,
    type: "workflow",
    position: layout[id] && Number.isFinite(layout[id].x) && Number.isFinite(layout[id].y)
      ? layout[id]
      : defaultCanvasPosition(index),
    data: { label: id, nodeType: workflow.nodes[id].type, start: workflow.start === id, detail: nodeDetail(workflow.nodes[id].type) },
  }));
  const edges: CanvasEdge[] = [];
  for (const [source, node] of Object.entries(workflow.nodes)) {
    if (node.next && known.has(node.next)) {
      edges.push({
        id: `${source}:next`, source, target: node.next, sourceHandle: "next",
        label: node.type === "parallel" ? "continue" : "next", semantic: { kind: "next" },
      });
    }
    if (node.type === "decision") {
      for (const [route, target] of Object.entries(node.routes || {})) {
        if (known.has(target)) edges.push({
          id: `${source}:route:${route}`, source, target, sourceHandle: `route:${route}`,
          label: route, semantic: { kind: "route", route },
        });
      }
    }
    if (node.type === "parallel") {
      for (const target of node.branches || []) {
        if (known.has(target)) edges.push({
          id: `${source}:branch:${target}`, source, target, sourceHandle: "branch",
          label: "branch", semantic: { kind: "branch" },
        });
      }
    }
  }
  return { nodes, edges };
}

function nextRouteName(routes: Record<string, string>): string {
  if (!("route" in routes)) return "route";
  let index = 2;
  while (`route-${index}` in routes) index += 1;
  return `route-${index}`;
}

export function applyConnection(workflow: WorkflowDefinition, connection: CanvasConnection): WorkflowDefinition {
  const { source, target } = connection;
  if (!source || !target || !workflow.nodes[source] || !workflow.nodes[target]) throw new Error("Connection endpoints must be existing workflow nodes");
  if (source === target) throw new Error("A workflow node cannot connect to itself");
  const result = clone(workflow);
  const node = result.nodes[source];
  const handle = connection.sourceHandle || "";
  if (node.type === "task" || node.type === "human") {
    if (handle !== "next") throw new Error(`${node.type} nodes can only connect through next`);
    node.next = target;
    return result;
  }
  if (node.type === "decision") {
    if (!handle.startsWith("route:")) throw new Error("Decision nodes can only connect through a named route");
    const routes = { ...(node.routes || {}) };
    const requested = handle.slice("route:".length);
    const route = requested === "new" ? nextRouteName(routes) : requested;
    if (!route) throw new Error("Decision route name is required");
    routes[route] = target;
    node.routes = routes;
    return result;
  }
  if (node.type === "parallel") {
    if (handle === "next") {
      node.next = target;
      return result;
    }
    if (handle === "branch") {
      node.branches = [...new Set([...(node.branches || []), target])];
      return result;
    }
    throw new Error("Parallel nodes connect through branch or next");
  }
  throw new Error(`${node.type} nodes cannot connect to another node`);
}

export function disconnectEdge(
  workflow: WorkflowDefinition,
  edge: EdgeSemantic & { source: string; target: string },
): WorkflowDefinition {
  if (!workflow.nodes[edge.source]) return workflow;
  const result = clone(workflow);
  const node = result.nodes[edge.source];
  if (edge.kind === "next" && node.next === edge.target) delete node.next;
  if (edge.kind === "route" && edge.route && node.routes?.[edge.route] === edge.target) {
    const routes = { ...node.routes };
    delete routes[edge.route];
    node.routes = routes;
  }
  if (edge.kind === "branch") node.branches = (node.branches || []).filter((target) => target !== edge.target);
  return result;
}

export function layoutFromCanvasNodes(nodes: Array<{ id: string; position: CanvasPosition }>): WorkflowLayout {
  return Object.fromEntries(nodes.map((node) => [node.id, {
    x: Math.round(node.position.x),
    y: Math.round(node.position.y),
  }]));
}
