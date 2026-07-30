import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// Fullscreen grid: every running terminal tiled at once, orientation toggle,
// per-tile collapse, esc to exit.

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

test("grid tiles every terminal, collapses one, exits on esc", async ({
  page,
}) => {
  await launchCat("gridA");
  await launchCat("gridB");
  await page.goto(base());
  await expect(page.locator(".rd-row", { hasText: "gridA" })).toBeVisible();

  await page.getByRole("button", { name: "Grid", exact: true }).click();
  const tiles = page.locator(".rd-grid-tile");
  await expect(tiles).toHaveCount(2, { timeout: 5_000 });

  // Both agents' terminals paint their banners.
  await expect(
    page.locator(".rd-grid-tile", { hasText: "gridA" }).locator(".xterm-rows"),
  ).toContainText("READY_gridA", { timeout: 8_000 });
  await expect(
    page.locator(".rd-grid-tile", { hasText: "gridB" }).locator(".xterm-rows"),
  ).toContainText("READY_gridB", { timeout: 8_000 });

  // Stacked <-> side-by-side.
  await page.getByRole("button", { name: /side by side|stacked/ }).click();
  await expect(page.locator(".rd-grid-tiles.rows")).toBeVisible();

  // Collapse one tile to its header bar.
  const tileA = page.locator(".rd-grid-tile", { hasText: "gridA" });
  await tileA.locator(".rd-grid-tile-collapse").click();
  await expect(tileA).toHaveClass(/collapsed/);

  await page.keyboard.press("Escape");
  await expect(page.locator(".rd-grid")).toHaveCount(0);
});
