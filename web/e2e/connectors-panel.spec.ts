import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base } from "./helpers";

// v0.4.85 shipped connectors rendered but invisible: the context body above is
// `flex: 0 1 auto`, so a tall Session card took the whole right column and left
// the connectors list zero pixels high. Presence in the DOM proved nothing, so
// these assert visible height and an on-screen row.
test("connectors keep usable height under a tall session card, and show what they serve", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 760 });
  const result = await apiPost("/sessions/launch", {
    command: "python3 -u -c 'import sys; print(\"CONNECTOR_PANEL_READY\"); [line for line in sys.stdin]'",
    cwd: "/tmp",
    name: "connector-panel-check",
    in_terminal: false,
    test: true,
  });
  expect(result.status).toBe(200);
  const key = String(result.body.session_key);
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "connector-panel-check" }).click();

    const panel = page.locator(".rd-connectors");
    await expect(panel).toBeVisible();
    const box = (await panel.boundingBox())!;
    expect(box.height).toBeGreaterThan(80);

    // The heading must sit inside the viewport, not below its bottom edge.
    const heading = panel.locator(".rd-panel-head");
    await expect(heading).toBeInViewport();

    // At least one connector row is reachable, which is what "I can see my
    // connectors" means to a user.
    await expect(panel.locator(".rd-connector").first()).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});
