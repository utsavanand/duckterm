import { chmodSync, existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { postEvent, seedSession } from "./helpers";

// Installable harnesses end to end: register a suite by path in the Harnesses
// modal, install it into a project folder, and verify the installer really ran
// there (marker file) with its output shown in the modal.

test("register, install with a picker choice, then uninstall", async ({
  page,
}) => {
  const suite = mkdtempSync(join(tmpdir(), "rd-e2e-suite-"));
  writeFileSync(
    join(suite, "duckterm-harness.json"),
    JSON.stringify({
      name: `kit-${Date.now()}`,
      description: "skills and hooks for the e2e test",
      install: ["./install.sh"],
      uninstall: ["./uninstall.sh"],
      args_choices: { "--persona": ["sport", "professional"] },
    }),
  );
  writeFileSync(
    join(suite, "install.sh"),
    '#!/bin/sh\necho "installed into $PWD with $*"\ntouch installed.marker\n',
  );
  writeFileSync(
    join(suite, "uninstall.sh"),
    '#!/bin/sh\nrm -f installed.marker\necho "uninstalled"\n',
  );
  chmodSync(join(suite, "install.sh"), 0o755);
  chmodSync(join(suite, "uninstall.sh"), 0o755);
  const target = mkdtempSync(join(tmpdir(), "rd-e2e-target-"));

  await page.goto("/");
  await page.getByRole("button", { name: "Harnesses" }).click();

  await page.getByPlaceholder("~/ws-my-projects/uv-suite").fill(suite);
  await page.getByRole("button", { name: "Register", exact: true }).click();
  const row = page.locator(".rd-harness", { hasText: "skills and hooks" });
  await expect(row).toBeVisible();

  // Pick a persona from the manifest-declared choices, then install.
  await row.getByPlaceholder("target project folder").fill(target);
  await row.locator("select").selectOption("sport");
  await row.getByRole("button", { name: "Install", exact: true }).click();

  await expect(row.locator(".rd-harness-output")).toContainText(
    "--persona sport",
    { timeout: 10_000 },
  );
  expect(existsSync(join(target, "installed.marker"))).toBe(true);

  // Uninstall reverses it, with its own output shown.
  await row.getByRole("button", { name: "Uninstall", exact: true }).click();
  await expect(row.locator(".rd-harness-output")).toContainText("uninstalled", {
    timeout: 10_000,
  });
  expect(existsSync(join(target, "installed.marker"))).toBe(false);
});

// The observation loop: corrections seeded for a folder come back as proposed
// AGENTS.md rules (the e2e server's LLM backend is a deterministic script).
test("AGENTS.md suggests rules from observed corrections", async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), "rd-e2e-observe-"));
  const key = `e2e-observe-${Date.now()}`;
  await seedSession(key, { name: key, cwd: dir });
  await postEvent({
    event_type: "UserPromptSubmit",
    session_key: key,
    prompt: "build the exporter",
  });
  await postEvent({
    event_type: "UserPromptSubmit",
    session_key: key,
    prompt: "use rg instead of grep please",
  });

  await page.goto("/");
  await page.locator(".rd-row-name", { hasText: key }).click();
  await page.getByRole("button", { name: "AGENTS.md" }).click();
  await page.getByRole("button", { name: "Suggest from corrections" }).click();

  const editor = page.getByPlaceholder(/Shared instructions/);
  await expect(editor).toHaveValue(/Suggested from corrections/, {
    timeout: 10_000,
  });
  await expect(editor).toHaveValue(/- Use rg, not grep/);

  // Saving lands the reviewed rules in the folder's AGENTS.md.
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("AGENTS.md saved")).toBeVisible();
});
