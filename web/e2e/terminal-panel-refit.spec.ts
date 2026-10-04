import { expect, test } from "@playwright/test";
import { apiPost, apiDelete, base } from "./helpers";

test("terminal refits after right panel changes and keeps the input draft", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  const sizes: { cols: number; rows: number }[] = [];
  page.on("websocket", socket => socket.on("framesent", ({ payload }) => {
    if (typeof payload !== "string") return;
    try { const data = JSON.parse(payload); if (data.resize) sizes.push(data.resize); } catch { /* Terminal input is binary. */ }
  }));
  const result = await apiPost("/sessions/launch", { command: "sh -c 'echo REFIT_READY; cat'", cwd: "/tmp", name: "Panel refit check", in_terminal: false, test: true });
  expect(result.status).toBe(200);
  const key = String(result.body.session_key);
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Panel refit check" }).click();
    const rows = page.locator(".rd-terminal-slot:visible .xterm-rows");
    await expect(rows).toContainText("REFIT_READY");
    const expand = page.getByRole("button", { name: "Show Context panel", exact: true });
    if (await expand.isVisible()) await expand.click();
    await expect.poll(() => sizes.at(-1)?.cols ?? 0).toBeGreaterThan(0);
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.keyboard.type("DRAFT_STAYS");
    const before = sizes.at(-1)!;
    for (let i = 0; i < 2; i++) {
      await page.getByRole("button", { name: "Collapse Context panel", exact: true }).click();
      await expect.poll(() => sizes.at(-1)?.cols ?? 0).toBeGreaterThan(before.cols);
      await expand.click();
      await expect.poll(() => sizes.at(-1)?.cols).toBe(before.cols);
      await expect(rows).toContainText("DRAFT_STAYS");
    }
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.keyboard.press("Enter");
    await expect(rows).toContainText("DRAFT_STAYS");
  } finally { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
});
