import { expect, test } from "@playwright/test";

test("computer pairing reviews sharing and preserves the dashboard", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.addInitScript(() => {
    window.__rubbertermDesktop = { currentTarget: "local", canCollaborate: true,
      targets: [{ id: "local", name: "This Mac" }, { id: "duckterm-dev", name: "Remote — duckterm-dev" }] };
    window.webkit = { messageHandlers: {
      remoteSession: { postMessage: () => { throw new Error("Must not switch windows"); } },
      launchRequest: { postMessage: async (raw: unknown) => {
        const request = raw as { operation: string; params: { plan_id?: string } };
        if (request.operation === "collaboration-preview") return {
          plan_id: "synthetic-preview", coordinator: "duckterm-dev", source: "This Mac",
          sessions: [
            { host: "local", key: "main", name: "main-local", folder: "Duckterm" },
            { host: "duckterm-dev", key: "remote", name: "main-remote", folder: "Duckterm" },
            { host: "local", key: "private", name: "Personal session", folder: "" },
          ],
        };
        if (request.operation === "collaboration-connect") {
          if (request.params.plan_id !== "synthetic-preview") throw new Error("Review required");
          return { connected: true };
        }
        return { status: 200, body: JSON.stringify({ sessions: [], counts: {}, approvals: [] }) };
      } },
    } };
  });
  await page.goto("/");
  const original = page.url();
  await page.getByRole("button", { name: "Settings", exact: false }).click();
  await page.getByRole("button", { name: "Collaboration", exact: false }).click();
  await expect(page.getByLabel("Always-on coordinator")).toHaveValue("duckterm-dev");
  await page.getByRole("button", { name: "Review sharing" }).click();
  await expect(page.getByRole("region", { name: "Folder sharing preview" })).toContainText("main-local");
  await expect(page.getByText("Personal session · This Mac — Ungrouped (private)")).toBeVisible();
  await page.screenshot({ path: "/tmp/duckterm-collaboration-preview.png", fullPage: true });
  await page.getByRole("button", { name: "Connect computers" }).click();
  await expect(page.getByRole("status")).toContainText("Computer connected");
  expect(page.url()).toBe(original);
});
