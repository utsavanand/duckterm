import { expect, test } from '@playwright/test';
import { apiPost, apiDelete, base } from './helpers';

// Oracle's control tower is a layer over the panes: the terminal underneath keeps
// its size, wrapping, and a half-typed draft, and stray keys don't reach it.
test('Oracle open and close restores terminal wrapping and keeps input usable', async ({ page }) => {
  await page.setViewportSize({width:1800,height:1000});
  const sizes: {cols:number;rows:number}[]=[];
  page.on('websocket',socket=>socket.on('framesent',({payload})=>{if(typeof payload==='string'){try{const data=JSON.parse(payload);if(data.resize)sizes.push(data.resize);}catch{/* Binary terminal input is not a resize control frame. */}}}));
  const r=await apiPost('/sessions/launch',{command:"python3 -u -c 'import sys; print(\"ORACLE_RESIZE_START\"); [print(str(i)+\" \"+\"abcdefghij\"*14+\" END_\"+str(i)) for i in range(12)]; print(\"ORACLE_RESIZE_READY\"); [print(line.rstrip()) for line in sys.stdin]'",cwd:'/tmp',name:'oracle-resize-check',in_terminal:false,test:true});
  expect(r.status).toBe(200);const key=String(r.body.session_key);
  try{
    await page.goto(base());await page.locator('.rd-row-name',{hasText:'oracle-resize-check'}).click();
    const rows=page.locator('.rd-terminal-slot:visible .xterm-rows');
    await expect(rows).toContainText('ORACLE_RESIZE_READY');
    await page.waitForTimeout(400);
    await page.locator('.rd-terminal-slot:visible .xterm-helper-textarea').focus();
    await page.keyboard.type('DRAFT_' + '0123456789'.repeat(10) + '_END');
    await expect(rows).toContainText('_END');
    const original=await rows.innerText();const size=sizes.at(-1)!;
    for(let i=0;i<3;i++){
      await page.getByRole('button',{name:'Oracle',exact:true}).click();
      await expect(page.getByLabel('Message Oracle')).toBeVisible();
      await expect.poll(()=>sizes.at(-1)).toEqual(size);
      await expect.poll(() => rows.innerText()).toBe(original);
      // The panes under the tower are inert: stray keys must not reach the terminal.
      await page.keyboard.type('STRAY');
      await page.getByRole('button',{name:'← Sessions',exact:true}).click();
      await expect.poll(()=>sizes.at(-1)).toEqual(size);
      await expect.poll(() => rows.innerText()).toBe(original);
    }
    await page.locator('.rd-terminal-slot:visible .xterm-helper-textarea').focus();await page.keyboard.type('INPUT_STILL_WORKS');await page.keyboard.press('Enter');await expect(rows).toContainText('INPUT_STILL_WORKS');
  }finally{await apiPost(`/sessions/${key}/stop`);await apiDelete(`/sessions/${key}`);}
});
