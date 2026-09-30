import { expect, test } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base } from "./helpers";

test("folder mentions deliver owner questions, retain replies, and review broadcasts", async ({ page }) => {
  test.setTimeout(60_000);
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-mentions-")));
  const folder = `Messages-${Date.now()}`;
  const keys: string[] = [], credentials: string[] = [];
  try {
    for (const [name, group] of [["ui-dev", folder], ["research", folder + "/Research"]]) {
      const launched = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd, runtime: "generic", name, in_terminal: false, test: true });
      expect(launched.status).toBe(200);
      const key = String(launched.body.session_key); keys.push(key);
      await apiPatch(`/sessions/${key}`, { group });
      const enrolled = await apiPost(`/sessions/${key}/collaboration`, { root: folder });
      credentials.push(String(enrolled.body.token));
    }
    const header = () => page.locator('.rd-group-head').filter({ has: page.getByRole('button', { name: `View interactions in ${folder}`, exact: true }) }).locator('.rd-group-name');
    const inbox = async (index: number) => (await fetch(`${base()}/api/v1/session/inbox`, { headers: { Authorization: `Bearer ${credentials[index]}` } })).json();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto(base()); await header().click();
    const input = page.getByRole('textbox', { name: 'Ask this folder' });
    await input.fill('@');
    await expect(page.getByRole('option', { name: /ui-dev/ })).toBeVisible();
    await page.screenshot({ path: '/tmp/duckterm-mentions-implemented.png' });
    await input.press('ArrowDown');
    await page.getByRole('option', { name: /ui-dev/ }).click();
    await input.fill('Verify keyboard navigation.');
    await page.getByRole('button', { name: 'Send to ui-dev', exact: true }).click();
    await expect(page.getByText('ui-dev · Queued in inbox', { exact: true })).toBeVisible();
    const items = await inbox(0);
    expect(items.messages).toHaveLength(1);
    expect(items.messages[0]).toMatchObject({ sender_kind: 'owner', requires_reply: true, question: 'Verify keyboard navigation.' });
    const answer = await fetch(`${base()}/api/v1/session/questions/${items.messages[0].id}/answer`, { method: 'POST', headers: { Authorization: `Bearer ${credentials[0]}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ text: 'Keyboard navigation verified. All paths passed.' }) });
    expect(answer.status).toBe(200);
    await expect(page.locator('.rd-folder-answer')).toContainText('Keyboard navigation verified. All paths passed.');
    await page.reload(); await header().click();
    await expect(page.locator('.rd-folder-answer')).toContainText('Keyboard navigation verified. All paths passed.');
    await input.fill('@Research');
    await page.getByRole('option', { name: /Research/ }).click();
    await input.fill('Please review sources.');
    await page.getByRole('button', { name: 'Review recipients', exact: true }).click();
    await expect(page.getByRole('dialog', { name: 'Review recipients' })).toContainText('research');
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(input).toHaveValue('Please review sources.');
    await page.getByRole('button', { name: 'Review recipients', exact: true }).click();
    await page.getByRole('button', { name: 'Send to 1 sessions', exact: true }).click();
    await expect(page.getByText('research · Queued in inbox', { exact: true })).toBeVisible();
    expect((await inbox(1)).messages[0]).toMatchObject({ sender_kind: 'owner', requires_reply: false, question: 'Please review sources.' });
    await page.emulateMedia({ colorScheme: 'light' });
    await page.screenshot({ path: '/tmp/duckterm-mentions-light.png' });
    await page.setViewportSize({ width: 900, height: 900 });
    await input.fill('@');
    await expect(page.getByRole('option', { name: /ui-dev/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: '/tmp/duckterm-mentions-narrow.png' });
  } finally {
    for (const key of keys) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
    rmSync(cwd, { recursive: true, force: true });
  }
});
