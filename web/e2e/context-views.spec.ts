import { expect, test } from "@playwright/test";
import { apiDelete, apiPatch, seedSession } from "./helpers";

test("context tabs keep connectors reachable, preserve notes and follow the selected host", async ({ page }) => {
  const key = await seedSession(`context-views-${Date.now()}`, { name: "Panel reviewer", runtime: "codex", test: true });
  await apiPatch(`/sessions/${key}`, { summary: "A populated session with enough detail to previously squeeze the connector list out of view. ".repeat(30) });
  try {
    await page.route("**/connectors", route => route.fulfill({ json: { connectors: Array.from({ length: 6 }, (_, i) => ({ name: `local-${i}`, title: `Local connector ${i + 1}`, enabled: true, description: "Repository tools and account details. ".repeat(5), identity: "Local test account", managed: true })) } }));
    await page.addInitScript(() => {
      window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "build", name: "Remote — Build server" }] };
      window.webkit = { messageHandlers: { launchRequest: { postMessage: async (message: unknown) => {
        const path = (message as { params?: { path?: string } }).params?.path;
        if (path === "/connectors") return { status: 200, body: JSON.stringify({ connectors: [{ name: "remote-git", title: "Remote repository tools", enabled: true, managed: true, description: "Remote host tools", identity: "Build account" }] }) };
        if (!["/sessions", "/tree", "/approvals", "/session-inbox-counts"].includes(path ?? "")) return { status: 404, body: '{"error":"Not in fixture"}' };
        return { status: 200, body: JSON.stringify({ nodes: [], sessions: [{ session_key: "context-remote", name: "Remote panel reviewer", runtime: "codex", state: "idle", started_at: 1, updated_at: Date.now(), event_count: 2, test: true }], counts: {}, requests: [], approvals: [] }) };
      } } } };
    });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: /^Panel reviewer$/ }).click();
    const pane = page.locator(".rd-context-pane");
    const session = pane.getByRole("tab", { name: "Session", exact: true });
    const connectors = pane.getByRole("tab", { name: "Connectors", exact: true });
    await expect(session).toHaveAttribute("aria-selected", "true");
    await pane.getByRole("button", { name: "Notes", exact: true }).click();
    await pane.locator("textarea").fill("Unsaved note survives changing views");
    await session.focus(); await page.keyboard.press("ArrowRight");
    await expect(connectors).toBeFocused();
    const list = pane.locator(".rd-connectors");
    for (const height of [900, 1000]) {
      await page.setViewportSize({ width: 1440, height });
      await expect(list.getByText("Local connector 1", { exact: true })).toBeVisible();
      const box = (await list.boundingBox())!;
      expect(box.height).toBeGreaterThan(600);
      expect(box.y + box.height).toBeLessThanOrEqual(height + 1);
      await expect.poll(() => list.evaluate(e => e.scrollHeight > e.clientHeight)).toBe(true);
      await list.hover();
      await page.mouse.wheel(0, 700);
      await expect.poll(() => list.evaluate(e => e.scrollTop)).toBeGreaterThan(0);
      await list.evaluate(e => { e.scrollTop = 0; });
    }
    await page.screenshot({ path: "/tmp/context-views-connectors.png" });
    await pane.getByRole("button", { name: "Collapse Context panel", exact: true }).click();
    await page.getByRole("button", { name: "Show Context panel", exact: true }).click();
    await expect(connectors).toHaveAttribute("aria-selected", "true");
    await session.click();
    await expect(pane.locator("textarea")).toHaveValue("Unsaved note survives changing views");
    await page.screenshot({ path: "/tmp/context-views-session.png" });
    await connectors.click();
    await page.reload();
    await expect(connectors).toHaveAttribute("aria-selected", "true");
    await page.locator(".rd-row-name", { hasText: /^Remote panel reviewer$/ }).click();
    await expect(list).toContainText("Build server");
    await expect(list.getByText("Remote repository tools", { exact: true })).toBeVisible();
    await expect(list.getByText("Local connector 1", { exact: true })).toHaveCount(0);
    await page.locator(".rd-row-name", { hasText: /^Panel reviewer$/ }).click();
    await expect(list).toContainText("This Mac");
    await expect(list.getByText("Local connector 1", { exact: true })).toBeVisible();
    await expect(list.getByText("Remote repository tools", { exact: true })).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    await connectors.scrollIntoViewIfNeeded();
    const tab = (await connectors.boundingBox())!, panel = (await pane.boundingBox())!;
    expect(tab.x).toBeGreaterThanOrEqual(panel.x);
    expect(tab.x + tab.width).toBeLessThanOrEqual(390);
    await session.focus(); await page.keyboard.press("End");
    await expect(connectors).toBeFocused();
    await page.keyboard.press("Home");
    await expect(session).toBeFocused();
    await expect(session).toHaveAttribute("aria-selected", "true");
  } finally { await apiDelete(`/sessions/${key}`); }
});
