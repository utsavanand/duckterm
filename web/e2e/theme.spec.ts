import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// The terminal follows the app's light/dark toggle. We assert on the host
// div's inline background (set by Terminal.tsx from the resolved palette) of
// the VISIBLE slot, and that the theme picker is scoped to the current mode.
async function shownTermBg(page: import("@playwright/test").Page): Promise<string> {
  return page.evaluate(() => {
    const slot = [...document.querySelectorAll<HTMLElement>(".rd-terminal-slot")].find(
      (s) => getComputedStyle(s).display !== "none",
    );
    const host = slot?.querySelector<HTMLElement>("div[style*='background']");
    return host?.style.background ?? "";
  });
}
const brightness = (s: string) =>
  (s.match(/\d+/g) ?? ["0", "0", "0"]).slice(0, 3).reduce((a, b) => a + Number(b), 0);

test("terminal palette follows the app light/dark toggle", async ({ page }) => {
  await apiPost("/sessions/launch", {
    command: "sh -c 'echo ready; exec cat'",
    cwd: "/tmp",
    name: "themed",
    in_terminal: false,
    test: true,
  });

  await page.goto(base());
  await page.evaluate(() => localStorage.setItem("rd-theme", "dark"));
  await page.reload();
  await page.locator(".rd-row-click", { hasText: "themed" }).click();

  await expect
    .poll(async () => brightness(await shownTermBg(page)), { timeout: 8000 })
    .toBeLessThan(200); // dark terminal background

  const picker = page.locator(".rd-term-theme");
  await expect(picker.locator("option", { hasText: "paper" })).toHaveCount(0);
  await expect(picker.locator("option", { hasText: "duck" })).toHaveCount(1);

  await page.evaluate(() => localStorage.setItem("rd-theme", "light"));
  await page.reload();
  await page.locator(".rd-row-click", { hasText: "themed" }).click();

  await expect
    .poll(async () => brightness(await shownTermBg(page)), { timeout: 8000 })
    .toBeGreaterThan(600); // near-white terminal background

  await expect(picker.locator("option", { hasText: "paper" })).toHaveCount(1);
  await expect(picker.locator("option", { hasText: "duck" })).toHaveCount(0);
});
