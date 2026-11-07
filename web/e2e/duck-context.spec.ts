import { expect, test } from "@playwright/test";
import { apiDelete, seedSession } from "./helpers";

for (const desktop of [false, true]) {
  test(`context ducks follow reported usage and reset after compaction (${desktop ? "desktop" : "browser"})`, async ({ page }) => {
    const key = await seedSession(`duck-context-${desktop}-${Date.now()}`, { name: "Context colour check", runtime: "codex", test: true });
    let used = 202_390;
    try {
      if (desktop) await page.addInitScript(() => { window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }] }; });
      await page.route("**/sessions", async route => {
        const response = await route.fetch();
        const data = await response.json();
        for (const session of data.sessions ?? []) if (session.session_key === key) {
          session.context_tokens = used;
          session.context_window_tokens = 258_400;
          session.model = "unknown-model";
        }
        await route.fulfill({ response, json: data });
      });
      await page.goto("/");
      const row = page.locator(".rd-row", { hasText: "Context colour check" });
      const body = row.locator('[fill="var(--duck-body, #FFD32B)"]').first();
      await expect(row.getByRole("img", { name: /Context 78% used, warm/ })).toBeVisible();
      await expect(row.locator(".rd-ctx-chip.warm")).toHaveText("202k");
      await expect(body).not.toHaveCSS("fill", "rgb(255, 211, 43)");
      used = 245_480;
      await page.reload();
      await expect(row.getByRole("img", { name: /Context 95% used, nearly full/ })).toBeVisible();
      await expect(body).toHaveCSS("fill", "rgb(217, 67, 67)");
      await expect(row.locator(".rd-ctx-chip.high")).toHaveText("245k");
      await page.screenshot({ path: `/tmp/duck-context-${desktop ? "desktop" : "browser"}.png` });
      used = 25_840;
      await page.reload();
      await expect(body).toHaveCSS("fill", "rgb(255, 211, 43)");
      await expect(row.getByRole("img", { name: /Context/ })).toHaveCount(0);
      await expect(row.locator(".rd-ctx-chip")).toHaveCount(0);
    } finally {
      await apiDelete(`/sessions/${key}`);
    }
  });
}
