import { describe, expect, it } from "vitest";
import type { WorkflowDefinition } from "./types";
import {
  applyConnection,
  defaultCanvasPosition,
  disconnectEdge,
  graphFromWorkflow,
  layoutFromCanvasNodes,
} from "./workflowGraph";


const workflow: WorkflowDefinition = {
  name: "graph",
  start: "draft",
  nodes: {
    draft: { type: "task", next: "decide" },
    decide: { type: "decision", routes: { approve: "fanout", reject: "done" } },
    fanout: { type: "parallel", branches: ["mail", "calendar"], next: "done" },
    mail: { type: "task" },
    calendar: { type: "task" },
    done: { type: "end" },
  },
};


describe("workflow graph transforms", () => {
  it("creates deterministic typed nodes and labeled semantic edges", () => {
    const graph = graphFromWorkflow(workflow, { draft: { x: 45, y: 90 } });

    expect(graph.nodes[0]).toMatchObject({ id: "draft", position: { x: 45, y: 90 }, data: { nodeType: "task", start: true } });
    expect(graph.nodes.find((node) => node.id === "decide")?.position).toEqual(defaultCanvasPosition(1));
    expect(graph.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "draft:next", source: "draft", target: "decide", label: "next", semantic: { kind: "next" } }),
      expect.objectContaining({ id: "decide:route:approve", target: "fanout", label: "approve", semantic: { kind: "route", route: "approve" } }),
      expect.objectContaining({ id: "fanout:branch:mail", target: "mail", label: "branch", semantic: { kind: "branch" } }),
      expect.objectContaining({ id: "fanout:next", target: "done", label: "continue", semantic: { kind: "next" } }),
    ]));
  });

  it("applies only connections supported by the source node semantics", () => {
    const task = applyConnection(workflow, { source: "mail", target: "done", sourceHandle: "next" });
    expect(task.nodes.mail.next).toBe("done");

    const routed = applyConnection(workflow, { source: "decide", target: "mail", sourceHandle: "route:new" });
    expect(routed.nodes.decide.routes).toMatchObject({ approve: "fanout", reject: "done", route: "mail" });

    const branched = applyConnection(workflow, { source: "fanout", target: "mail", sourceHandle: "branch" });
    expect(branched.nodes.fanout.branches).toEqual(["mail", "calendar"]);

    expect(() => applyConnection(workflow, { source: "done", target: "draft", sourceHandle: "next" })).toThrow(/cannot connect/i);
    expect(() => applyConnection(workflow, { source: "draft", target: "draft", sourceHandle: "next" })).toThrow(/itself/i);
  });

  it("removes exactly the semantic represented by a deleted edge", () => {
    const withoutRoute = disconnectEdge(workflow, { kind: "route", source: "decide", target: "fanout", route: "approve" });
    expect(withoutRoute.nodes.decide.routes).toEqual({ reject: "done" });

    const withoutBranch = disconnectEdge(workflow, { kind: "branch", source: "fanout", target: "mail" });
    expect(withoutBranch.nodes.fanout.branches).toEqual(["calendar"]);

    const withoutNext = disconnectEdge(workflow, { kind: "next", source: "draft", target: "decide" });
    expect(withoutNext.nodes.draft.next).toBeUndefined();
  });

  it("extracts rounded layout without mutating executable semantics", () => {
    const layout = layoutFromCanvasNodes([
      { id: "draft", position: { x: 10.4, y: 20.7 } },
      { id: "done", position: { x: 401.9, y: 99.2 } },
    ]);
    expect(layout).toEqual({ draft: { x: 10, y: 21 }, done: { x: 402, y: 99 } });
    expect(workflow.nodes.draft.next).toBe("decide");
  });
});
