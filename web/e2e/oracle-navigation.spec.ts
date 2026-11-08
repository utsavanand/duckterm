import { expect, test } from "@playwright/test";
import { api, apiDelete, apiPost, base } from "./helpers";

test("Oracle navigation preserves the terminal connection and fits narrow windows", async ({ page }) => {
  const edition = await (await api("/edition")).json();
  test.skip(!(edition.features as string[]).includes("oracle"), "Conversational Oracle requires its capability");
  const result = await apiPost("/sessions/launch", {
    command: "sh -c 'echo ORACLE_NAVIGATION_READY; exec cat'", cwd: "/tmp",
    name: "Oracle navigation test", test: true, in_terminal: false,
  });
  expect(result.status).toBe(200);
  const key = String(result.body.session_key);
  let connections = 0;
  page.on("websocket", socket => { if (socket.url().includes(`/sessions/${key}/terminal`)) connections++; });
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Oracle navigation test" }).click();
    const rows = page.locator(".rd-terminal-slot:visible .xterm-rows");
    await expect(rows).toContainText("ORACLE_NAVIGATION_READY");
    const original = connections;
    expect(original).toBeGreaterThan(0);
    for (const width of [1440, 1000, 700]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.getByRole("button", { name: "Oracle main chat", exact: true }).click();
      const composer = page.getByLabel("Message Oracle");
      await expect(composer).toBeVisible();
      const bounds = (await composer.boundingBox())!;
      expect(bounds.width).toBeGreaterThan(100);
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await page.locator(".rd-row-name", { hasText: "Oracle navigation test" }).click();
      await expect(rows).toContainText("ORACLE_NAVIGATION_READY");
      expect(connections).toBe(original);
    }
  } finally {
    await apiPost(`/sessions/${key}/stop`);
    await apiDelete(`/sessions/${key}`);
  }
});
