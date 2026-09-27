import { expect, test } from "@playwright/test";
import { apiDelete, base, seedSession } from "./helpers";

test("location icons preserve names and expose remote identity, disconnection and recovery", async ({ page }) => {
  const key = await seedSession("location-local", { name: "local-reviewer", runtime: "codex" });
  try {
    await page.addInitScript(() => {
      window.__rubbertermDesktop = { currentTarget: "local", selectedSession: "location-local", targets: [{ id: "local", name: "This Mac" }, { id: "build", name: "Remote — Build server" }] };
      window.webkit = { messageHandlers: { launchRequest: { postMessage: async (message: unknown) => {
        if (localStorage.getItem('probe.remoteOffline')) throw new Error('Disconnected');
        const path = (message as { params?: { path?: string } }).params?.path;
        if (!['/sessions', '/tree', '/approvals', '/session-inbox-counts'].includes(path ?? '')) return { status: 404, body: '{"error":"Not in fixture"}' };
        return { status: 200, body: JSON.stringify({ nodes: [], sessions: [{ session_key: "location-local", name: "remote-reviewer", state: "idle", started_at: 1, updated_at: Date.now(), event_count: 2, test: true }], counts: {}, requests: [], approvals: [] }) };
      } } } };
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto(base());
    const local = page.locator('.rd-row', { has: page.locator('.rd-row-name', { hasText: 'local-reviewer' }) });
    const remote = page.locator('.rd-row', { has: page.locator('.rd-row-name', { hasText: 'remote-reviewer' }) });
    const computer = local.getByRole('group', { name: 'This Mac', exact: true });
    const cloud = remote.getByRole('group', { name: 'Remote · Build server', exact: true });
    await expect(computer).toBeVisible(); await expect(cloud).toBeVisible();
    await expect(page.locator('.rd-host-label')).toHaveCount(0);
    await cloud.hover();
    expect(await cloud.evaluate(node => getComputedStyle(node, '::after').visibility)).toBe('visible');
    await computer.focus();
    expect(await computer.evaluate(node => getComputedStyle(node, '::after').content)).toContain('This Mac');
    for (const mode of ['compact', 'standard', 'relaxed']) {
      await page.getByRole('button', { name: 'Settings', exact: true }).click();
      await page.getByRole('combobox', { name: 'Sidebar density' }).selectOption(mode);
      await page.keyboard.press('Escape');
      const box = (await cloud.boundingBox())!, name = (await remote.locator('.rd-row-name').boundingBox())!;
      expect(box.x + box.width).toBeLessThanOrEqual(name.x);
      expect(name.width).toBeGreaterThan(100);
      await page.screenshot({ path: `/tmp/session-location-${mode}-implemented.png` });
    }
    await page.evaluate(() => { localStorage.setItem('probe.remoteOffline', 'true'); window.dispatchEvent(new Event('remote-sessions-refresh')); });
    const disconnected = remote.getByRole('group', { name: 'Remote · Build server · Disconnected', exact: true });
    await expect(disconnected).toBeVisible();
    await expect(remote.locator('.rd-location-cloud path')).toHaveCount(2);
    await expect(computer).toBeVisible();
    await disconnected.focus();
    await page.screenshot({ path: '/tmp/session-location-offline-implemented.png' });
    await page.evaluate(() => { localStorage.removeItem('probe.remoteOffline'); window.dispatchEvent(new Event('remote-sessions-refresh')); });
    await expect(cloud).toBeVisible();
    await expect(remote.locator('.rd-location-cloud path')).toHaveCount(1);
    await expect(page.locator('.rd-row-name', { hasText: 'remote-reviewer' })).toHaveCount(1);
  } finally { await apiDelete(`/sessions/${key}`); }
});
