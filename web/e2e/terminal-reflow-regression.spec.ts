import { expect, test } from '@playwright/test';
import { apiPost, apiDelete, base } from './helpers';

// Prose must retain soft-wrap boundaries when replayed, not just resize the PTY.
// Cat/draft tests alone miss snapshots that permanently break paragraphs apart.
test('terminal prose reflows after panel resize and reconnect without splitting off fragments', async ({page}) => {
  let columns = 0;
  page.on('websocket', ws => ws.on('framesent', ({payload}) => {
    if (typeof payload !== 'string') return;
    try { const data = JSON.parse(payload); if (data.resize) columns = data.resize.cols; } catch { /* Input is binary. */ }
  }));
  const content = 'The terminal should preserve a readable conversation across panel changes. '.repeat(5);
  const code = `import sys; print(${JSON.stringify(content)}); print('REFLOW_READY',flush=True); [print(line.rstrip(),flush=True) for line in sys.stdin]`;
  const result = await apiPost('/sessions/launch', {command:"python3 -u -c '"+code.replaceAll("'", "'\\''")+"'",cwd:'/tmp',name:'Reflow regression',in_terminal:false,test:true});
  expect(result.status).toBe(200); const key=String(result.body.session_key);
  try {
    await page.setViewportSize({width:1800,height:1000});await page.goto(base());await page.locator('.rd-row-name',{hasText:'Reflow regression'}).click();
    const rows=page.locator('.rd-terminal-slot:visible .xterm-rows');await expect(rows).toContainText('REFLOW_READY');
    const check = async () => {
      await expect.poll(() => columns).toBeGreaterThan(0);
      await expect.poll(async () => (await rows.locator(':scope > div').first().textContent())?.trimEnd()).toBe(content.slice(0,columns).trimEnd());
    };
    await check(); const initialCols=columns;
    await page.getByRole('button',{name:'Collapse Context panel',exact:true}).click();
    await expect.poll(() => columns).toBeGreaterThan(initialCols);await check();
    await page.getByRole('button',{name:'Show Context panel',exact:true}).click();
    await expect.poll(() => columns).toBe(initialCols);await check();
    await page.setViewportSize({width:1760,height:1000});
    await expect.poll(() => columns).toBeLessThan(initialCols);await check();
    await page.screenshot({path:'/tmp/terminal-reflow-fixed.png'});
    await page.reload();await expect(rows).toContainText('REFLOW_READY');await check();
    await page.getByRole('button',{name:'Collapse Context panel',exact:true}).click();
    await expect.poll(() => columns).toBeGreaterThan(initialCols);await check();
    await page.locator('.rd-terminal-slot:visible .xterm-helper-textarea').focus();
    await page.keyboard.type('INPUT_AFTER_REFLOW');await page.keyboard.press('Enter');
    await expect(rows).toContainText('INPUT_AFTER_REFLOW');
  } finally {await apiPost(`/sessions/${key}/stop`);await apiDelete(`/sessions/${key}`);}
});
