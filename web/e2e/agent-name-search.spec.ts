import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, expandFolder, postEvent, seedSession } from "./helpers";

test("agent-name search combines filters, highlights matches, and restores workspace navigation", async ({ page }) => {
  const keys = ["name-search-ui", "name-search-qa", "name-search-docs"];
  const folders = ["Search review", "Search review/QA", "dev workspace"];
  try {
    for (const name of folders) await apiPost("/folders", { name });
    await seedSession(keys[0], { name: "UI-dev", group: folders[0], runtime: "codex", test: true });
    await seedSession(keys[1], { name: "Main-QA", group: folders[1], runtime: "claude-code", test: true });
    await postEvent({ session_key: keys[1], event_type: "PermissionRequest", runtime: "claude-code" });
    await seedSession(keys[2], { name: "Docs", group: folders[2], runtime: "codex", test: true });
    await page.setViewportSize({ width: 1440, height: 950 });
    await page.goto(base());
    await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    await expandFolder(page, folders[0]);
    await page.locator('.rd-row-name', { hasText: 'UI-dev' }).click();
    const search = page.getByRole('searchbox', { name: 'Search agents by name' });
    const toggle = page.getByRole('button', { name: /^Filters(?:,|$)/ });
    const rows = page.locator('.rd-agents .rd-row');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await search.fill(' DEV ');
    await expect(rows).toHaveCount(1);
    await expect(rows.locator('mark')).toHaveText('dev');
    await expect(rows).toContainText('UI-dev');
    await toggle.click();
    await expect(page.getByRole('button', { name: 'Working 1', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Waiting 0', exact: true }).click();
    await expect(rows).toHaveCount(0);
    await expect(page.getByText('No agents match “DEV” with these filters.')).toBeVisible();
    await search.fill('qa');
    await expect(rows).toHaveCount(1);
    await expect(rows.locator('mark')).toHaveText('QA');
    await expect(rows.locator('.rd-row-folder')).toContainText(folders[1]);
    await toggle.click();
    await expect(search).toBeVisible();
    await search.press('Escape');
    await expect(search).toHaveValue('');
    await expect(rows).toHaveCount(1);
    await expect(toggle).toHaveAccessibleName('Filters, 1 active');
    await search.press('Escape');
    await expect(toggle).toHaveAccessibleName('Filters');
    await expect(toggle).toBeFocused();
    await expect(page.getByRole('button', { name: `Collapse ${folders[0]}`, exact: true })).toBeVisible();
    // Reapply Waiting to exercise the separate Clear filters action below.
    await toggle.click();
    await page.getByRole('button', { name: 'Waiting 1', exact: true }).click();
    await search.fill('main');
    await page.getByRole('region', { name: 'Session filters' }).getByRole('button', { name: 'Clear filters' }).click();
    await expect(search).toHaveValue('main');
    await expect(rows).toHaveCount(1);
    await page.getByRole('search', { name: 'Find agents' }).getByRole('button', { name: 'Clear search' }).click();
    await expect(search).toBeFocused();
    await expect(page.getByRole('button', { name: `Collapse ${folders[0]}`, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: `Expand ${folders[1]}`, exact: true })).toBeVisible();
    await expect(page.locator('.rd-row.selected')).toContainText('UI-dev');
    await search.fill('qa');
    await toggle.click();
    await page.screenshot({ path: '/tmp/agent-search-implemented-dark.png' });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; document.documentElement.style.setProperty('--ui-scale', '1.5'); });
    await page.screenshot({ path: '/tmp/agent-search-implemented-light-large.png' });
    const bounds = await page.locator('.rd-agent-search').evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth }));
    expect(bounds.scroll).toBeLessThanOrEqual(bounds.width);
    await search.fill('');
    await search.press('Escape');
    await expect(search).toBeFocused();
  } finally {
    for (const key of keys) await apiDelete(`/sessions/${key}`);
    for (const folder of folders.reverse()) await apiDelete(`/folders/${encodeURIComponent(folder)}`);
  }
});

test('name search intersects unanswered inbox and updates counts at phone width', async ({ page }) => {
  const folder = 'Search inbox review';
  const keys = ['search-inbox-ui', 'search-inbox-qa', 'search-inbox-docs'];
  try {
    for (const [i, name] of ['UI-dev', 'QA-dev', 'Docs'].entries()) {
      await seedSession(keys[i], { name, group: folder, runtime: 'codex', test: true });
    }
    const sender = await apiPost(`/sessions/${keys[1]}/collaboration`, { root: folder });
    for (const key of [keys[0], keys[2]]) {
      await apiPost(`/sessions/${key}/collaboration`, { root: folder });
      const response = await fetch(`${base()}/api/v1/session/questions`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${sender.body.token}`, 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify({ target_session_id: key, question: 'Synthetic review request' }),
      });
      expect(response.status).toBe(202);
    }
    await page.setViewportSize({ width: 1440, height: 950 });
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto(base());
    const search = page.getByRole('searchbox', { name: 'Search agents by name' });
    const rows = page.locator('.rd-agents .rd-row');
    await search.fill('DEV');
    await expect(rows).toHaveCount(2);
    await page.getByRole('button', { name: /^Filters(?:,|$)/ }).click();
    await page.getByRole('button', { name: 'Unanswered inbox 1', exact: true }).click();
    await expect(rows).toHaveCount(1);
    await expect(rows).toContainText('UI-dev');
    await expect(rows.locator('mark')).toHaveText('dev');
    await page.setViewportSize({ width: 390, height: 844 });
    await search.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    await search.fill('Docs');
    await expect(rows).toHaveCount(1);
    await expect(rows).toContainText('Docs');
    await search.fill('QA');
    await expect(rows).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Unanswered inbox 0', exact: true })).toHaveCount(1);
    await page.locator('.rd-filter-empty').getByRole('button', { name: 'Clear filters' }).click();
    await expect(rows).toHaveCount(1);
    await expect(search).toHaveValue('QA');
    await page.getByRole('search', { name: 'Find agents' }).getByRole('button', { name: 'Clear search' }).click();
    await expect(search).toHaveValue('');
    await expect(search).toBeFocused();
  } finally {
    for (const key of keys) await apiDelete(`/sessions/${key}`);
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
  }
});
