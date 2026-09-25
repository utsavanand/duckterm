import { expect, test } from "@playwright/test";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { apiDelete, apiPost, base } from "./helpers";

test("pins persist, open the exact older message, preserve terminal drafts, and retain saved copies", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  const cwd = realpathSync(mkdtempSync(join(tmpdir(), "duckterm-pin-test-")));
  const project = join(homedir(), ".claude", "projects", cwd.replace(/[^a-zA-Z0-9]/g, "-"));
  mkdirSync(project, { recursive: true });
  const transcript = join(project, "pin-test.jsonl");
  const records = [
    { type: "user", message: { role: "user", content: "Keep the installation decision" } },
    { type: "assistant", message: { role: "assistant", content: "Use the existing project for this test." } },
    { type: "user", message: { role: "user", content: "What is next?" } },
    { type: "assistant", message: { role: "assistant", content: "Now check the backup." } },
  ];
  writeFileSync(transcript, records.map((r) => JSON.stringify(r)).join("\n"));
  let key = "";
  try {
    const launch = await apiPost("/sessions/launch", {
      command: "sh -c 'cat'", cwd, runtime: "claude-code", name: "pin-test-agent", in_terminal: false, test: true,
    });
    expect(launch.status).toBe(200);
    key = String(launch.body.session_key);
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "pin-test-agent" }).click();
    const terminal = page.locator(".rd-terminal-slot:visible .xterm-helper-textarea");
    await terminal.focus();
    await page.keyboard.type("UNSUBMITTED_DRAFT");
    await page.locator(".rd-view-toggle button", { hasText: "Messages" }).click();
    await page.getByRole("button", { name: "Previous turn", exact: true }).click();
    const message = page.locator(".rd-message", { hasText: "Use the existing project for this test." });
    await message.getByRole("button", { name: "Pin", exact: true }).click();
    await expect(message.getByRole("button", { name: "Unpin", exact: true })).toBeEnabled();
    await page.locator(".rd-view-toggle button", { hasText: "Terminal" }).click();
    await expect(page.locator(".rd-terminal-slot:visible .xterm-rows")).toContainText("UNSUBMITTED_DRAFT");
    const chip = page.getByRole("button", { name: "Open pinned message: Use the existing project for this test.", exact: true });
    await expect(chip).toHaveText("");
    await expect(chip.locator("svg")).toHaveAttribute("stroke", "currentColor");
    await chip.hover();
    await expect(chip).toHaveAttribute("title", "Use the existing project for this");
    await chip.click();
    await expect(page.locator(".rd-message-target")).toContainText("Use the existing project for this test.");
    await expect(page.locator(".rd-turn-nav")).toContainText("turn 1 / 2");
    await page.reload();
    await page.locator(".rd-row-name", { hasText: "pin-test-agent" }).click();
    await expect(chip).toBeVisible();
    await page.screenshot({ path: "/tmp/message-pins-implemented-terminal.png" });
    await chip.click();
    await expect(page.locator(".rd-message-target")).toContainText("Use the existing project for this test.");
    await page.screenshot({ path: "/tmp/message-pins-implemented-messages.png" });
    // Reuse the same record positions with new content: never target that text.
    records[1].message.content = "A different decision now occupies this line.";
    writeFileSync(transcript, records.map((r) => JSON.stringify(r)).join("\n"));
    await expect(page.getByText("Saved copy ·", { exact: false })).toBeVisible({ timeout: 8_000 });
    await expect(page.locator(".rd-message-target")).toContainText("Use the existing project for this test.");
    await expect(page.getByText("A different decision now occupies this line.", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Unpin", exact: true }).click();
    await expect(chip).toHaveCount(0);
    await page.getByRole("button", { name: "Back to latest", exact: true }).click();
    await expect(page.locator(".rd-msg-text")).toContainText("Now check the backup.");
  } finally {
    if (key) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
    rmSync(project, { recursive: true, force: true });
    rmSync(cwd, { recursive: true, force: true });
  }
});
