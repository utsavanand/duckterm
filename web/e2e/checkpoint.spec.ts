import { sessionMenu } from "./helpers";
import { expect, test } from "@playwright/test";
import { apiDelete, checkpoints, postEvent, seedSession } from "./helpers";

// Record a checkpoint from the UI and verify the backend captured the session's
// activity. We seed a prompt + a Bash command via the events API (the same path
// the real hooks use), click Checkpoint, then read back GET
// /sessions/:key/checkpoints to confirm the prompt and command landed in the
// checkpoint record — not just that a row was created.
test("checkpoint captures the session's prompts and commands", async ({
  page,
}) => {
  const key = `e2e-checkpoint-${Date.now()}`;
  await seedSession(key, { name: key });
  try {
  await postEvent({
    event_type: "UserPromptSubmit",
    session_key: key,
    prompt: "add a login form",
    cwd: "/tmp/e2e",
  });
  await postEvent({
    event_type: "PreToolUse",
    session_key: key,
    tool_name: "Bash",
    tool_input: { command: "npm test" },
    cwd: "/tmp/e2e",
  });

  // Backend starts with no checkpoints for this session.
  expect(await checkpoints(key)).toHaveLength(0);

  await page.goto("/");

  const row = page.locator(".rd-row", { hasText: key });
  await expect(row).toBeVisible();

  await row.locator(".rd-row-click").click();
  await (await sessionMenu(page)).getByRole("menuitem", { name: "Checkpoint", exact: true }).click();

  // UI: the row saved, but the fake provider did not generate a usable summary.
  await expect(page.getByText("Summary update failed ·", { exact: false })).toBeVisible();

  // Backend: a checkpoint now exists and captured the prompt + the Bash command.
  await expect.poll(async () => (await checkpoints(key)).length).toBe(1);
  const [cp] = await checkpoints(key);
  expect(cp.label).toBe("manual");
  expect(cp.summary_update?.state).toBe("failed");
  expect(cp.record.prompts).toContain("add a login form");
  expect(cp.record.commands).toContain("npm test");
  expect(cp.record.tools).toContainEqual({ tool: "Bash", count: 1 });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.locator(".rd-view-toggle").getByRole("button", { name: "Timeline", exact: true }).click();
  const timeline = page.getByRole("region", { name: "Session timeline" });
  await expect(timeline.getByText("Owner message", { exact: true })).toHaveCount(0);
  await expect(timeline.locator(".rd-timeline-checkpoint")).toHaveCount(1);
  await timeline.getByRole("button", { name: "Checkpoints", exact: true }).click();
  await expect(timeline.getByText("Owner message", { exact: true })).toHaveCount(0);
  await expect(timeline.locator(".rd-timeline-checkpoint")).toHaveCount(1);
  await timeline.locator(".rd-timeline-checkpoint > summary").click();
  await expect(timeline.getByText("Handoff at save", { exact: true })).toHaveCount(0);
  await expect(timeline.getByText("Summary update failed", { exact: true })).toBeVisible();
  await expect(timeline.getByText("Not ready", { exact: true })).toHaveCount(0);
  await timeline.getByText("Original prompts (1)", { exact: true }).click();
  await expect(timeline.getByText("add a login form", { exact: true })).toBeVisible();
  await timeline.getByText("Commands (1)", { exact: true }).click();
  await expect(timeline.getByText("npm test", { exact: true })).toBeVisible();
  await expect((await sessionMenu(page)).getByRole("menuitem", { name: "Notes", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit file", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.screenshot({ path: "/tmp/timeline-implemented-dark.png", animations: "disabled" });
  await page.emulateMedia({ colorScheme: "light" });
  await page.screenshot({ path: "/tmp/timeline-implemented-light.png", animations: "disabled" });
  await page.getByRole("button", { name: "Latest checkpoint ↗", exact: true }).click();
  await expect(timeline.locator(".rd-timeline-checkpoint")).toHaveAttribute("open", "");
  await timeline.getByRole("button", { name: "Retry summary update", exact: true }).click();
  await expect.poll(async () => (await checkpoints(key)).length).toBe(2);
  await expect(timeline.locator(".rd-timeline-checkpoint")).toHaveCount(2);
  const retried = await checkpoints(key);
  expect(retried.every(c => c.summary_update?.state === "failed")).toBe(true);
  expect(retried[0].record.prompts).toEqual(cp.record.prompts);
  expect(retried[0].record.commands).toEqual(cp.record.commands);
  await timeline.getByRole("button", { name: "Progress", exact: true }).click();
  await expect(timeline.locator(".rd-timeline-checkpoint")).toHaveCount(0);
  await expect(timeline.getByText("Owner message", { exact: true })).toHaveCount(0);
  await expect(timeline.getByRole("button", { name: "Messages", exact: true })).toBeVisible();
  } finally { await apiDelete(`/sessions/${key}`); }
});
