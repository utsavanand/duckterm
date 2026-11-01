import { expect, test, type Locator, type Page } from "@playwright/test";
import { apiDelete, apiPost, base, expandFolder, seedSession } from "./helpers";

async function drag(page: Page, source: Locator, target: Locator, after = false, cancel = false) {
  await source.scrollIntoViewIfNeeded();
  // scrollIntoViewIfNeeded can leave the target at the tree's edge, inside the
  // 28px auto-scroll band, so the list scrolls away under the pointer mid-drag.
  await target.evaluate(el => el.scrollIntoView({ block: "center" }));
  const from = await source.boundingBox(), to = await target.boundingBox();
  if (!from || !to) throw new Error("Missing drag geometry");
  await page.mouse.move(from.x + 100, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + 100, to.y + (after ? to.height - 3 : 3), { steps: 12 });
  if (cancel) await page.keyboard.press("Escape");
  await page.mouse.up();
}
test("session order survives reload, preserves forks and selection, and still allows folder moves", async ({ page }) => {
  const folder = "Order preview", other = "Order destination";
  const keys = ["order-a", "order-b", "order-child", "order-sibling"];
  const row = (key: string) => page.locator(`.rd-row[data-session-key="${key}"]`);
  const names = () => page.locator('.rd-row[data-session-key^="order-"]').evaluateAll(rows => rows.map(r => r.getAttribute('data-session-key')));
  try {
    await apiPost('/folders', { name: folder }); await apiPost('/folders', { name: other });
    await seedSession(keys[0], { name: "First session", group: folder });
    await seedSession(keys[1], { name: "Second session", group: folder });
    await seedSession(keys[2], { name: "First fork", group: folder, parent_session_key: keys[0] });
    await seedSession(keys[3], { name: "Second fork", group: folder, parent_session_key: keys[0] });
    await page.addInitScript(order => { if (!localStorage.getItem('rd.sidebarSessionOrder.v1')) localStorage.setItem('rd.sidebarSessionOrder.v1', JSON.stringify(order)); }, keys);
    await page.goto(base()); await expandFolder(page, folder);
    await row(keys[0]).locator('.rd-row-name').click();
    await drag(page, row(keys[1]), row(keys[0]));
    await expect.poll(names).toEqual([keys[1],keys[0],keys[2],keys[3]]);
    await expect(row(keys[0])).toHaveClass(/selected/);
    await page.reload(); await expandFolder(page, folder);
    await expect.poll(names).toEqual([keys[1],keys[0],keys[2],keys[3]]);
    await drag(page,row(keys[3]),row(keys[2]));
    await expect.poll(names).toEqual([keys[1],keys[0],keys[3],keys[2]]);
    // A fork cannot be detached by dragging onto a root session.
    await drag(page,row(keys[2]),row(keys[1]));
    await expect.poll(names).toEqual([keys[1],keys[0],keys[3],keys[2]]);
    await drag(page,row(keys[0]),row(keys[1]),false,true);
    await expect.poll(names).toEqual([keys[1],keys[0],keys[3],keys[2]]);
    await row(keys[0]).focus(); await page.keyboard.press('Alt+ArrowUp');
    await expect.poll(names).toEqual([keys[0],keys[3],keys[2],keys[1]]);
    await page.screenshot({path:'/tmp/sidebar-order-implemented-dark.png'});
    await page.emulateMedia({colorScheme:'light'});
    await page.screenshot({path:'/tmp/sidebar-order-implemented-light.png'});
    const header=page.locator(`[data-session-drop-group="${other}"]`);
    await drag(page,row(keys[1]),header);
    await expandFolder(page,other);
    await expect(header.locator('..').locator(`[data-session-key="${keys[1]}"]`)).toBeVisible();
    await expect(row(keys[0])).toHaveClass(/selected/);
  } finally {
    for(const key of keys.reverse()) await apiDelete(`/sessions/${key}`);
    for(const f of [folder,other]) await apiDelete(`/folders/${encodeURIComponent(f)}`);
  }
});
