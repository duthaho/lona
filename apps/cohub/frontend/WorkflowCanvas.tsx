import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  SmoothStepEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { WorkflowDefinition } from "./types";
import {
  graphFromWorkflow,
  layoutFromCanvasNodes,
  type CanvasNodeData,
  type EdgeSemantic,
  type WorkflowLayout,
} from "./workflowGraph";


type FlowNodeData = CanvasNodeData & { routes: string[] };
type FlowNode = Node<FlowNodeData, "workflow">;
type FlowEdge = Edge<{ semantic: EdgeSemantic }, "smoothstep">;

function WorkflowNodeCard({ data, selected }: NodeProps<FlowNode>) {
  const sourceHandles = data.nodeType === "decision"
    ? [...data.routes.map((route) => ({ id: `route:${route}`, label: route })), { id: "route:new", label: "+ route" }]
    : data.nodeType === "parallel"
      ? [{ id: "branch", label: "branch" }, { id: "next", label: "continue" }]
      : data.nodeType === "task" || data.nodeType === "human"
        ? [{ id: "next", label: "next" }]
        : [];
  return <article aria-label={`Canvas node ${data.label}`} className={`canvas-node canvas-node-${data.nodeType} ${selected ? "selected" : ""}`}>
    <Handle type="target" id="target" position={Position.Left} className="canvas-handle target-handle" />
    <header><span>{data.nodeType}</span>{data.start && <b>Start</b>}</header>
    <strong>{data.label}</strong>
    <small>{data.detail}</small>
    {sourceHandles.map((handle, index) => <div className="source-handle-row" key={handle.id} style={{ top: `${42 + index * Math.min(18, 44 / Math.max(sourceHandles.length - 1, 1))}%` }}>
      <span>{handle.label}</span><Handle type="source" id={handle.id} position={Position.Right} className="canvas-handle" />
    </div>)}
  </article>;
}

const nodeTypes = { workflow: WorkflowNodeCard };
const edgeTypes = { smoothstep: SmoothStepEdge };

export function WorkflowCanvas({ definition, layout, selectedId, onSelect, onDefinitionConnection, onDeleteEdge, onDeleteNode, onLayout, onMessage }: {
  definition: WorkflowDefinition;
  layout: WorkflowLayout;
  selectedId: string;
  onSelect: (nodeId: string) => void;
  onDefinitionConnection: (connection: Connection) => void;
  onDeleteEdge: (edge: EdgeSemantic & { source: string; target: string }) => void;
  onDeleteNode: (nodeId: string) => void;
  onLayout: (layout: WorkflowLayout) => void;
  onMessage: (message: string) => void;
}) {
  const graph = useMemo(() => graphFromWorkflow(definition, layout), [definition, layout]);
  const nodeSignature = useMemo(() => Object.keys(definition.nodes).join("\u0000"), [definition.nodes]);
  const flowNodes = useMemo(() => graph.nodes.map((node): FlowNode => ({
    ...node,
    selected: node.id === selectedId,
    data: {
      ...node.data,
      routes: definition.nodes[node.id].type === "decision" ? Object.keys(definition.nodes[node.id].routes || {}) : [],
    },
  })), [definition, graph.nodes, selectedId]);
  const flowEdges = useMemo(() => graph.edges.map((edge): FlowEdge => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    label: edge.label,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    data: { semantic: edge.semantic },
    className: `canvas-edge edge-${edge.semantic.kind}`,
  })), [graph.edges]);
  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>(flowEdges);
  const [instance, setInstance] = useState<ReactFlowInstance<FlowNode, FlowEdge> | null>(null);

  useEffect(() => setNodes(flowNodes), [flowNodes, setNodes]);
  useEffect(() => setEdges(flowEdges), [flowEdges, setEdges]);
  useEffect(() => {
    if (!instance) return;
    const frame = requestAnimationFrame(() => void instance.fitView({ padding: 0.25, maxZoom: 1.1 }));
    return () => cancelAnimationFrame(frame);
  }, [instance, nodeSignature]);

  return <div className="workflow-flow" aria-label="Workflow canvas">
    <ReactFlow<FlowNode, FlowEdge>
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onSelect(node.id)}
      onNodeDragStop={(_, moved) => {
        const latest = nodes.map((node) => node.id === moved.id ? { ...node, position: moved.position } : node);
        onLayout(layoutFromCanvasNodes(latest));
      }}
      onConnect={(connection) => {
        try { onDefinitionConnection(connection); }
        catch (caught) { onMessage((caught as Error).message); }
      }}
      onEdgesDelete={(deleted) => deleted.forEach((edge) => {
        const semantic = edge.data?.semantic;
        if (semantic) onDeleteEdge({ ...semantic, source: edge.source, target: edge.target });
      })}
      onNodesDelete={(deleted) => deleted.forEach((node) => onDeleteNode(node.id))}
      isValidConnection={(connection) => {
        if (!connection.source || !connection.target || connection.source === connection.target) return false;
        const source = definition.nodes[connection.source];
        return Boolean(source && source.type !== "end");
      }}
      deleteKeyCode={["Backspace", "Delete"]}
      defaultEdgeOptions={{ type: "smoothstep" }}
      onInit={setInstance}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1.1 }}
      minZoom={0.25}
      maxZoom={1.8}
      snapToGrid
      snapGrid={[16, 16]}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1} color="rgba(255,255,255,.07)" />
      <Panel position="top-left" className="canvas-help">Drag nodes · connect handles · Delete removes selection</Panel>
      <MiniMap pannable zoomable nodeColor={(node) => node.data.nodeType === "end" ? "#5dd39e" : node.data.nodeType === "human" ? "#e2ad4b" : "#7567ff"} />
      <Controls showInteractive={false} />
    </ReactFlow>
  </div>;
}
