import { expect, test } from "@playwright/test";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPost, base } from "./helpers";

test("Messages Send starts a terminal turn, rather than leaving a pasted draft", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "duckterm-submit-test-"));
  const script = join(dir,"agent.py");
  // A real raw-mode PTY reader: report a new turn only on Enter outside paste.
  writeFileSync(script, `import os, tty\ntty.setraw(0)\nprint('READY', flush=True)\nbuf=b''\nwhile True:\n b=os.read(0,1)\n if b == b'\\r':\n  text=buf.replace(b'\\x1b[200~',b'').replace(b'\\x1b[201~',b'')\n  print('\\r\\nTURN_STARTED: '+text.decode()+'\\r\\n', flush=True)\n  buf=b''\n else: buf+=b\n`);
  let key = "";
  try {
    const launch = await apiPost("/sessions/launch", {command:`python3 -u ${script}`,cwd:dir,name:"submit-proof",test:true,in_terminal:false});
    expect(launch.status).toBe(200); key=String(launch.body.session_key);
    await page.route(`**/sessions/${key}/messages`, route => route.fulfill({json:{messages:[{id:1,role:"assistant",blocks:[{type:"text",text:"Ready for a follow-up."}]}]}}));
    await page.goto(base());
    await page.locator(".rd-row-name",{hasText:"submit-proof"}).click();
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("READY");
    await page.locator(".rd-view-toggle button",{hasText:"Messages"}).click();
    await page.getByRole("textbox",{name:"Follow-up message"}).fill("start this next turn");
    await page.getByRole("button",{name:"Send",exact:true}).click();
    await expect(page.getByRole("textbox",{name:"Follow-up message"})).toHaveValue("");
    await page.locator(".rd-view-toggle button",{hasText:"Terminal"}).click();
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("TURN_STARTED: start this next turn");
  } finally {
    if(key) await apiDelete(`/sessions/${key}`);
    rmSync(dir,{recursive:true,force:true});
  }
});
