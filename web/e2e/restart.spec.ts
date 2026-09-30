import { expect, test } from "@playwright/test";
import { apiDelete, seedSession } from "./helpers";

test("Restart dialog edits model, queues visibly, survives reload, and cancels", async ({ page }) => {
  const key = `e2e-restart-${Date.now()}`;
  await seedSession(key, { name: "Restart review", runtime: "codex", launched: true });
  let state = { can_restart: true, draft_clear: true, after_turn: true, model: "current-model", status: "", requested_model: "" };
  const submitted: string[] = [];
  await page.route(`**/sessions/${key}/restart`, async route => {
    const method = route.request().method();
    if (method === "POST") { const data = route.request().postDataJSON(); submitted.push(data.model); state = { ...state, status: "queued", requested_model: data.model }; }
    if (method === "DELETE") state = { ...state, status: "canceled" };
    await route.fulfill({ json: state });
  });
  await page.route(`**/sessions/${key}/models`, route => route.fulfill({ json: { models: [{ id: "current-model", label: "Current model 1.0" }, { id: "new-model", label: "New model 2.0" }] } }));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.getByText("Restart review", { exact: true }).first().click();
    const restart = page.getByRole("button", { name: "Restart", exact: true });
    await expect(restart).toBeEnabled();
    const change = page.getByRole("button", { name: "Change model", exact: true });
    await change.click();
    await expect(page.getByRole("menu", { name: "Choose model" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(change).toBeFocused();
    await change.click();
    await page.getByRole("menuitemradio", { name: /New model 2.0/ }).click();
    await expect(page.getByRole("dialog", { name: "Change model" })).toBeVisible();
    await page.keyboard.press("Escape");
    expect(submitted).toHaveLength(0);
    await restart.click();
    const dialog = page.getByRole("dialog", { name: "Restart session" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Model after restart")).toHaveValue("current-model");
    await dialog.getByLabel("Model after restart").selectOption("new-model");
    await page.screenshot({ path: "/tmp/duckterm-f15-implemented-dialog.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "/tmp/duckterm-f15-implemented-narrow.png" });
    await dialog.getByRole("button", { name: "Restart after this turn" }).click();
    expect(submitted).toEqual(["new-model"]);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await expect(page.getByText(/Restart pending/)).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Cancel restart" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel restart" }).click();
    await expect(page.getByRole("button", { name: "Cancel restart" })).toHaveCount(0);
    expect(submitted).toHaveLength(1);
    await expect(restart).toBeEnabled();
    state = { ...state, draft_clear: false };
    await restart.click();
    await expect(dialog.getByRole("button", { name: "Restart after this turn" })).toBeDisabled();
  } finally { await apiDelete(`/sessions/${key}`); }
});
