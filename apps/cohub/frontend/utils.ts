import type { Run } from "./types";

export function filterRuns<T extends Run>(runs: T[], status: string, query: string): T[] {
  const normalized = query.trim().toLowerCase();
  return runs.filter((run) => {
    const statusMatches = status === "all" || run.status === status;
    const text = `${run.id} ${run.workflow_name} ${run.title || ""}`.toLowerCase();
    return statusMatches && (!normalized || text.includes(normalized));
  });
}

export function approvalSummary(payload: Record<string, unknown>) {
  if (payload.kind === "hermes_tool") {
    return {
      kind: "Hermes tool",
      title: String(payload.tool || payload.action || "Tool action"),
      description: String(payload.reason || "Hermes requires permission to continue."),
      command: payload.command ? String(payload.command) : "",
    };
  }
  const action = String(payload.action || "Protected action");
  const target = payload.target ? `Target: ${String(payload.target)}` : "Review the workflow intent before continuing.";
  return {
    kind: "Workflow gate",
    title: action.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()),
    description: target,
    command: "",
  };
}

export function relativeTime(value?: string, now = Date.now()): string {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return "—";
  const seconds = Math.round((timestamp - now) / 1000);
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const ranges: Array<[number, Intl.RelativeTimeFormatUnit]> = [
    [60, "second"],
    [60, "minute"],
    [24, "hour"],
    [7, "day"],
    [4.345, "week"],
    [12, "month"],
    [Number.POSITIVE_INFINITY, "year"],
  ];
  let amount = seconds;
  for (const [boundary, unit] of ranges) {
    if (Math.abs(amount) < boundary) return formatter.format(Math.round(amount), unit);
    amount /= boundary;
  }
  return "—";
}

export function titleCase(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export const terminalStatuses = new Set(["completed", "failed", "cancelled"]);
