import { describe, expect, it } from "vitest";
import { addNode, blankWorkflow, removeNode, renameNode, setNodeType } from "./workflowDraft";
import type { WorkflowDefinition } from "./types";


describe("workflow draft transforms", () => {
  it("creates a valid-shaped blank draft with one terminal node", () => {
    expect(blankWorkflow()).toEqual({
      name: "untitled-workflow",
      description: "",
      start: "done",
      nodes: { done: { type: "end" } },
    });
  });

  it("adds, renames, and rewires node references without mutation", () => {
    const original = addNode(blankWorkflow(), "draft", "task");
    original.nodes.draft.next = "done";
    const renamed = renameNode(original, "done", "complete");

    expect(renamed.start).toBe("complete");
    expect(renamed.nodes.draft.next).toBe("complete");
    expect(renamed.nodes.complete.type).toBe("end");
    expect(original.nodes.done).toBeDefined();
  });

  it("removes dangling next, route, and branch references", () => {
    const workflow: WorkflowDefinition = {
      ...blankWorkflow(),
      start: "decide",
      nodes: {
        decide: { type: "decision", routes: { yes: "work", no: "done" } },
        fanout: { type: "parallel", branches: ["work", "done"], next: "done" },
        work: { type: "task", next: "done" },
        done: { type: "end" },
      },
    };
    const removed = removeNode(workflow, "done");

    expect(removed.nodes.decide.routes).toEqual({ yes: "work" });
    expect(removed.nodes.fanout.branches).toEqual(["work"]);
    expect(removed.nodes.fanout.next).toBeUndefined();
    expect(removed.nodes.work.next).toBeUndefined();
  });

  it("resets unsupported fields when changing node type", () => {
    const workflow: WorkflowDefinition = {
      ...blankWorkflow(),
      nodes: { work: { type: "task", prompt: "Draft", next: "done", side_effect: true }, done: { type: "end" } },
      start: "work",
    };
    const changed = setNodeType(workflow, "work", "human");

    expect(changed.nodes.work).toEqual({ type: "human", payload: {}, next: "done" });
  });
});
