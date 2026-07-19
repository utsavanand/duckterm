import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { apiPost, base, seedSession } from "./helpers";

// Browser-click coverage for the row/topbar actions that the flow specs don't
// exercise: Approve/Deny, Notes, the AGENTS.md modal, the theme toggle, and
// folder delete.

test("approve button answers a blocking permission request", async ({
  page,
}) => {
  const key = `e2e-approve-${Date.now()}`;
  await seedSession(key, { name: key });

  // A blocking hook registers the request and then polls /decision.
  const reg = await apiPost("/approvals", {
    session_key: key,
    tool_name: "Bash",
    tool_input: { command: "rm -rf build" },
  });
  expect(reg.status).toBe(200);
  const id = reg.body.id as string;
  expect(id).toBeTruthy();

  await page.goto("/");
  const approval = page.locator(".rd-approval", { hasText: key });
  await expect(approval).toBeVisible({ timeout: 10_000 });
  await approval.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved")).toBeVisible();

  // The polling hook sees the decision (what actually unblocks the agent).
  const res = await fetch(`${base()}/approvals/${id}/decision`);
  expect(((await res.json()) as { status: string }).status).toBe("approve");
});

test("notes open, save, and persist on the session", async ({ page }) => {
  const key = `e2e-notes-${Date.now()}`;
  await seedSession(key, { name: key });

  await page.goto("/");
  const row = page.locator(".rd-row", { hasText: key });
  await expect(row).toBeVisible();
  await row.hover();
  await row.getByRole("button", { name: /^Notes/ }).click();

  await row.locator(".rd-row-notes").fill("check the retry logic");
  await row.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Notes saved")).toBeVisible();

  const res = await fetch(`${base()}/sessions/${key}`);
  expect(((await res.json()) as { notes?: string }).notes).toBe(
    "check the retry logic",
  );
});

test("AGENTS.md modal saves the folder's shared instructions", async ({
  page,
}) => {
  const dir = mkdtempSync(join(tmpdir(), "rd-e2e-agentsmd-"));
  const key = `e2e-agentsmd-${Date.now()}`;
  await seedSession(key, { name: key, cwd: dir });

  await page.goto("/");
  // Select the session so the topbar AGENTS.md button targets its folder.
  await page.locator(".rd-row-name", { hasText: key }).click();
  await page.getByRole("button", { name: "AGENTS.md" }).click();

  await page
    .getByPlaceholder(/Shared instructions/)
    .fill("# Shared rules\n\nBe brief.");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("AGENTS.md saved")).toBeVisible();

  expect(readFileSync(join(dir, "AGENTS.md"), "utf8")).toContain("Be brief.");
});

test("theme toggle cycles the theme", async ({ page }) => {
  await page.goto("/");
  const toggle = page.getByRole("button", { name: "Toggle theme" });
  const before = await toggle.getAttribute("title");
  await toggle.click();
  await expect.poll(async () => toggle.getAttribute("title")).not.toBe(before);
});

test("folder ✕ deletes the folder after confirm", async ({ page }) => {
  const name = `Nix-${Date.now()}`;
  await apiPost("/folders", { name });

  await page.goto("/");
  const head = page.locator(".rd-group-head", { hasText: name });
  await expect(head).toBeVisible();

  page.on("dialog", (d) => d.accept());
  await head.locator(".rd-group-del").click();
  await expect(head).toHaveCount(0);

  const res = await fetch(`${base()}/folders`);
  expect(((await res.json()) as { folders: string[] }).folders).not.toContain(
    name,
  );
});
