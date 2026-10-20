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

test("recovery shows uncertain delivery, preserves conflicts separately and confirms disconnect", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 1000 });
  await page.addInitScript(() => {
    const local = { enabled: true, coordinator: false, last_sync: Date.now(), unsent_count: 1,
      pending_changes: [{ operation: { id: "owner-change", action: "move", old: "Project", new: "Renamed" }, attempted: true, result: null as null | { state: string; error: string } }],
      conflict: { path: "Renamed", token: "reviewed-membership" } as null | { path: string; token: string },
    };
    window.__rubbertermDesktop = { currentTarget: "local", canCollaborate: true,
      targets: [{ id: "local", name: "This Mac" }, { id: "duckterm-dev", name: "duckterm-dev" }] };
    window.webkit = { messageHandlers: {
      remoteSession: { postMessage: () => { throw new Error("Must not switch windows"); } },
      launchRequest: { postMessage: async (raw: unknown) => {
        const request = raw as { target: string; operation: string; params: { id?: string; new?: string; token?: string } };
        if (request.operation === "collaboration-status") return request.target === "local" ? structuredClone(local) : { enabled: true, coordinator: true, last_sync: Date.now() };
        if (request.operation === "collaboration-retry") {
          local.pending_changes[0].result = { state: "blocked", error: "Destination parent changed" };
          return { synced: false };
        }
        if (request.operation === "collaboration-cancel") {
          if (request.params.id !== "owner-change") throw new Error("Wrong pending action");
          local.pending_changes = []; return { cancelled: true };
        }
        if (request.operation === "collaboration-keep-separately") {
          if (request.params.new !== "Renamed (saved)" || request.params.token !== "reviewed-membership") throw new Error("Stale or incorrect recovery");
          local.conflict = null; return { kept_as: request.params.new };
        }
        if (request.operation === "collaboration-disconnect") { local.enabled = false; return { disconnected: true }; }
        return { status: 200, body: JSON.stringify({ sessions: [], counts: {}, approvals: [] }) };
      } },
    } };
  });
  await page.goto("/");
  const original = page.url();
  await page.getByRole("button", { name: "Settings", exact: false }).click();
  await page.getByRole("button", { name: "Collaboration", exact: false }).click();
  await expect(page.getByRole("region", { name: "Connected computers" })).toContainText("This Mac");
  await expect(page.getByRole("button", { name: "Cancel pending change" })).toBeDisabled();
  await page.screenshot({ path: "/tmp/duckterm-collaboration-recovery-implemented.png", fullPage: true });
  await page.getByRole("button", { name: "Retry now" }).click();
  await expect(page.getByRole("region", { name: "Pending folder change" })).toContainText("Destination parent changed");
  await page.getByRole("button", { name: "Cancel pending change" }).click();
  await expect(page.getByRole("region", { name: "Pending folder change" })).toHaveCount(0);
  await page.getByLabel("Keep the existing local folder as").fill("Renamed (saved)");
  await page.getByRole("button", { name: "Keep separately and retry" }).click();
  await expect(page.getByRole("region", { name: "Folder needs attention" })).toHaveCount(0);
  await page.getByRole("button", { name: "Disconnect…", exact: true }).click();
  await expect(page.getByRole("region", { name: "Confirm disconnect" })).toContainText("sessions and files stay in place");
  await page.getByRole("button", { name: "Keep connected", exact: true }).click();
  await expect(page.getByRole("region", { name: "Confirm disconnect" })).toHaveCount(0);
  await page.getByRole("button", { name: "Disconnect…", exact: true }).click();
  await page.getByRole("button", { name: "Disconnect and cancel unsent actions" }).click();
  await expect(page.getByRole("button", { name: "Disconnect…", exact: true })).toHaveCount(0);
  expect(page.url()).toBe(original);
});

test("sidebar folder creation reports pending instead of claiming completion", async ({ page }) => {
  await page.addInitScript(() => {
    window.__rubbertermDesktop = { currentTarget: "local", canCollaborate: true,
      targets: [{ id: "local", name: "This Mac" }] };
    window.webkit = { messageHandlers: { launchRequest: { postMessage: async (raw: unknown) => {
      const request = raw as { operation: string; params: { action: string; new: string } };
      if (request.operation === "collaboration-folder") {
        if (request.params.action !== "create" || request.params.new !== "Queued project") throw new Error("Incorrect canonical request");
        return { handled: true, pending: true };
      }
      return {};
    } } } };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByRole("button", { name: "New folder", exact: false }).click();
  await page.getByPlaceholder("e.g. payments").fill("Queued project");
  await page.getByRole("button", { name: "Create folder", exact: true }).click();
  await expect(page.getByText("Folder creation pending sync. Open Settings → Collaboration to review.", { exact: true })).toBeVisible();
  await expect(page.locator('.rd-group-name', { hasText: "Queued project" })).toHaveCount(0);
});
