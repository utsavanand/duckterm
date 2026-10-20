import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, apiPatch, base, postEvent, expandFolder } from "./helpers";

test("Focus pins: cap, live input, top-three entry, stopped sessions and unpin", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({width:1440,height:1000});
  await page.emulateMedia({colorScheme:"dark"});
  const keys: string[] = [];
  try {
    for (const name of ["focus-a", "focus-b", "focus-c", "focus-d"]) {
      const r = await apiPost("/sessions/launch", {command:`sh -c 'echo READY_${name}; exec cat'`, cwd:"/tmp", name, in_terminal:false, test:true});
      expect(r.status).toBe(200); keys.push(r.body.session_key as string);
    }
    await page.goto(base());
    await expect(page.getByRole("button", {name:"Focus · 0",exact:true})).toBeDisabled();
    await page.locator(".rd-row-name", {hasText: "focus-d"}).click();
    await page.getByRole("button", {name:"Collapse Context panel",exact:true}).click();
    for (const name of ["focus-a", "focus-b", "focus-c"]) {
      await page.locator(".rd-agents").getByRole("button", {name:`Pin ${name}`,exact:true}).click();
      await expect(page.locator(".rd-agents").getByRole("button", {name:`Unpin ${name}`,exact:true})).toHaveAttribute("aria-pressed","true");
      await expect(page.locator(".rd-row.selected .rd-row-name")).toHaveText("focus-d");
    }
    await page.locator(".rd-row-name", {hasText: "focus-d"}).click();
    await page.locator(".rd-agents").getByRole("button", {name:"Pin focus-d",exact:true}).click();
    await expect(page.locator(".rd-agents").getByRole("button", {name:"Unpin focus-d",exact:true})).toBeVisible();
    await expect(page.getByRole("button", {name:"Focus · 4",exact:true})).toBeEnabled();
    await page.locator(".rd-agents").getByRole("button", {name:"Unpin focus-d",exact:true}).click();
    await expect(page.getByRole("button", {name:"Focus · 3",exact:true})).toBeEnabled();
    await page.locator(".rd-row-name",{hasText:"focus-a"}).click();
    await expect(page.locator(".rd-agents").getByRole("button",{name:"Unpin focus-a",exact:true})).toBeVisible();
    for (const density of ["compact", "standard", "relaxed"]) {
      await page.getByRole("button", {name:"Settings",exact:true}).click();
      await page.getByRole("combobox", {name:"Sidebar density"}).selectOption(density);
      await page.keyboard.press("Escape");
      const pin = page.locator(".rd-agents").getByRole("button", {name:"Unpin focus-a",exact:true});
      await expect(pin).toBeVisible();
      const box = (await pin.boundingBox())!, sidebar = (await page.locator(".rd-agents").boundingBox())!;
      expect(box.x + box.width).toBeLessThanOrEqual(sidebar.x + sidebar.width);
      await page.screenshot({path:`/tmp/sidebar-session-pin-${density}.png`});
    }
    await page.emulateMedia({colorScheme:"light"});
    await page.screenshot({path:"/tmp/sidebar-session-pin-light.png"});
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
    // Each Focus entry starts with the top three pins, not the previous collapsed layout.
    await expect(page.locator(".rd-grid-tile")).toHaveCount(3);
    await expect(page.locator(".rd-grid-dock-chip",{hasText:"focus-c"})).toHaveCount(0);
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

test("eight pins open the ordered top three and leaving Focus restores the sidebar and selected view", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 700 });
  const keys: string[] = [], folders = ["Focus project", "Focus project/Nested", "Focus other", "Focus closed", ...Array.from({ length: 12 }, (_, i) => `Focus spare ${i}`)];
  try {
    for (const name of folders) expect((await apiPost("/folders", { name })).status).toBe(200);
    for (let i = 0; i < 8; i++) {
      const result = await apiPost("/sessions/launch", { command: `sh -c 'echo READY_order-${i}; exec cat'`, cwd: "/tmp", name: `order-${i}`, in_terminal: false, test: true });
      expect(result.status).toBe(200); const key = String(result.body.session_key); keys.push(key);
      expect((await apiPatch(`/sessions/${key}`, { group: i < 4 ? "Focus project/Nested" : "Focus other" })).status).toBe(200);
    }
    await page.goto(base());
    await expandFolder(page, "Focus project/Nested"); await expandFolder(page, "Focus other");
    await page.locator(".rd-row-name", { hasText: "order-0" }).click();
    for (let i = 0; i < 8; i++) await page.locator(".rd-agents").getByRole("button", { name: `Pin order-${i}`, exact: true }).click();
    const rail = page.getByRole("navigation", { name: "Pinned session shortcuts" });
    await rail.getByRole("button", { name: "Choose sessions to pin" }).hover();
    await rail.getByRole("combobox", { name: "Shortcut display" }).selectOption("expanded");
    const grip = rail.getByRole("button", { name: "Reorder Focus other / order-7", exact: true });
    await grip.focus(); for (let i = 0; i < 7; i++) await page.keyboard.press("Alt+ArrowUp");
    const expected = (await rail.locator(".rd-shortcut-session").allTextContents()).slice(0, 3);
    expect(expected[0]).toBe("order-7");
    // An old eight-pane Focus layout must not override the new entry rule.
    await page.evaluate(ids => localStorage.setItem("rd.grid.focus", JSON.stringify({ tree: { type: "split", dir: "row", children: ids.map(key => ({ type: "leaf", key })), weights: ids.map(() => 1) }, docked: [] })), keys);
    await page.getByRole("button", { name: "Messages", exact: true }).click();
    const sidebar = page.locator(".rd-tree");
    await sidebar.evaluate(el => { el.scrollTop = 160; });
    await expect.poll(() => sidebar.evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    const scrollBefore = await sidebar.evaluate(el => el.scrollTop);
    await page.getByRole("button", { name: "Focus · 8", exact: true }).click();
    await expect(page.locator(".rd-grid-tile-name")).toHaveText(expected);
    await expect(page.locator(".rd-grid-dock-chip")).toHaveCount(5);
    await page.locator(".rd-grid-dock-chip").first().click();
    await expect(page.locator(".rd-grid-tile")).toHaveCount(4);
    await page.getByRole("button", { name: "← Sessions (esc)", exact: true }).click();
    for (const name of ["Focus project", "Focus project/Nested", "Focus other"]) await expect(page.getByRole("button", { name: `Collapse ${name}`, exact: true })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("button", { name: "Expand Focus closed", exact: true })).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator(".rd-row.selected .rd-row-name")).toHaveText("order-0");
    await expect(page.getByRole("button", { name: "Messages", exact: true })).toHaveClass(/active/);
    await expect.poll(() => sidebar.evaluate(el => el.scrollTop)).toBe(scrollBefore);
    await page.getByRole("button", { name: "Focus · 8", exact: true }).click();
    await expect(page.locator(".rd-grid-tile-name")).toHaveText(expected);
    await expect(page.locator(".rd-grid-dock-chip")).toHaveCount(5);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Collapse Focus project/Nested", exact: true })).toBeVisible();
    // A fresh dashboard load still honors the existing collapsed-by-default rule.
    await page.reload();
    await expect(page.getByRole("button", { name: "Expand Focus project", exact: true })).toHaveAttribute("aria-expanded", "false");
  } finally {
    for (const key of keys) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    for (const name of folders.reverse()) await apiDelete(`/folders/${encodeURIComponent(name)}`);
  }
});
