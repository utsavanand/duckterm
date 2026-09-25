import { expect, test } from "@playwright/test";
import { apiDelete, postEvent, seedSession } from "./helpers";

test("duck celebrates live completion, respects reduced motion, and never celebrates replay", async ({ page }) => {
  const key = "duck-celebration-review";
  await seedSession(key, { name: "Duck celebration review" });
  try {
    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: /^Duck celebration review$/ }).click();
    await expect(page.locator(".rd-duck-celebrating")).toHaveCount(0);
    await postEvent({ session_key: key, event_type: "PreToolUse", tool_name: "Read" });
    await expect(page.locator(".rd-row", { hasText: "Duck celebration review" }).locator(".rd-duck-busy")).toBeVisible();
    await postEvent({ session_key: key, event_type: "Stop" });
    await expect(page.getByRole("img", { name: "Turn complete" })).toHaveCount(2);
    await page.screenshot({ path: "/tmp/duck-completion-real.png" });
    await expect(page.getByRole("img", { name: "Turn complete" })).toHaveCount(0, { timeout: 6000 });
    await page.reload();
    await expect(page.locator(".rd-row-name", { hasText: /^Duck celebration review$/ })).toBeVisible();
    await expect(page.locator(".rd-duck-celebrating")).toHaveCount(0);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await postEvent({ session_key: key, event_type: "PreToolUse", tool_name: "Read" });
    await postEvent({ session_key: key, event_type: "PermissionRequest" });
    const duck = page.locator(".rd-row", { hasText: "Duck celebration review" }).locator(".rd-duck-celebrating-ready > svg");
    await expect(duck).toBeVisible();
    expect(await duck.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
    await page.screenshot({ path: "/tmp/duck-ready-real.png" });
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});
