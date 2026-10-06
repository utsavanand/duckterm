import { expect, test } from "@playwright/test";
import { apiDelete, base, postEvent, seedSession } from "./helpers";

test("needs-you wing waves across session surfaces, respects reduced motion and clears with the answer", async ({ page }) => {
  const key = await seedSession("duck-wave-review", { name: "Wave reviewer", test: true });
  const other = await seedSession("duck-wave-other", { name: "Wave observer", test: true });
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
    await page.locator('.rd-row-name', { hasText: /^Wave observer$/ }).click();
    const row = page.locator('.rd-row', { hasText: 'Wave reviewer' });
    await postEvent({ session_key: key, event_type: "PermissionRequest" });
    await postEvent({ session_key: key, event_type: "PreToolUse", tool_name: "Read" });
    const wing = row.locator('.duck-wave-wing');
    await expect(wing).toBeVisible(); // latched attention survives resumed activity
    expect(await wing.evaluate(e => getComputedStyle(e).animationName)).toBe('duck-wave');
    expect(await wing.evaluate(e => getComputedStyle(e).animationIterationCount)).toBe('infinite');
    await postEvent({ session_key: key, event_type: "PermissionRequest" });
    await row.locator('.rd-row-name').click();
    await expect(page.locator('.rd-context-pane .duck-wave-wing')).toBeVisible();
    await expect(row.locator('.rd-duck-celebrating')).toHaveCount(0);
    await page.screenshot({ path: '/tmp/duck-wave-implemented-dark.png' });
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('combobox', { name: 'Sidebar density' }).selectOption('compact');
    await page.keyboard.press('Escape');
    await page.evaluate(() => document.documentElement.dataset.theme = 'light');
    await expect(wing).toBeVisible();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    expect(await wing.evaluate(e => getComputedStyle(e).animationName)).toBe('none');
    await page.screenshot({ path: '/tmp/duck-wave-implemented-light-compact.png' });
    await page.locator('.rd-context-pane').getByRole('button', { name: 'Pin Wave reviewer', exact: true }).click();
    await page.getByRole('button', { name: 'Focus · 1', exact: true }).click();
    await expect(page.locator('.rd-grid-tile .duck-wave-wing')).toBeVisible();
    await page.screenshot({ path: '/tmp/duck-wave-implemented-focus.png' });
    await postEvent({ session_key: key, event_type: 'UserPromptSubmit', prompt: 'Approved test request' });
    await expect(page.locator('.duck-wave-wing')).toHaveCount(0);
    await expect(page.locator('.rd-grid-tile .rd-duck-busy')).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
    await apiDelete(`/sessions/${other}`);
  }
});
