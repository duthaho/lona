import { expect, test } from "@playwright/test";

test("operator can review attention queue and inspect a run", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Good day, Duthaho." })).toBeVisible();
  await expect(page.getByText("1 decision need your attention")).toBeVisible();

  await page.getByRole("button", { name: /1 decision need/ }).click();
  await expect(page.getByRole("heading", { name: "Approvals", level: 1 })).toBeVisible();
  await page.getByRole("button", { name: /Deliver Briefing/ }).click();
  await expect(page.getByRole("heading", { name: "Review protected action" })).toBeVisible();
  await expect(page.getByText("Exact payload", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve exact payload" })).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();

  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Open navigation" }).click();
  }
  await page.getByRole("button", { name: "Runs", exact: true }).click();
  await page.getByPlaceholder("Search by title, workflow, or run ID").fill("release");
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("row", { name: /release-briefing/i }).nth(1).click();
  await expect(page.getByRole("heading", { name: "Release briefing" })).toBeVisible();
  await expect(page.getByText("Artifacts", { exact: true })).toBeVisible();
});

test("workflow workspace exposes versioned graph and run action", async ({ page }, testInfo) => {
  await page.goto("/");
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Open navigation" }).click();
  }
  await page.getByRole("button", { name: "Workflows", exact: true }).click();
  await page.getByRole("button", { name: /release-briefing Version/ }).click();
  await expect(page.getByRole("heading", { name: "Workflows", level: 1 })).toBeVisible();
  await expect(page.getByText("draft", { exact: true })).toBeVisible();
  await expect(page.getByText("approve", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible();
});

test("operator can override the Hermes model for a run", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "New run", exact: true }).click();
  await page.getByLabel("Model").selectOption({ label: "gpt-5.6 · Featured" });
  await page.getByPlaceholder("What outcome do you want?").fill("Model routed release");
  await page.getByRole("button", { name: /Start run/ }).click();

  await expect(page.getByRole("heading", { name: "Model routed release" })).toBeVisible();
  await expect(page.getByText("openai-codex/gpt-5.6")).toBeVisible();
});

test("operator can save, reopen, validate, and publish a form-based workflow draft", async ({ page }, testInfo) => {
  const workflowName = `form-authored-report-${testInfo.project.name}`;
  await page.goto("/");
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Open navigation" }).click();
  }
  await page.getByRole("button", { name: "Workflows", exact: true }).click();
  await page.getByRole("button", { name: "New draft" }).click();

  await expect(page.getByRole("heading", { name: "untitled-workflow" })).toBeVisible();
  await page.getByLabel("Workflow name").fill(workflowName);
  await page.getByLabel("New node ID").fill("draft");
  await page.getByRole("button", { name: "Add node" }).click();
  await page.getByLabel("Prompt").fill("Create a concise verified report.");
  await page.getByLabel("Next node").selectOption("done");
  await page.getByLabel("Start node").selectOption("draft");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText("Saved revision 2")).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();

  await page.getByRole("button", { name: /Drafts/ }).click();
  await page.getByRole("button", { name: new RegExp(workflowName) }).click();
  await page.getByRole("button", { name: "Advanced JSON" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export JSON" }).click();
  expect((await download).suggestedFilename()).toBe(`${workflowName}.json`);
  await page.getByRole("button", { name: "Form editor" }).click();
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Draft is valid and ready to publish.")).toBeVisible();
  await page.getByRole("button", { name: "Publish", exact: true }).click();

  await page.getByRole("button", { name: /Published/ }).click();
  await expect(page.getByRole("heading", { name: workflowName })).toBeVisible();
});

test("JSON import creates an editable draft instead of publishing", async ({ page }, testInfo) => {
  const workflowName = `imported-report-${testInfo.project.name}`;
  await page.goto("/");
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Open navigation" }).click();
  }
  await page.getByRole("button", { name: "Workflows", exact: true }).click();
  await page.getByRole("button", { name: "Import JSON" }).click();
  await page.getByRole("textbox", { name: "Workflow JSON" }).fill(JSON.stringify({ name: workflowName, start: "done", nodes: { done: { type: "end" } } }, null, 2));
  await page.getByRole("button", { name: "Import as draft" }).click();

  await expect(page.getByRole("heading", { name: workflowName })).toBeVisible();
  await expect(page.getByText("Workflow draft · Revision 1")).toBeVisible();
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: /Drafts/ }).click();
  await expect(page.getByRole("button", { name: new RegExp(`${workflowName} Revision 1`) })).toBeVisible();
});
