import { expect, test } from "@playwright/test";
import { apiPost, base } from "./helpers";

// The Share button on a live session creates a read-only link and shows it in a
// copyable popover. (The relay isn't running under e2e — the uplink retries in
// the background; share creation returns the link regardless.)
test("Share button surfaces a read-only viewer link", async ({ page }) => {
  // A quiet session (no ongoing output) so the row doesn't re-render under the
  // click — an actively-printing session makes Playwright's actionability wait
  // loop forever on a detaching node.
  const r = await apiPost("/sessions/launch", {
    command: "sh -c 'exec cat'",
    cwd: "/tmp",
    name: "shareable",
    in_terminal: false,
    test: true,
  });
  expect(r.status).toBe(200);

  await page.goto(base());
  const row = page.locator(".rd-row", { hasText: "shareable" }).first();
  await expect(row).toBeVisible();
  await expect(row.locator(".rd-share-url")).toHaveCount(0);

  const shareBtn = () => row.locator("button", { hasText: "Share" }).first();
  await shareBtn().evaluate((b: HTMLElement) => b.click());

  const url = row.locator(".rd-share-url");
  await expect(url).toBeVisible({ timeout: 10_000 });
  // Path-routed with the capability token in the fragment.
  expect(await url.inputValue()).toMatch(/\/s\/[^#]+#.+/);

  // Clicking Share again hides the link (toggle).
  await shareBtn().evaluate((b: HTMLElement) => b.click());
  await expect(row.locator(".rd-share-url")).toHaveCount(0);
});
