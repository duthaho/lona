import { describe, expect, it } from "vitest";
import { approvalSummary, filterRuns, relativeTime } from "./utils";

const runs = [
  { id: "run_alpha", workflow_name: "daily-report", status: "running", created_at: "2026-08-24T08:00:00Z" },
  { id: "run_beta", workflow_name: "deploy", status: "failed", created_at: "2026-08-24T07:00:00Z" },
];

describe("filterRuns", () => {
  it("combines status and text filters without losing matching identifiers", () => {
    expect(filterRuns(runs, "failed", "beta").map((run) => run.id)).toEqual(["run_beta"]);
    expect(filterRuns(runs, "running", "deploy")).toEqual([]);
  });
});

describe("approvalSummary", () => {
  it("prioritizes redacted Hermes command and reason over raw payload JSON", () => {
    expect(approvalSummary({kind: "hermes_tool", tool: "terminal", command: "docker restart api", reason: "service restart"})).toEqual({
      kind: "Hermes tool",
      title: "terminal",
      description: "service restart",
      command: "docker restart api",
    });
  });

  it("summarizes workflow approvals with human-readable intent", () => {
    expect(approvalSummary({action: "deliver", target: "telegram"})).toMatchObject({
      kind: "Workflow gate",
      title: "Deliver",
      description: "Target: telegram",
    });
  });
});

describe("relativeTime", () => {
  it("returns a stable fallback when a date is missing or invalid", () => {
    expect(relativeTime(undefined)).toBe("—");
    expect(relativeTime("not-a-date")).toBe("—");
  });
});
