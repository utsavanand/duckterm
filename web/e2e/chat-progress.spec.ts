import { expect, test } from "@playwright/test";
import { mkdtempSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPatch, apiPost, base } from "./helpers";

test("chat shows real stages, elapsed seconds and saved activity after reload", async ({ page }) => {
  test.setTimeout(60_000);
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-chat-progress-")));
  const folder = `Chat progress ${Date.now()}`;
  let key = "";
  try {
    const launch = await apiPost("/sessions/launch", { command: "sh -c 'cat'", cwd, runtime: "generic", name: "Progress probe", in_terminal: false, test: true });
    expect(launch.status).toBe(200); key=String(launch.body.session_key);
    await apiPatch(`/sessions/${key}`, { group: folder });
    await page.setViewportSize({width:1280,height:850});
    await page.emulateMedia({colorScheme:"dark"});
    await page.goto(base());
    const openFolder = async () => {
      const header = page.locator(".rd-group-head").filter({ has: page.getByRole("button", { name: `View interactions in ${folder}`, exact: true }) });
      await header.locator(".rd-group-name").click();
    };
    await openFolder();
    await page.getByRole("textbox",{name:"Ask this workspace"}).fill("CHAT_PROGRESS_TIMING_PROBE: What needs attention?");
    await page.getByRole("button",{name:"Ask",exact:true}).click();
    const live = page.locator(".rd-folder-chat .rd-chat-progress").last();
    await expect(live.getByLabel("Elapsed time")).toHaveText(/^[1-9]\d*s$/);
    await live.getByText("Activity",{exact:true}).click();
    await page.screenshot({path:"/private/tmp/duckterm-chat-progress-running.png"});
    await expect(page.locator(".rd-folder-answer")).toContainText("Use rg",{timeout:20000});
    const saved=page.locator(".rd-folder-answer .rd-chat-progress");
    await expect(saved.getByRole("status")).toHaveText("Answered");
    await saved.getByText("Activity",{exact:true}).click();
    await expect(saved).toContainText("Reading saved memories and recent activity");
    await expect(saved).toContainText("Preparing your answer");
    const seconds=await saved.getByLabel("Elapsed time").textContent();
    expect(parseInt(seconds!)).toBeGreaterThanOrEqual(4);
    await saved.locator("li").last().scrollIntoViewIfNeeded();
    await page.screenshot({path:"/private/tmp/duckterm-chat-progress-complete.png"});
    await page.reload();await openFolder();
    await expect(page.locator(".rd-folder-answer .rd-chat-progress").getByLabel("Elapsed time")).toHaveText(seconds!);
    await page.emulateMedia({colorScheme:"light"});
    await page.setViewportSize({width:900,height:850});
    await page.locator(".rd-folder-answer .rd-chat-progress").getByText("Activity",{exact:true}).click();
    await page.locator(".rd-folder-answer .rd-chat-progress li").last().scrollIntoViewIfNeeded();
    await page.screenshot({path:"/private/tmp/duckterm-chat-progress-light-narrow.png"});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  } finally {
    if(key){await apiPost(`/sessions/${key}/stop`);await apiDelete(`/sessions/${key}`);}
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
    rmSync(cwd,{recursive:true,force:true});
  }
});
