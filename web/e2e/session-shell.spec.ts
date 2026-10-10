import { expect, test } from "@playwright/test";
import { apiPost, apiDelete, base, seedSession } from "./helpers";

test("shell collapse preserves its process and draft, close preserves the agent", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  const result = await apiPost("/sessions/launch", { command: "sh -c 'echo AGENT_STAYS; cat'", cwd: "/tmp", name: "Shell integration", in_terminal: false, test: true });
  expect(result.status).toBe(200);
  const key = String(result.body.session_key);
  let creations = 0, deletions = 0, agentConnections = 0;
  page.on("request", request => {
    if (request.url().endsWith(`/sessions/${key}/shell`)) {
      if (request.method() === "POST") creations++;
      if (request.method() === "DELETE") deletions++;
    }
  });
  page.on("websocket", socket => { if (socket.url().endsWith(`/sessions/${key}/terminal`)) agentConnections++; });
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Shell integration" }).click();
    const agent = page.locator(".rd-terminal-slot:visible .xterm-rows");
    await expect(agent).toContainText("AGENT_STAYS");
    await expect(page.locator("#rd-shell-toggle button")).toBeVisible();
    expect(creations).toBe(0);
    await page.locator("#rd-shell-toggle button").click();
    const shell = page.getByRole("region", { name: "Session shell" });
    const input = shell.locator(".xterm-helper-textarea");
    await expect(shell.locator(".rd-shell-state")).toHaveText("Idle");
    if (process.env.SHELL_SCREENSHOTS) {
      await page.screenshot({ path: "/tmp/session-shell-implemented-dark.png" });
      await page.emulateMedia({ colorScheme: "light" });
      await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
      await page.screenshot({ path: "/tmp/session-shell-implemented-light.png" });
      await page.emulateMedia({ colorScheme: "dark" });
    }
    await input.focus();
    await page.keyboard.type("printf 'SHELL_READY\\n'\n");
    await expect(shell.locator(".xterm-rows")).toContainText("SHELL_READY");
    await page.keyboard.type("DRAFT_SURVIVES");
    const divider = page.getByRole("separator", { name: "Resize shell" });
    await divider.focus();
    await page.keyboard.press("ArrowUp");
    expect(Number(await divider.getAttribute("aria-valuenow"))).toBe(254);
    await shell.getByRole("button", { name: "Collapse" }).click();
    expect(deletions).toBe(0);
    await page.getByRole("button", { name: "Expand", exact: true }).click();
    await expect(shell.locator(".xterm-rows")).toContainText("DRAFT_SURVIVES");
    expect(creations).toBe(1);
    expect(Number(await divider.getAttribute("aria-valuenow"))).toBe(254);
    await input.focus();
    await page.keyboard.press("Control+u");
    await page.keyboard.type("sleep 120\n");
    await expect(shell.locator(".rd-shell-state")).toHaveText("sleep", { timeout: 10000 });
    await shell.getByRole("button", { name: "Close shell", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Close this shell?" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator("code")).toHaveText("sleep");
    if (process.env.SHELL_SCREENSHOTS) await page.screenshot({ path: "/tmp/session-shell-implemented-confirm.png" });
    await dialog.getByRole("button", { name: "Keep open" }).click();
    await expect(dialog).not.toBeVisible();
    await shell.getByRole("button", { name: "Collapse" }).click();
    await expect(page.locator(".rd-shell-collapsed")).toContainText("Keeps running");
    await page.getByRole("button", { name: "Expand", exact: true }).click();
    await shell.getByRole("button", { name: "Close shell", exact: true }).click();
    await dialog.getByRole("button", { name: "Close shell", exact: true }).click();
    await expect(shell).not.toBeVisible();
    await expect(page.locator("#rd-shell-toggle button")).toHaveAttribute("aria-expanded", "false");
    expect(agentConnections).toBe(1);
    await page.locator(".rd-terminal-slot:visible .xterm-helper-textarea").focus();
    await page.keyboard.type("AGENT_STILL_ACCEPTS_INPUT\n");
    await expect(agent).toContainText("AGENT_STILL_ACCEPTS_INPUT");
  } finally { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); }
});

test("watched sessions explain unsupported shells without creating one", async ({ page }) => {
  const key = await seedSession("shell-watched", { name: "Watched shell fixture" });
  let created = false;
  page.on("request", request => { if (request.url().endsWith(`/sessions/${key}/shell`) && request.method() === "POST") created = true; });
  try {
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Watched shell fixture" }).click();
    await page.locator("#rd-shell-toggle button").click();
    await expect(page.getByText("Shell not supported for this session", { exact: true })).toBeVisible();
    expect(created).toBe(false);
  } finally { await apiDelete(`/sessions/${key}`); }
});

test("shell open state and height survive session switches and reload", async ({ page }) => {
  const keys: string[] = [];
  try {
    for (const name of ["Shell first", "Shell second"]) {
      const result = await apiPost("/sessions/launch", { command: "cat", cwd: "/tmp", name, in_terminal: false, test: true });
      expect(result.status).toBe(200); keys.push(String(result.body.session_key));
    }
    await page.goto(base());
    await page.locator(".rd-row-name", { hasText: "Shell first" }).click();
    await page.locator("#rd-shell-toggle button").click();
    await expect(page.locator(".rd-shell-state")).toHaveText("Idle");
    const divider = page.getByRole("separator", { name: "Resize shell" });
    await divider.focus(); await page.keyboard.press("ArrowUp");
    const height = await divider.getAttribute("aria-valuenow");
    await page.locator(".rd-session-shell .xterm-helper-textarea").focus();
    await page.keyboard.type("SAVED_SHELL_DRAFT");
    await page.locator(".rd-row-name", { hasText: "Shell second" }).click();
    await expect(page.locator("#rd-shell-toggle button")).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator(".rd-session-shell")).not.toBeVisible();
    await page.locator(".rd-row-name", { hasText: "Shell first" }).click();
    await expect(page.locator(".rd-session-shell .xterm-rows")).toContainText("SAVED_SHELL_DRAFT");
    await expect(divider).toHaveAttribute("aria-valuenow", height!);
    await page.reload();
    await expect(page.locator(".rd-session-shell .xterm-rows")).toContainText("SAVED_SHELL_DRAFT");
    await expect(divider).toHaveAttribute("aria-valuenow", height!);
    await page.setViewportSize({ width: 390, height: 844 });
    const toggle = await page.locator("#rd-shell-toggle button").boundingBox();
    expect(toggle).not.toBeNull();
    expect(toggle!.x + toggle!.width).toBeLessThanOrEqual(390);
    await expect(page.getByRole("button", { name: "Close shell", exact: true })).toBeVisible();
  } finally { for (const key of keys) { await apiPost(`/sessions/${key}/stop`); await apiDelete(`/sessions/${key}`); } }
});
