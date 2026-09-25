import { expect, test } from "@playwright/test";

test("copy review shows source, destination and exclusions before transfer", async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.addInitScript(() => {
    window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — Development" }] };
    window.webkit = { messageHandlers: {
      remoteSession: { postMessage: () => { throw new Error("Must not switch before launch"); } },
      launchRequest: { postMessage: async (raw: unknown) => {
        const request = raw as { operation: string };
        if (request.operation === "project-preview") return { source: "/Users/you/projects/service", fingerprint: "review", bytes: 18432, entries: [{ path: "src/app.py" }, { path: "README.md" }], excluded: [".env", ".venv/"], ignored: [], git: { kind: "git" }, conversation: {} };
        if (request.operation === "project-preflight") return { runtime: "available" };
        if (request.operation === "project-transfer") return { id: "test", stage: "ready", destination: "/home/duckterm/projects/service-copy" };
        return { themes: [] };
      } },
    } };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByRole("button", { name: "New session", exact: true }).click();
  await page.getByRole("combobox", { name: "Run on" }).selectOption("dev");
  await page.getByRole("combobox", { name: "Project source" }).selectOption("copy");
  await page.getByLabel("Local project path").fill("/Users/you/projects/service");
  await page.getByLabel("Remote destination folder").fill("/home/duckterm/projects/service-copy");
  await page.getByRole("button", { name: "Review transfer" }).click();
  await expect(page.getByRole("button", { name: "Copy project" })).toBeDisabled();
  await page.getByText("Excluded files (2)").click();
  await expect(page.getByText(".env\n.venv/", { exact: true })).toBeVisible();
  await page.screenshot({ path: "/tmp/remote-project-review.png", fullPage: true });
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Copy project" }).click();
  await expect(page.getByText("/home/duckterm/projects/service-copy", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Launch", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => window.__rubbertermDesktop?.currentTarget)).toBe("local");
});

test("Move to remote preserves the local session and exposes the destination link", async ({ page }) => {
  const { apiPost, apiDelete, seedSession } = await import("./helpers");
  const key = `move-preview-${Date.now()}`;
  await seedSession(key, { session_id: "11111111-1111-4111-8111-111111111111", name: "Migration fixture", cwd: "/tmp/synthetic-project", runtime: "codex", launched: true, test: true });
  await apiPost("/events", { event_type: "Notification", session_key: key, lifecycle: "stopped" });
  await page.addInitScript(() => {
    window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — Development" }] };
    window.webkit = { messageHandlers: {
      remoteSession: { postMessage: () => { throw new Error("Wait for explicit Open remote session"); } },
      launchRequest: { postMessage: async (raw: unknown) => {
        const r = raw as { operation: string };
        if (r.operation === "project-preview") return { source: "/tmp/synthetic-project", fingerprint: "review", bytes: 10, entries: [{ path: "hello.py" }], excluded: [], ignored: [], git: { kind: "folder" }, conversation: { runtime: "codex", id: "11111111-1111-4111-8111-111111111111", sha256: "synthetic" } };
        if (r.operation === "project-transfer") return { id: "synthetic", stage: "ready", destination: "/remote/project" };
        if (r.operation === "project-launch") return { id: "synthetic", stage: "launched", session_key: "remote-synthetic" };
        return {};
      } },
    } };
  });
  try {
    await page.goto("/");
    const row = page.locator(".rd-row", { hasText: "Migration fixture" });
    await row.hover();
    await row.getByRole("button", { name: "Move to remote…" }).click();
    await page.getByLabel("Remote destination folder").fill("/remote/project");
    await page.getByRole("button", { name: "Review transfer" }).click();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Move to remote", exact: true }).click();
    await expect(page.getByText(/The stopped local session and project remain/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Close", exact: true })).toBeEnabled();
    expect(await page.evaluate(() => window.__rubbertermDesktop?.currentTarget)).toBe("local");
  } finally { await apiDelete(`/sessions/${key}`); }
});
