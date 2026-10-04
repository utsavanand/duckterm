import { expect, test } from "@playwright/test";
import { apiDelete, base, expandFolder, postEvent, seedSession } from "./helpers";

test("deleting a parent immediately restores child controls and preserves descendants through reload", async ({ page }) => {
  const prefix = `e2e-parent-${Date.now()}`;
  const parent = `${prefix}-parent`;
  const child = `${prefix}-child`;
  const grandchild = `${prefix}-grandchild`;
  const group = `${prefix}-folder`;
  try {
    await seedSession(parent, { name: parent, group });
    await seedSession(child, { name: child, group, parent_session_key: parent });
    await seedSession(grandchild, { name: grandchild, group, parent_session_key: child });
    await page.goto("/");
    await expandFolder(page, group);
    const row = (name: string) => page.locator(".rd-row").filter({ has: page.locator(".rd-row-name", { hasText: name }) });
    const controls = page.locator(".rd-session-controls");
    await row(child).locator(".rd-row-click").click();
    await expect(controls.getByRole("button", { name: "Ungroup", exact: true })).toHaveCount(0);
    await row(parent).locator(".rd-row-click").click();
    await controls.getByRole("button", { name: "Stop watching", exact: true }).click();
    await controls.getByRole("button", { name: "Confirm?", exact: true }).click();
    await expect(row(parent)).toHaveCount(0);
    await row(child).locator(".rd-row-click").click();
    await expect(controls.getByRole("button", { name: "Ungroup", exact: true })).toBeVisible();
    await expect(row(grandchild)).toBeVisible();

    // The real server rejects a late supervisor event's stale parent link.
    await postEvent({ session_key: child, event_type: "PreToolUse", tool_name: "Read", parent_session_key: parent });
    const response = await fetch(`${base()}/sessions`);
    const data = await response.json();
    expect(data.sessions.find((s: { session_key: string }) => s.session_key === child).parent_session_key).toBeNull();
    expect(data.sessions.find((s: { session_key: string }) => s.session_key === grandchild).parent_session_key).toBe(child);
    await page.reload();
    await expandFolder(page, group);
    await row(child).locator(".rd-row-click").click();
    await expect(controls.getByRole("button", { name: "Ungroup", exact: true })).toBeVisible();
    await row(grandchild).locator(".rd-row-click").click();
    await expect(controls.getByRole("button", { name: "Ungroup", exact: true })).toHaveCount(0);
  } finally {
    for (const key of [grandchild, child, parent]) await apiDelete(`/sessions/${key}`);
    await apiDelete(`/groups/${encodeURIComponent(group)}`);
  }
});
