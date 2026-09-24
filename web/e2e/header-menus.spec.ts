import { expect, test, type WebSocketRoute } from "@playwright/test";
import { apiDelete, apiPost } from "./helpers";

test("terminal replay does not steal focus from an open header menu", async ({ page }) => {
  const result = await apiPost("/sessions/launch", {
    command: "cat", cwd: "/tmp", name: "menu-focus-probe", in_terminal: false, test: true,
  });
  expect(result.status).toBe(200);
  const key = result.body.session_key as string;
  let terminal: WebSocketRoute | undefined;
  await page.routeWebSocket(`**/sessions/${key}/terminal`, (socket) => { terminal = socket; });
  try {
    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: "menu-focus-probe" }).click();
    await expect.poll(() => !!terminal).toBe(true);
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const theme = page.getByRole("combobox", { name: "Theme", exact: true });
    await expect(theme).toBeFocused();
    terminal!.send(Buffer.from("DELAYED_REPLAY\r\n"));
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("DELAYED_REPLAY");
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))));
    await expect(theme).toBeFocused();
    await page.getByRole("button", { name: "Back up to remote", exact: true }).click();
    await expect(page.getByLabel("Backup destination", { exact: true })).toBeEnabled();
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});

test("header menus support keyboard navigation, dismissal, and separate rules", async ({ page }) => {
  await page.goto("/");
  const header = page.locator(".rd-topbar");
  const create = header.getByRole("button", { name: "New", exact: true });
  const settings = header.getByRole("button", { name: "Settings", exact: true });
  await expect(header.getByRole("button", { name: "AGENTS.md" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New session", exact: true })).toHaveCount(0);
  await create.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "New session", exact: true })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("button", { name: "New folder", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(create).toBeFocused();
  await expect(create).toHaveAttribute("aria-expanded", "false");
  await create.click();
  await settings.click();
  await expect(create).toHaveAttribute("aria-expanded", "false");
  const panel = page.getByRole("region", { name: "Settings", exact: true });
  await expect(panel.getByRole("combobox", { name: "Theme", exact: true })).toBeFocused();
  await expect(panel.getByRole("checkbox", { name: "Desktop notifications" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "AGENTS.md" })).toHaveCount(0);
  await panel.getByRole("combobox", { name: "Theme", exact: true }).selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.keyboard.press("Escape");
  await expect(settings).toBeFocused();
  await settings.click();
  await header.locator(".rd-brand").click();
  await expect(panel).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await settings.click();
  const box = await panel.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
});
