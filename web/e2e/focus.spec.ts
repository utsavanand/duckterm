import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, postEvent } from "./helpers";

test("Focus pins: cap, live input, saved layout, stopped sessions and unpin", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({width:1440,height:1000});
  const keys: string[] = [];
  try {
    for (const name of ["focus-a", "focus-b", "focus-c", "focus-d"]) {
      const r = await apiPost("/sessions/launch", {command:`sh -c 'echo READY_${name}; exec cat'`, cwd:"/tmp", name, in_terminal:false, test:true});
      expect(r.status).toBe(200); keys.push(r.body.session_key as string);
    }
    await page.goto(base());
    await expect(page.getByRole("button", {name:"Focus · 0",exact:true})).toBeDisabled();
    for (const name of ["focus-a", "focus-b", "focus-c"]) {
      await page.locator(".rd-agents").getByRole("button", {name:`Pin ${name}`,exact:true}).click();
      await expect(page.locator(".rd-agents").getByRole("button", {name:`Unpin ${name}`,exact:true})).toHaveAttribute("aria-pressed","true");
    }
    await page.locator(".rd-agents").getByRole("button", {name:"Pin focus-d",exact:true}).click();
    await expect(page.getByText("Unpin one first", {exact:true})).toBeVisible();
    await expect(page.getByRole("button", {name:"Focus · 3",exact:true})).toBeEnabled();
    await page.locator(".rd-row-name",{hasText:"focus-a"}).click();
    await expect(page.locator(".rd-context-pane").getByRole("button",{name:"Unpin focus-a",exact:true})).toBeVisible();
    await page.screenshot({path:"/tmp/duckterm-focus-implemented-sessions.png"});
    await page.getByRole("button", {name:"Focus · 3",exact:true}).click();
    const tile = (name:string) => page.locator(".rd-grid-tile",{has:page.locator(".rd-grid-tile-name",{hasText:name})});
    await expect(page.locator(".rd-grid-tile")).toHaveCount(3);
    for (const name of ["focus-a","focus-b","focus-c"]) {
      await expect(tile(name).locator(".xterm-rows")).toContainText(`READY_${name}`);
      await tile(name).locator(".xterm-helper-textarea").focus();
      await page.keyboard.type(`INPUT_${name}`); await page.keyboard.press("Enter");
      await expect(tile(name).locator(".xterm-rows")).toContainText(`INPUT_${name}`);
    }
    await page.screenshot({path:"/tmp/duckterm-focus-implemented-grid.png"});
    await page.locator(".rd-grid-cols").selectOption("1");
    await tile("focus-c").locator(".rd-grid-tile-collapse").click();
    await expect(page.locator(".rd-grid-dock-chip",{hasText:"focus-c"})).toBeVisible();
    await page.reload();
    await page.getByRole("button", {name:"Focus · 3",exact:true}).click();
    await expect(page.locator(".rd-grid-tile")).toHaveCount(2);
    await expect(page.locator(".rd-grid-split.col")).toBeVisible();
    await expect(page.locator(".rd-grid-dock-chip",{hasText:"focus-c"})).toBeVisible();
    await page.locator(".rd-grid-dock-chip",{hasText:"focus-c"}).click();
    await postEvent({session_key:keys[1], event_type:"SessionEnd", lifecycle:"stopped"});
    await expect(tile("focus-b")).toContainText("is stopped. Its pin is saved.");
    await postEvent({session_key:keys[2], event_type:"SessionEnd", lifecycle:"archived"});
    await expect(tile("focus-c")).toContainText("is archived. Its pin is saved.");
    await expect(page.getByRole("button", {name:"Focus · 3",exact:true})).toBeEnabled();
    for (const name of ["focus-a","focus-b","focus-c"]) {
      await page.locator(".rd-grid").getByRole("button",{name:`Unpin ${name}`,exact:true}).click();
    }
    await expect(page.locator(".rd-grid")).toHaveCount(0);
    await expect(page.getByRole("button", {name:"Focus · 0",exact:true})).toBeDisabled();
  } finally {
    for (const key of keys) await apiDelete(`/sessions/${key}`);
  }
});
