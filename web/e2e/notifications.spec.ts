import { expect, test } from "@playwright/test";
import { apiDelete, base, postEvent, seedSession } from "./helpers";

test("browser notification preference persists without replaying waiting sessions", async ({ page }) => {
  const key = await seedSession(`notification-${Date.now()}`, { name: "Notification check", runtime: "claude-code" });
  await postEvent({ session_key: key, event_type: "Notification", notification_type: "permission_prompt", message: "Approval needed", runtime: "claude-code" });
  try {
    await page.addInitScript(() => {
      const notices: string[] = [];
      Object.assign(window, { notificationTestNotices: notices });
      Object.defineProperty(window, "Notification", { configurable: true, value: class {
        static permission = "granted";
        static requestPermission = async () => "granted";
        constructor(title: string) { notices.push(title); }
      } });
    });
    await page.goto(base());
    await expect(page.locator(".rd-row-name", { hasText: "Notification check" })).toBeVisible();
    await expect(page.locator(".rd-row").filter({ has: page.locator(".rd-row-name", { hasText: "Notification check" }) })).toContainText("waiting");
    await page.getByRole("button", { name: /^Settings/ }).click();
    const toggle = page.getByLabel("Desktop notifications", { exact: true });
    await expect(toggle).toBeChecked();
    const notices = () => page.evaluate(() => (window as unknown as { notificationTestNotices: string[] }).notificationTestNotices);
    expect(await notices()).toEqual([]);
    await toggle.uncheck(); await page.reload();
    await page.getByRole("button", { name: /^Settings/ }).click();
    await expect(toggle).not.toBeChecked();
    await toggle.check(); expect(await notices()).toEqual([]);
    await page.getByRole("button", { name: /^Settings/ }).click();
    await postEvent({ session_key: key, event_type: "UserPromptSubmit", runtime: "claude-code" });
    const row = page.locator(".rd-row").filter({ has: page.locator(".rd-row-name", { hasText: "Notification check" }) });
    await expect(row).toContainText("busy");
    await postEvent({ session_key: key, event_type: "Notification", notification_type: "permission_prompt", message: "Approval needed", runtime: "claude-code" });
    await expect.poll(notices).toEqual(["Notification check needs you"]);
  } finally { await apiDelete(`/sessions/${key}`); }
});

test("blocked notification permission has actionable feedback", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("rd.notifyOn", "true");
    Object.defineProperty(window, "Notification", { configurable: true, value: class { static permission = "denied"; } });
  });
  await page.goto(base());
  await page.getByRole("button", { name: /^Settings/ }).click();
  await expect(page.getByLabel("Desktop notifications", { exact: true })).not.toBeChecked();
  await expect(page.locator("#header-notification-help")).toContainText("blocked in your browser");
});
