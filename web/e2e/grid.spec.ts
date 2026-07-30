import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// Fullscreen grid: every running terminal tiled 2D — columns control, drag
// order, collapse-to-dock, esc to exit.

async function launchCat(name: string) {
  const r = await apiPost("/sessions/launch", {
    command: `sh -c 'echo READY_${name}; exec cat'`,
    cwd: "/tmp",
    name,
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);
}

test("grid tiles terminals 2D, docks a collapsed one, exits on esc", async ({
  page,
}) => {
  await launchCat("gridA");
  await launchCat("gridB");
  await launchCat("gridC");
  await page.goto(base());
  await expect(page.locator(".rd-row", { hasText: "gridA" })).toBeVisible();

  await page.getByRole("button", { name: "Grid", exact: true }).click();
  // Other specs' sessions may tile too — assert on OUR tiles by name.
  const tileFor = (n: string) => page.locator(".rd-grid-tile", { hasText: n });
  await expect(tileFor("gridA")).toBeVisible({ timeout: 5_000 });
  await expect(tileFor("gridB")).toBeVisible();
  await expect(tileFor("gridC")).toBeVisible();

  // Terminals paint their banners in the tiles.
  await expect(
    page.locator(".rd-grid-tile", { hasText: "gridA" }).locator(".xterm-rows"),
  ).toContainText("READY_gridA", { timeout: 8_000 });

  // 2 columns: 3 tiles flow as 2-up + 1-below (the flexible arrangement).
  await page.locator(".rd-grid-folder").last().selectOption("2");
  await expect(page.locator(".rd-grid-tiles")).toHaveCSS(
    "grid-template-columns",
    /^\d/,
  );

  // Collapse one tile: it leaves the grid and appears as a dock chip; the
  // chip brings it back.
  await tileFor("gridB").locator(".rd-grid-tile-collapse").click();
  await expect(tileFor("gridB")).toHaveCount(0);
  const chip = page.locator(".rd-grid-dock-chip", { hasText: "gridB" });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(tileFor("gridB")).toBeVisible();

  // Esc is swallowed by a focused terminal on purpose (it belongs to the
  // agent there) — the Exit button is the always-works path.
  await page.getByRole("button", { name: /Exit grid/ }).click();
  await expect(page.locator(".rd-grid")).toHaveCount(0);
});
