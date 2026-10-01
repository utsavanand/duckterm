import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, seedSession } from "./helpers";

test("folder priority preserves exact text, durable delivery status and acknowledged cancellation", async ({ page }) => {
  test.setTimeout(60_000);
  const folder = `Priority-review-${Date.now()}`;
  const keys = [`priority-review-${Date.now()}`, `priority-qa-${Date.now()}`];
  const credentials: string[] = [];
  try {
    for (let i = 0; i < keys.length; i++) {
      await seedSession(keys[i], { runtime: i ? "codex" : "claude-code", group: i ? folder + "/Research" : folder, name: i ? "qa" : "review-dev" });
      const enrolled = await apiPost(`/sessions/${keys[i]}/collaboration`, { root: folder });
      expect(enrolled.status).toBe(200); credentials.push(String(enrolled.body.token));
    }
    const openFolder = async () => {
      const head = page.locator('.rd-group-head').filter({ has: page.getByRole('button', { name: `View interactions in ${folder}`, exact: true }) });
      await head.locator('.rd-group-name').click();
    };
    let statusReads = 0;
    page.on('request', r => { if (r.method() === 'GET' && new URL(r.url()).pathname.startsWith('/broadcasts/')) statusReads++; });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto(base()); await openFolder();
    await page.getByRole('button', { name: 'To everyone in this folder', exact: true }).click();
    const priority = page.getByRole('checkbox', { name: 'Priority', exact: true });
    await expect(priority).not.toBeChecked(); await priority.check();
    const exact = '  Please finish your current check, then share the résumé 🦆.\nKeep this wording.\n';
    await page.getByRole('textbox', { name: 'Ask this folder' }).fill(exact);
    await page.getByRole('button', { name: 'Review recipients', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('review-dev');
    await expect(page.getByRole('dialog')).toContainText('qa');
    expect(await page.locator('.rd-folder-review-text').textContent()).toBe(exact);
    await page.screenshot({ path: '/tmp/duckterm-priority-implemented-review.png' });
    await page.getByRole('button', { name: 'Send to 2 sessions', exact: true }).click();
    await expect(page.locator('.rd-delivery-chip').filter({ hasText: 'pending next turn' })).toHaveCount(1);
    await expect(page.locator('.rd-delivery-chip').filter({ hasText: 'inbox only' })).toHaveCount(1);
    const inboxes = [];
    for (const token of credentials) {
      const response = await fetch(`${base()}/api/v1/session/inbox`, { headers: { Authorization: `Bearer ${token}` } });
      expect(response.status).toBe(200);
      const inbox = await response.json(); inboxes.push(inbox.messages);
      expect(inbox.messages).toHaveLength(1);
      expect(inbox.messages[0]).toMatchObject({ question: exact, priority: true, sender_kind: 'owner' });
    }
    await page.screenshot({ path: '/tmp/duckterm-priority-implemented-dark.png' });
    await page.getByRole('tab', { name: 'Artifacts', exact: true }).click();
    const before = statusReads; await page.waitForTimeout(3400); expect(statusReads).toBe(before);
    await page.getByRole('tab', { name: 'Chat', exact: true }).click();
    await page.reload(); await openFolder();
    await expect(page.locator('.rd-folder-question p')).toHaveText(exact);
    await expect(page.getByText('inbox only', { exact: true })).toBeVisible();
    await page.emulateMedia({ colorScheme: 'light' });
    await page.screenshot({ path: '/tmp/duckterm-priority-implemented-light.png' });
    await page.setViewportSize({ width: 900, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('button', { name: 'Cancel broadcast', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: '/tmp/duckterm-priority-implemented-narrow.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: 'Collapse Agents panel' }).click();
    await page.getByRole('button', { name: 'Cancel broadcast', exact: true }).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: '/tmp/duckterm-priority-implemented-mobile.png' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    const answer = await fetch(`${base()}/api/v1/session/questions/${inboxes[0][0].id}/answer`, { method: 'POST', headers: { Authorization: `Bearer ${credentials[0]}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ text: 'Check complete; summary saved.' }) });
    expect(answer.status).toBe(200);
    await expect(page.getByText('acknowledged', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Cancel broadcast', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Cancel this broadcast?' });
    await expect(dialog).toContainText('cannot retract text');
    await dialog.getByRole('button', { name: 'Cancel broadcast', exact: true }).click();
    await expect(page.getByText('cancelled', { exact: true })).toBeVisible();
    await expect(page.getByText('acknowledged', { exact: true })).toBeVisible();
    await page.route('**/broadcasts/**', route => route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'broadcast not found' }) }));
    await page.reload(); await openFolder();
    await expect(page.getByText('Status history is no longer available. Saved messages and last known states are kept.')).toBeVisible();
    await expect(page.getByText('acknowledged', { exact: true })).toBeVisible();
    await expect(page.getByText('cancelled', { exact: true })).toBeVisible();
    await expect(page.locator('.rd-folder-question p')).toHaveText(exact);
  } finally {
    for (const key of keys) await apiDelete(`/sessions/${key}`);
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
  }
});
