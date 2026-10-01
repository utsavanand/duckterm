import { expect, test } from "@playwright/test";
import { apiDelete, apiPost, base, seedSession } from "./helpers";

test("explicit assignment creates a visible task and ordinary messages do not", async ({ page }) => {
  const folder = `Tasks ${Date.now()}`, key = `tasks-agent-${Date.now()}`;
  try {
    await seedSession(key, { name: "Design agent", runtime: "generic", group: folder });
    const enrollment = await apiPost(`/sessions/${key}/collaboration`, { root: folder });
    expect(enrollment.status).toBe(200);
    const tasks = async () => { const response = await fetch(`${base()}/api/v1/session/tasks`, { headers: { Authorization: `Bearer ${enrollment.body.token}` } }); expect(response.ok).toBe(true); return (await response.json()).tasks; };
    await page.setViewportSize({ width: 1440, height: 1000 }); await page.emulateMedia({ colorScheme: "dark" }); await page.goto(base());
    await page.locator(".rd-group-head").filter({ has: page.getByRole("button", { name: `View interactions in ${folder}`, exact: true }) }).locator(".rd-group-name").click();
    const input = page.getByRole("textbox", { name: "Ask this folder" });
    await input.fill("@"); await page.getByRole("option", { name: /Design agent/ }).click();
    await input.fill("Review the mobile navigation");
    await page.getByRole("button", { name: "Send to Design agent", exact: true }).click();
    await expect(page.getByText("Queued in inbox", { exact: false }).first()).toBeVisible();
    expect(await tasks()).toHaveLength(0);
    await input.fill("Implement keyboard navigation");
    await page.getByRole("button", { name: "Assign & send", exact: true }).click();
    const widget = page.getByRole("article", { name: "Tasks", exact: true });
    await expect(widget.getByRole("heading", { name: "Implement keyboard navigation", exact: true })).toBeVisible();
    expect(await tasks()).toHaveLength(1);
    await page.screenshot({ path: "/tmp/duckterm-folder-tasks-dark.png" });
    await page.emulateMedia({ colorScheme: "light" }); await page.screenshot({ path: "/tmp/duckterm-folder-tasks-light.png" });
    await page.setViewportSize({ width: 900, height: 900 });
    await page.getByRole("button", { name: "Details", exact: true }).click();
    await expect(widget).toBeInViewport(); await page.screenshot({ path: "/tmp/duckterm-folder-tasks-narrow.png" });
    await widget.getByRole("button", { name: "Design agent ↗" }).click();
    await expect(page.getByRole("region", { name: "Session controls" })).toBeVisible();
  } finally { await apiDelete(`/sessions/${key}`); await apiDelete(`/folders/${encodeURIComponent(folder)}`); }
});
