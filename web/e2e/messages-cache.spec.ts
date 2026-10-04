import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession } from "./helpers";

test("Messages keeps its last reply on tab return and pauses reads behind Oracle", async ({ page }) => {
  const key = await seedSession(`messages-cache-${Date.now()}`, { name: "Messages cache review", runtime: "codex", test: true });
  let reads = 0, comments = 0, held = false;
  let release: (() => void) | undefined;
  try {
    await page.route(`**/sessions/${key}/messages`, async route => {
      reads++;
      if (held) await new Promise<void>(resolve => { release = resolve; });
      await route.fulfill({ json: { messages: [
        { id: 1, role: "user", blocks: [{ type: "text", text: "Review the navigation change" }] },
        { id: 2, role: "assistant", blocks: [{ type: "text", text: "The navigation review is complete. The existing sessions stay available." }] },
      ] } });
    });
    await page.route(`**/sessions/${key}/annotations`, async route => { comments++; await route.continue(); });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Messages cache review" }).click();
    await page.getByRole("button", { name: "Messages", exact: true }).click();
    const reply = page.locator(".rd-msg-text").filter({ hasText: "The navigation review is complete" });
    await expect(reply).toBeVisible();
    await page.getByRole("button", { name: "Terminal", exact: true }).click();
    held = true;
    await page.getByRole("button", { name: "Messages", exact: true }).click();
    await expect.poll(() => reads).toBe(2);
    await expect(reply).toBeVisible();
    await expect(page.getByText("Loading messages…", { exact: true })).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-messages-cached-return.png" });
    // The next request must wait for the current one, even past the old interval.
    await page.waitForTimeout(3300);
    expect(reads).toBe(2);
    await page.getByRole("button", { name: /^Oracle/ }).click();
    await expect(page.getByRole("heading", { name: /Control tower/ })).toBeVisible();
    held = false; release?.();
    const commentCount = comments;
    await page.waitForTimeout(3300);
    expect(reads).toBe(2); expect(comments).toBe(commentCount);
    await page.getByRole("button", { name: "← Sessions", exact: true }).click();
    await expect.poll(() => reads).toBe(3);
    await expect(reply).toBeVisible();
    await page.getByRole("button", { name: "Terminal", exact: true }).click();
    await page.waitForTimeout(3300);
    expect(reads).toBe(3);
  } finally { held = false; release?.(); await apiDelete(`/sessions/${key}`); }
});
