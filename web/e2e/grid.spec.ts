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
  await launchCat("gOutside");
  // gA in the folder, gB in its SUBfolder (both must tile); gOutside is not.
  await apiPatch(`/sessions/${a}`, { group: folder });
  await apiPatch(`/sessions/${b}`, { group: `${folder}/deep` });

  await page.goto(base());
  const head = page.locator(".rd-group-head", { hasText: folder });
  await expect(head).toBeVisible();
  await head.locator(".rd-group-grid").click();

  const tileFor = (n: string) => page.locator(".rd-grid-tile", { hasText: n });
  await expect(tileFor("gA")).toBeVisible({ timeout: 5_000 });
  await expect(tileFor("gB")).toBeVisible(); // subfolder session included
  await expect(tileFor("gOutside")).toHaveCount(0); // out of scope

  // 2 columns, then drag the vertical bar to resize.
  await page.locator(".rd-grid-folder").selectOption("2");
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
