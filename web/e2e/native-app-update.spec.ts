import { expect, test } from "@playwright/test";

test("complete Mac update card uses native progress and stays compact", async ({ page }) => {
  await page.addInitScript(() => {
    let state: Record<string, unknown> = { phase: "available", installed_version: "0.4.208", latest_version: "0.4.210", can_cancel: true };
    Object.defineProperty(window, "__rubbertermDesktop", { value: { currentTarget: "local", canUpdateApp: true, targets: [{ id: "local", name: "This Mac" }] }, configurable: true });
    Object.defineProperty(window, "webkit", { value: { messageHandlers: { appUpdate: { postMessage: async ({ operation }: { operation: string }) => {
      if (operation === "install") state = { ...state, phase: "downloading", progress: 0.42 };
      if (operation === "cancel") state = { ...state, phase: "idle" };
      return state;
    } } } }, configurable: true });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const card = page.getByRole("region", { name: "Update DuckTerm" });
  await card.scrollIntoViewIfNeeded();
  await expect(card).toContainText("0.4.210 available");
  await page.screenshot({ path: "/tmp/duckterm-native-update-available.png" });
  await card.getByRole("button", { name: "Update & restart" }).click();
  await expect(card.getByRole("progressbar")).toHaveAttribute("value", "0.42");
  await page.screenshot({ path: "/tmp/duckterm-native-update-progress.png" });
  await page.setViewportSize({ width: 390, height: 844 }); await card.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/duckterm-native-update-mobile.png" });
  const box = await card.boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  await card.getByRole("button", { name: "Cancel" }).click();
  await expect(card.getByRole("progressbar")).toHaveCount(0);
});
