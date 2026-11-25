import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { apiDelete, base, seedSession } from "./helpers";

test("activity delivery stays separate from identity and updates in Session details", async ({ page }) => {
  const key = await seedSession(`tracking-${randomUUID()}`, { name: "Activity tracking reviewer", launched: true, runtime: "claude-code", test: true });
  let state = "awaiting";
  // Real session and recovery response; only provider delivery is controlled.
  // The authenticated hook-to-history path has a separate runtime integration test.
  await page.route(`**/sessions/${key}/conversation-recovery`, async route => {
    const response = await route.fetch();
    const identity = await response.json();
    identity.hooks.status = "configured";
    identity.hooks.connection = { state };
    await route.fulfill({ response, json: identity });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(() => localStorage.setItem("rd-theme", "dark"));
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Activity tracking reviewer" }).click();
    await page.locator(".rd-session-controls .rd-session-section > summary").click();
    const tracking = page.locator(".rd-activity-tracking");
    await expect(tracking).toContainText("Awaiting first event");
    await tracking.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/duckterm-activity-tracking-awaiting.png" });
    state = "unconfirmed";
    await expect(tracking).toContainText("Not confirmed", { timeout: 15000 });
    await expect(tracking).toContainText("Check /hooks in Claude Code.");
    await tracking.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/duckterm-activity-tracking-unconfirmed.png" });
    state = "connected";
    await expect(tracking).toContainText("Connected", { timeout: 15000 });
    await expect(tracking).not.toContainText("Check /hooks");
    await page.screenshot({ path: "/tmp/duckterm-activity-tracking-connected.png" });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await page.screenshot({ path: "/tmp/duckterm-activity-tracking-light.png" });
  } finally { await apiDelete(`/sessions/${key}`); }
});
