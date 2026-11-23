import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { api, apiPost, seedSession } from "./helpers";

test("AGENTS.md waits for existing rules and reconciles a manual addition before save", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "rd-e2e-rules-load-"));
  const key = `e2e-rules-load-${Date.now()}`;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await seedSession(key, { name: key, cwd: dir });
  try {
    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: key }).click();
    await page.route("**/agents-md?*", async route => { await held; await route.continue(); });
    await page.getByRole("button", { name: "AGENTS.md", exact: true }).click();
    await expect(page.getByRole("button", { name: /Add rule/ })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
    release();
    await page.getByRole("button", { name: /Add rule/ }).click();
    await page.getByLabel("Title", { exact: true }).fill("Preserve instructions");
    await page.getByLabel("Instruction", { exact: true }).fill("Keep the new rule.");
    await page.getByRole("button", { name: "Compare with existing rules" }).click();
    await expect(page.locator(".ar-proposal")).toContainText("Keep the new rule.");
    await page.getByRole("button", { name: "Accept change" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText(/Rules saved/)).toBeVisible();
    expect(readFileSync(join(dir, "AGENTS.md"), "utf8")).toContain("Keep the new rule.");
    const saved = JSON.parse(readFileSync(join(dir, ".duckterm-rules.json"), "utf8"));
    expect(saved.rules).toEqual([expect.objectContaining({ title: "Preserve instructions", text: "Keep the new rule.", status: "active" })]);
    expect(saved).not.toHaveProperty("proposals");
    expect(saved).not.toHaveProperty("capture_fingerprint");
    const review = await (await api(`/agents-md?dir=${encodeURIComponent(dir)}`)).json();
    expect(review.proposals[0].status).toBe("accepted");
    await page.getByRole("button", { name: "Rules 1" }).click();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.getByLabel("Why", { exact: true }).fill("Avoid losing owner instructions.");
    await page.getByRole("button", { name: "Add example", exact: true }).click();
    await page.getByLabel("Situation", { exact: true }).fill("Continuing after a checkpoint");
    await page.getByLabel("Do", { exact: true }).fill("Retain the original constraints.");
    await page.getByRole("button", { name: "Keep changes" }).click();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.locator(".ar-save-state")).toHaveText("Saved rules");
    expect(readFileSync(join(dir, "AGENTS.md"), "utf8")).toContain("Retain the original constraints.");
    await page.screenshot({ path: "/tmp/duckterm-rules-implemented.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.locator(".ar-page").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
    await apiPost(`/sessions/${key}/delete`);
    rmSync(dir, { recursive: true, force: true });
  }
});

test("complete repository rules remain readable and filterable at desktop and phone sizes", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "rd-rules-preview-"));
  const key = `e2e-rules-preview-${Date.now()}`;
  const rules = JSON.parse(readFileSync(join(process.cwd(), "e2e/fixtures/agents-rules.json"), "utf8"));
  await seedSession(key, { name: key, cwd: dir });
  try {
    expect((await apiPost("/agents-md", { dir, rules })).status).toBe(200);
    await page.addInitScript(() => localStorage.setItem("rd-theme", "dark"));
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: key }).click();
    await page.getByRole("button", { name: "AGENTS.md", exact: true }).click();
    await expect(page.locator(".ar-rule")).toHaveCount(rules.length);
    await expect(page.locator(".ar-rule").first()).toContainText(rules[0].text);
    await page.screenshot({ path: "/tmp/duckterm-rules-desktop.png" });
    await page.getByLabel("Who it targets").selectOption("codex");
    await expect(page.locator(".ar-rule")).toHaveCount(1);
    await page.getByLabel("Who it targets").selectOption("any");
    await page.getByRole("textbox", { name: "Search rules" }).fill("pipx");
    await expect(page.locator(".ar-rule")).toHaveCount(1);
    await page.getByRole("textbox", { name: "Search rules" }).fill("");
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.locator(".ar-page").evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.screenshot({ path: "/tmp/duckterm-rules-phone.png" });
  } finally {
    await apiPost(`/sessions/${key}/delete`);
    rmSync(dir, { recursive: true, force: true });
  }
});
