import { expect, test } from "@playwright/test";
import { apiPatch, apiPost, base } from "./helpers";

// The grid opens from a FOLDER's ⛶ (scoped to it and its subfolders): tiles
// arrange 2D via the columns control, resize via the bars between tiles,
// collapse to a bottom dock, exit via the button (esc belongs to a focused
// terminal's agent).

async function launchCat(name: string): Promise<string> {
  const r = await apiPost("/sessions/launch", {
    command: `sh -c 'echo READY_${name}; exec cat'`,
    cwd: "/tmp",
    name,
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);
  return r.body.session_key as string;
}

test("folder grid: subtree tiles, resize, dock, exit", async ({ page }) => {
  const folder = `Fleet${Date.now()}`;
  await apiPost("/folders", { name: folder });
  await apiPost("/folders", { name: `${folder}/deep` });
  const a = await launchCat("gA");
  const b = await launchCat("gB");
  const c = await launchCat("gC");
  const d = await launchCat("gD");
  await launchCat("gOutside");
  // gA/gC/gD in the folder, gB in its SUBfolder (all must tile); gOutside not.
  await apiPatch(`/sessions/${a}`, { group: folder });
  await apiPatch(`/sessions/${b}`, { group: `${folder}/deep` });
  await apiPatch(`/sessions/${c}`, { group: folder });
  await apiPatch(`/sessions/${d}`, { group: folder });

  await page.goto(base());
  const head = page.locator(".rd-group-head", { hasText: folder });
  await expect(head).toBeVisible();
  await head.locator(".rd-group-grid").click();

  // Default: vertical sections with at most 3 expanded — the 4th starts in
  // the dock, out-of-scope sessions nowhere.
  const tileFor = (n: string) => page.locator(".rd-grid-tile", { hasText: n });
  await expect(page.locator(".rd-grid-tile")).toHaveCount(3, {
    timeout: 5_000,
  });
  await expect(page.locator(".rd-grid-dock-chip")).toHaveCount(1);
  await expect(tileFor("gOutside")).toHaveCount(0); // out of scope

  // The terminals must have REAL height (a zero-height regression once passed
  // text assertions while rendering a black void).
  const h = await page
    .locator(".rd-grid-tile-term")
    .first()
    .evaluate((el) => el.clientHeight);
  expect(h).toBeGreaterThan(150);

  // Expand the docked one: all four tile (gA is now guaranteed visible).
  await page.locator(".rd-grid-dock-chip").click();
  await expect(page.locator(".rd-grid-tile")).toHaveCount(4);
  await expect(tileFor("gA")).toBeVisible();
  await expect(tileFor("gB")).toBeVisible(); // subfolder session included

  // 2 columns, then drag the vertical bar to resize.
  await page.locator(".rd-grid-cols").selectOption("2");
  const tilesBox = page.locator(".rd-grid-tiles");
  const before = await tilesBox.evaluate(
    (el) => getComputedStyle(el).gridTemplateColumns,
  );
  const bar = page.locator(".rd-grid-split-v").first();
  const box = await bar.boundingBox();
  if (!box) throw new Error("no resize bar");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 160, box.y + box.height / 2, { steps: 4 });
  await page.mouse.up();
  const after = await tilesBox.evaluate(
    (el) => getComputedStyle(el).gridTemplateColumns,
  );
  expect(after).not.toBe(before); // proportions actually changed

  // Collapse to the dock and bring back.
  await tileFor("gA").locator(".rd-grid-tile-collapse").click();
  await expect(tileFor("gA")).toHaveCount(0);
  const chip = page.locator(".rd-grid-dock-chip", { hasText: "gA" });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(tileFor("gA")).toBeVisible();

  // Switch folders from inside the grid: pick the subfolder — only its
  // session tiles now.
  await page
    .locator(".rd-grid-folder:not(.rd-grid-cols)")
    .selectOption(`${folder}/deep`);
  await expect(tileFor("gB")).toBeVisible({ timeout: 5_000 });
  await expect(tileFor("gA")).toHaveCount(0);

  await page.getByRole("button", { name: /Exit grid/ }).click();
  await expect(page.locator(".rd-grid")).toHaveCount(0);
});

test("folders nest: subfolder renders inside, session moves into it", async ({
  page,
}) => {
  const parent = `Nest${Date.now()}`;
  await apiPost("/folders", { name: parent });
  await apiPost("/folders", { name: "Loose" });
  // Nest "Loose" under the parent via the move API (drag-drop does the same).
  const moved = await apiPatch(`/folders/${encodeURIComponent("Loose")}`, {
    parent,
  });
  expect(moved.status).toBe(200);
  expect(moved.body.to).toBe(`${parent}/Loose`);

  await page.goto(base());
  const parentHead = page.locator(".rd-group-head", { hasText: parent });
  await expect(parentHead).toBeVisible();
  // The nested folder renders inside the parent's body, by its leaf name.
  const nested = page
    .locator(".rd-group", { hasText: parent })
    .locator(".rd-group-head", { hasText: "Loose" });
  await expect(nested).toBeVisible();
});
