import { expect, test, type Page } from '@playwright/test';
import { base } from './helpers';

// Oracle voice mode in a real browser, with speech stubbed so what would be
// said is recorded instead. Adapted from main-qa's PR #156 reproducers.

async function stubSpeech(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { spoken: string[] } & Record<string, unknown>;
    w.spoken = [];
    w.SpeechSynthesisUtterance = class { text: string; onend?: () => void; constructor(t: string) { this.text = t; } };
    Object.defineProperty(window, 'speechSynthesis', {
      value: { speak: (u: { text: string; onend?: () => void }) => { w.spoken.push(u.text); u.onend?.(); }, cancel: () => {} },
      configurable: true,
    });
  });
}

// An open question note that already existed before the page loaded,
// delivered late, the way a slow first /relay response arrives.
async function relayWithBacklog(page: Page) {
  await page.route('**/relay', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await route.fulfill({ json: { notes: [{ id: 'old-voice-e2e', session_key: 'old-fixture', name: 'Existing backlog', folder: 'QA', runtime: 'codex', kind: 'question', status: 'open', created_at: 1, urgency: 'blocked' }], rules: [], open: 1 } });
  });
}

const spoken = (page: Page) => page.evaluate(() => (window as unknown as { spoken: string[] }).spoken);

test('voice: notes already open when the page loads are not read out', async ({ page }) => {
  await stubSpeech(page);
  await relayWithBacklog(page);
  await page.goto(base());
  await expect(page.getByLabel('Voice announcements').first()).toHaveValue('done');
  await page.waitForTimeout(2400);
  expect(await spoken(page)).toEqual([]);
});

test('voice: turning it on later does not read out the backlog either', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('rd.voice', 'off'));
  await stubSpeech(page);
  await relayWithBacklog(page);
  await page.goto(base());
  await page.getByLabel('Voice announcements').first().selectOption('done');
  await page.waitForTimeout(2400);
  expect(await spoken(page)).toEqual(['Voice announcements on']);
});

test('voice: off stops relay polling and survives a reload', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('rd.voice', 'off'));
  let calls = 0;
  await page.route('**/relay', (route) => { calls++; return route.fulfill({ json: { notes: [], rules: [], open: 0 } }); });
  await page.goto(base());
  await expect(page.getByLabel('Voice announcements').first()).toHaveValue('off');
  await page.waitForTimeout(4400);
  expect(calls).toBe(0);
  await page.reload();
  await expect(page.getByLabel('Voice announcements').first()).toHaveValue('off');
});
