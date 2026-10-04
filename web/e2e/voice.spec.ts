import { expect, test, type Page } from '@playwright/test';
import { apiDelete, base, postEvent, seedSession } from './helpers';

// Oracle voice mode in a real browser. The natural voice is stubbed as
// installed, and every line Oracle asks it to speak (POST /voice/say) is
// recorded. Adapted from main-qa's PR #156 reproducers.

const said = new WeakMap<Page, string[]>();

// A valid, silent 24 kHz WAV, so playback completes.
function silentWav(): Buffer {
  const samples = 2400;
  const b = Buffer.alloc(44 + samples * 2);
  b.write('RIFF', 0); b.writeUInt32LE(36 + samples * 2, 4); b.write('WAVE', 8);
  b.write('fmt ', 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(24000, 24); b.writeUInt32LE(48000, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write('data', 36); b.writeUInt32LE(samples * 2, 40);
  return b;
}

async function stubSpeech(page: Page, installed = true) {
  const lines: string[] = [];
  said.set(page, lines);
  await page.route('**/voice/status', (route) =>
    route.fulfill({ json: installed ? { state: 'ready', voices: [{ id: 'af_heart', label: 'Heart', accent: 'US' }] } : { state: 'absent', size: 'about 310 MB' } }),
  );
  await page.route('**/voice/warm', (route) => route.fulfill({ status: 202, json: { warming: true } }));
  await page.route('**/voice/say', async (route) => {
    lines.push((route.request().postDataJSON() as { text: string }).text);
    await route.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav() });
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

const spoken = async (page: Page) => said.get(page) ?? [];

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
  await stubSpeech(page);
  let calls = 0;
  await page.route('**/relay', (route) => { calls++; return route.fulfill({ json: { notes: [], rules: [], open: 0 } }); });
  await page.goto(base());
  await expect(page.getByLabel('Voice announcements').first()).toHaveValue('off');
  await page.waitForTimeout(4400);
  expect(calls).toBe(0);
  await page.reload();
  await expect(page.getByLabel('Voice announcements').first()).toHaveValue('off');
});

// main-qa's PR #156 reproducer: a session already waiting before the page opens.
test('voice: a session already waiting when the page loads is not read out', async ({ page }) => {
  const key = `voice-waiting-${Date.now()}`;
  await seedSession(key, { name: 'Old waiting fixture', runtime: 'codex' });
  await postEvent({ session_key: key, runtime: 'codex', event_type: 'Notification', notification_type: 'permission_prompt' });
  await stubSpeech(page);
  try {
    await page.goto(base());
    await expect(page.locator('.rd-row-click').filter({ hasText: 'Old waiting fixture' }).locator('.rd-state')).toHaveText('waiting');
    await page.waitForTimeout(2200);
    expect(await spoken(page)).toEqual([]);
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});

test('voice: stays off, saying why, until a natural voice is downloaded', async ({ page }) => {
  await stubSpeech(page, false);
  await page.goto(base());
  const indicator = page.getByRole('img', { name: 'Voice off — download a voice in Settings' });
  await expect(indicator).toBeVisible();
  await expect(indicator).toHaveAttribute('title', 'Voice off — download a voice in Settings');
  await page.waitForTimeout(1500);
  expect(await spoken(page)).toEqual([]);
});
