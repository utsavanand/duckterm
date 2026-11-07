import { expect, test } from "@playwright/test";
import { apiDelete, seedSession } from "./helpers";

test("session Timeline keeps readable vertical rows with PR history styles loaded", async ({ page }) => {
  const key = `e2e-timeline-layout-${Date.now()}`;
  await seedSession(key, { name: "Timeline layout review" });
  const now = Date.now();
  const entries = Array.from({ length: 88 }, (_, i) => ({
    id: `attention-${i}`, ts: now - i * 60_000, kind: "needs-you",
    one_line: "Needs your attention", detail: { text: `Needs your attention: review the completed change ${i + 1} before release.` }, refs: [],
  }));
  try {
    await page.route(`**/sessions/${key}/timeline?*`, route => route.fulfill({ json: {
      summary: { total: entries.length, counts: {}, text: "", updated_at: now }, entries, next_cursor: null,
    } }));
    await page.goto("/");
    await page.locator(".rd-row", { hasText: "Timeline layout review" }).locator(".rd-row-click").click();
    await page.getByRole("button", { name: "Timeline", exact: true }).click();
    const timeline = page.getByRole("region", { name: "Session timeline" });
    await expect(timeline.locator("article")).toHaveCount(88);
    for (const width of [1972, 1280]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect.poll(async () => timeline.evaluate(el => {
        const rows = Array.from(el.querySelectorAll("article"));
        const first = rows[0].getBoundingClientRect(), second = rows[1].getBoundingClientRect();
        const content = rows[0].querySelector("p")!.getBoundingClientRect();
        const intro = el.querySelector(".rd-timeline-intro")!.getBoundingClientRect();
        const filters = el.querySelector(".rd-timeline-filters")!.getBoundingClientRect();
        return second.top >= first.bottom && Math.abs(first.left - second.left) < 1
          && content.width > 240 && content.height < 100 && filters.top >= intro.bottom
          && el.scrollWidth <= el.clientWidth + 1;
      })).toBe(true);
      await expect(timeline.getByRole("button", { name: "Progress", exact: true })).toBeVisible();
      await page.screenshot({ path: `/tmp/timeline-restored-${width}.png` });
    }
    await timeline.getByRole("button", { name: "Messages", exact: true }).click();
    await expect(timeline).toBeHidden();
  } finally { await apiDelete(`/sessions/${key}`); }
});
