import { sessionMenu } from "./helpers";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, expandFolder, postEvent, seedSession, sessions } from "./helpers";

test("reviewed merge preserves exact text and a closed child stays readable", async ({ page }) => {
  const parent = `merge-parent-${Date.now()}`, child = `merge-child-${Date.now()}`;
  const folder = "Merge review";
  try {
    await seedSession(parent, { name: "Planning", runtime: "generic", group: folder });
    await seedSession(child, { name: "Implementation fork", runtime: "claude-code", group: folder, parent_session_key: parent, launched: true });
    expect((await apiPost(`/sessions/${parent}/collaboration`, { root: folder })).status).toBe(200);
    await postEvent({ event_type: "Notification", session_key: child, lifecycle: "stopped" });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base());
    await expandFolder(page, folder);
    await page.locator(".rd-row-name", { hasText: "Implementation fork" }).click();
    await (await sessionMenu(page)).getByRole("menuitem", { name: "Merge back…", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Merge back to parent" });
    await expect(dialog.getByText("Planning", { exact: true })).toBeVisible();
    await expect(dialog.getByText(/receives inbox mail only/)).toBeVisible();
    const summary = "Summary of the fork, not the full thread.\n\nImplemented the reviewed layout. 🦆\nRemaining: native acceptance. \n";
    await dialog.getByRole("textbox", { name: /Summary to send/ }).fill(summary);
    await page.screenshot({ path: "/tmp/duckterm-fork-merge-desktop.png" });
    await page.evaluate(() => { localStorage.setItem("rd-theme", "dark"); document.documentElement.dataset.theme = "dark"; });
    await page.screenshot({ path: "/tmp/duckterm-fork-merge-dark.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(dialog.getByRole("button", { name: "Send summary & close child" })).toBeInViewport({ ratio: 1 });
    await page.screenshot({ path: "/tmp/duckterm-fork-merge-mobile.png" });
    await dialog.getByRole("button", { name: "Send summary & close child" }).click();
    await expect(dialog.getByText("Child is merged and remains readable. Both histories record this merge.")).toBeVisible();
    expect(await dialog.locator("pre").textContent()).toBe(summary);
    expect((await sessions()).find(row => row.session_key === child)?.state).toBe("merged");
    await dialog.getByRole("button", { name: "Done", exact: true }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    const controls = page.getByRole("region", { name: "Session controls" });
    await expect(controls.getByRole("button", { name: /^(Resume|Restart|Merge back)$/ })).toHaveCount(0);
    await page.getByRole("button", { name: "Timeline", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Fork merges" })).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${child}`); await apiDelete(`/sessions/${parent}`);
  }
});
