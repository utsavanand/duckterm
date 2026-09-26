import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base } from "./helpers";

test("side panels reclaim space independently, preserve PTY drafts, and remember choices", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const sizes: { cols: number; rows: number }[] = [];
  let connections = 0;
  page.on("websocket", socket => {
    if (!socket.url().includes(`/sessions/${key}/terminal`)) return;
    connections++;
    socket.on("framesent", ({ payload }) => {
      if (typeof payload === "string") {
        try { const data = JSON.parse(payload); if (data.resize) sizes.push(data.resize); }
        catch { /* Terminal input is not a resize frame. */ }
      }
    });
  });
  const result = await apiPost("/sessions/launch", { command: "python3 -u -c 'import sys; print(\"PANEL_READY\"); [print(\"SUBMITTED:\"+line.rstrip()) for line in sys.stdin]'", cwd: "/tmp", name: "panel-resize-check", in_terminal: false, test: true });
  expect(result.status).toBe(200);
  const key = String(result.body.session_key);
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "panel-resize-check" }).click();
    const rows = page.locator(".rd-terminal-slot:visible .xterm-rows");
    const input = page.locator(".rd-terminal-slot:visible .xterm-helper-textarea");
    const center = page.locator(".rd-terminal-pane");
    const width = async () => (await center.boundingBox())!.width;
    await expect(rows).toContainText("PANEL_READY");
    await expect.poll(() => sizes.at(-1)?.cols ?? 0).toBeGreaterThan(0);
    const originalWidth = await width();
    const originalCols = sizes.at(-1)!.cols;
    const originalConnections = connections;
    const draft = "UNSENT_" + "0123456789".repeat(12) + "_END";
    await input.focus(); await page.keyboard.type(draft);
    const rendered = async () => (await rows.innerText()).replace(/\s/g, "");
    await expect.poll(rendered).toContain(draft);
    await page.screenshot({ path: "/tmp/duckterm-panels-expanded.png" });
    await page.getByRole("button", { name: "Collapse Agents panel", exact: true }).click();
    await expect.poll(width).toBeGreaterThan(originalWidth + 200);
    await expect.poll(() => sizes.at(-1)!.cols).toBeGreaterThan(originalCols);
    await expect(page.locator(".rd-agents .rd-row-name", { hasText: "panel-resize-check" })).toBeHidden();
    const leftOnlyWidth = await width();
    const leftOnlyCols = sizes.at(-1)!.cols;
    await page.getByRole("button", { name: "Collapse Context panel", exact: true }).click();
    await expect.poll(width).toBeGreaterThan(leftOnlyWidth + 200);
    await expect.poll(() => sizes.at(-1)!.cols).toBeGreaterThan(leftOnlyCols);
    await expect.poll(rendered).toContain(draft);
    await expect(rows).not.toContainText("SUBMITTED:");
    await page.screenshot({ path: "/tmp/duckterm-panels-collapsed.png" });
    const wideCols = sizes.at(-1)!.cols;
    await page.getByRole("button", { name: "Oracle", exact: true }).click();
    await expect(page.getByLabel("Message Oracle")).toBeVisible();
    await page.getByRole("button", { name: "← Sessions", exact: true }).click();
    await expect.poll(() => sizes.at(-1)!.cols).toBe(wideCols);
    await expect(page.getByRole("button", { name: "Show Context panel", exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Show Agents panel", exact: true })).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("button", { name: "Show Context panel", exact: true })).toHaveAttribute("aria-expanded", "false");
    await page.getByRole("button", { name: "Show Agents panel", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.locator(".rd-row-name", { hasText: "panel-resize-check" }).click();
    await expect.poll(rendered).toContain(draft);
    await page.getByRole("button", { name: "Show Context panel", exact: true }).click();
    await expect.poll(width).toBe(originalWidth);
    await expect.poll(() => sizes.at(-1)!.cols).toBe(originalCols);
    // Only the explicit reload should create another terminal connection.
    expect(connections).toBe(originalConnections + 1);
    await input.focus(); await page.keyboard.press("Enter");
    await expect(rows).toContainText("SUBMITTED:");
    await page.setViewportSize({ width: 1000, height: 900 });
    const stackedHeight = (await center.boundingBox())!.height;
    await page.getByRole("button", { name: "Collapse Agents panel", exact: true }).click();
    await page.getByRole("button", { name: "Collapse Context panel", exact: true }).click();
    await expect.poll(async () => (await center.boundingBox())!.height).toBeGreaterThan(stackedHeight);
    await expect(page.getByRole("button", { name: "Show Agents panel", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Show Context panel", exact: true })).toBeVisible();
  } finally {
    await apiPost(`/sessions/${key}/stop`);
    await apiDelete(`/sessions/${key}`);
  }
});
