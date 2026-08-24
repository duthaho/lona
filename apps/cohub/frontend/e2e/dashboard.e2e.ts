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
  await expect(page.getByRole("heading", { name: "Workflows", level: 1 })).toBeVisible();
  await expect(page.getByText("draft", { exact: true })).toBeVisible();
  await expect(page.getByText("approve", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run", exact: true })).toBeVisible();
});
