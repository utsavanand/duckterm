import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { api, apiPost, postEvent, seedSession } from "./helpers";

// Skill kits end to end: add a kit by path on the Skill kits page, install it
// into a workspace from the dry run, and read what a skill does on the kit's
// page. DuckTerm writes the kit's files itself and never runs a kit's own
// script, so a stray install.sh in the kit is never executed.

test("add a skill kit, install it into a workspace and read a skill; the kit's script never runs", async ({
  page,
}) => {
  const kit = mkdtempSync(join(tmpdir(), "rd-e2e-kit-"));
  const project = realpathSync(mkdtempSync(join(tmpdir(), "rd-e2e-kit-project-")));
  const stamp = Date.now();
  mkdirSync(join(kit, "skills", "ship-it"), { recursive: true });
  writeFileSync(
    join(kit, "skills", "ship-it", "SKILL.md"),
    "---\nname: ship-it\ndescription: Reviews, tests, then ships.\n---\n",
  );
  writeFileSync(
    join(kit, "skill-kit.toml"),
    [
      "manifest_version = 1",
      `name = "kit-${stamp}"`,
      'description = "skills and hooks for the e2e test"',
      'version = "0.1.0"',
      "",
      "[[components]]",
      'kind = "skill"',
      'path = "skills/ship-it"',
      "",
    ].join("\n"),
  );
  writeFileSync(join(kit, "install.sh"), "#!/bin/sh\ntouch installed.marker\n", {
    mode: 0o755,
  });
  // A workspace is installable once a session has run in a repository.
  const workspace = `kit-ws-${stamp}`;
  await seedSession(`kit-session-${stamp}`, { name: "kit agent", group: workspace, cwd: project });

  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Skill kits" }).click();
  await page.getByRole("button", { name: "Add skill kit…" }).click();
  await page.getByLabel("Kit folder").fill(kit);
  await page.getByRole("button", { name: "Add", exact: true }).click();

  await expect(page.getByRole("heading", { name: `kit-${stamp}` })).toBeVisible();
  await expect(page.getByText(/Step 2 of 3/)).toBeVisible();
  await page.getByRole("button", { name: "Next: choose workspaces" }).click();
  await page.getByLabel("These workspaces").check();
  await page.getByLabel(workspace, { exact: true }).check();
  await page.getByRole("button", { name: "Review what will change" }).click();

  await expect(page.getByText(/nothing is written until you press Enable/)).toBeVisible();
  expect(existsSync(join(project, ".claude", "skills", "ship-it", "SKILL.md"))).toBe(false);
  await page.getByRole("button", { name: "Enable", exact: true }).click();

  await expect(page.getByRole("heading", { name: `kit-${stamp} is enabled` })).toBeVisible();
  await page.getByRole("button", { name: `Open kit-${stamp}` }).click();
  await expect(page.getByRole("heading", { name: `kit-${stamp}` })).toBeVisible();
  await expect(page.locator(".rd-kits-row", { hasText: workspace })).toContainText("claude-code");
  await page.locator(".rd-kits-group > summary", { hasText: "Skills" }).click();
  await page.locator(".rd-kits-skill > summary", { hasText: "/ship-it" }).click();
  await expect(page.getByText("Reviews, tests, then ships.")).toBeVisible();

  expect(existsSync(join(project, ".claude", "skills", "ship-it", "SKILL.md"))).toBe(true);
  expect(existsSync(join(kit, "installed.marker"))).toBe(false);
  expect(existsSync(join(project, "installed.marker"))).toBe(false);
});

// At phone width the dry-run matrix keeps a readable first column and scrolls
// sideways; before, a 390px viewport wrapped "skill · review-and-ship" one
// letter per line. The screenshots are the owner's preview of the narrow view.
test("the dry-run matrix stays readable at 390px in both themes", async ({ page }, testInfo) => {
  const kit = mkdtempSync(join(tmpdir(), "rd-e2e-kit-"));
  const project = realpathSync(mkdtempSync(join(tmpdir(), "rd-e2e-kit-project-")));
  const stamp = Date.now();
  mkdirSync(join(kit, "skills", "review-and-ship"), { recursive: true });
  mkdirSync(join(kit, "teams"));
  writeFileSync(
    join(kit, "skills", "review-and-ship", "SKILL.md"),
    "---\nname: review-and-ship\ndescription: Reviews, tests, then ships.\n---\n",
  );
  writeFileSync(
    join(kit, "teams", "release-squad.toml"),
    [
      'name = "release-squad"',
      'description = "A reviewer beside a builder"',
      "[roles.dev]",
      'card = { purpose = "Builds" }',
      "[roles.reviewer]",
      'card = { purpose = "Reviews" }',
      "",
    ].join("\n"),
  );
  writeFileSync(
    join(kit, "skill-kit.toml"),
    [
      "manifest_version = 1",
      `name = "kit-${stamp}"`,
      'description = "a skill and a team, for the narrow matrix"',
      'version = "0.1.0"',
      'teams = ["teams/release-squad.toml"]',
      "",
      "[[components]]",
      'kind = "skill"',
      'path = "skills/review-and-ship"',
      "",
    ].join("\n"),
  );
  const workspace = `kit-ws-${stamp}`;
  await seedSession(`kit-session-${stamp}`, { name: "kit agent", group: workspace, cwd: project });

  await page.setViewportSize({ width: 390, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Skill kits" }).click();
  await page.getByRole("button", { name: "Add skill kit…" }).click();
  await page.getByLabel("Kit folder").fill(kit);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Next: choose workspaces" }).click();
  await page.getByLabel("These workspaces").check();
  await page.getByLabel(workspace, { exact: true }).check();
  await page.getByRole("button", { name: "Review what will change" }).click();
  await expect(page.getByText(/nothing is written until you press Enable/)).toBeVisible();

  const first = page.locator(".rd-kits-plan td").first();
  await expect(first).toContainText("skill · review-and-ship");
  const cell = await first.boundingBox();
  expect(cell?.width ?? 0).toBeGreaterThanOrEqual(150);
  const name = await first.locator(".rd-kits-mono").boundingBox();
  expect(name?.height ?? 0).toBeLessThan(30); // one line, not one letter per line
  const matrix = page.locator(".rd-kits-plan").first();
  const scrolls = await matrix.evaluate((el) => el.scrollWidth > el.clientWidth);
  expect(scrolls).toBe(true);
  const pageWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(pageWidth).toBe(390);

  await page.screenshot({ path: testInfo.outputPath("review-390-light.png"), fullPage: true });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({ path: testInfo.outputPath("review-390-dark.png"), fullPage: true });
  rmSync(kit, { recursive: true, force: true });
});

// Browse lives inside the Add form: opening it, moving around and picking a
// manifest must never submit the form. Only the Add button (or Enter in the
// field) registers a kit. The browser is confined to the home folder, so the
// fixture lives under it and is removed afterwards.
test("Browse offers each manifest of a folder and registers nothing until Add is pressed", async ({
  page,
}) => {
  const stamp = Date.now();
  const parent = join(homedir(), ".cache", "duckterm-e2e");
  mkdirSync(parent, { recursive: true });
  const kit = mkdtempSync(join(parent, `kits-${stamp}-`));
  try {
    mkdirSync(join(kit, "skills", "ship-it"), { recursive: true });
    writeFileSync(
      join(kit, "skills", "ship-it", "SKILL.md"),
      "---\nname: ship-it\ndescription: Ships.\n---\n",
    );
    const manifest = (name: string) =>
      [
        "manifest_version = 1",
        `name = "${name}"`,
        'description = "two kits in one folder"',
        'version = "0.1.0"',
        "",
        "[[components]]",
        'kind = "skill"',
        'path = "skills/ship-it"',
        "",
      ].join("\n");
    writeFileSync(join(kit, "skill-kit.toml"), manifest(`main-${stamp}`));
    writeFileSync(join(kit, "skill-kit.alt.toml"), manifest(`alt-${stamp}`));
    const registers: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url().includes("/kits/register")) registers.push(r.postData() ?? "");
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Skill kits" }).click();
    await page.getByRole("button", { name: "Add skill kit…" }).click();
    await page.getByLabel("Kit folder").fill(kit);
    await page.getByRole("button", { name: "Browse…" }).click();
    await expect(page.getByRole("button", { name: `Use alt-${stamp}` })).toBeVisible();
    await expect(page.getByRole("button", { name: `Use main-${stamp}` })).toBeVisible();
    expect(registers).toEqual([]);
    await page.getByRole("button", { name: `Use alt-${stamp}` }).click();
    await expect(page.getByLabel("Kit folder")).toHaveValue(join(kit, "skill-kit.alt.toml"));
    expect(registers).toEqual([]);
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.getByRole("heading", { name: `alt-${stamp}` })).toBeVisible();
    await expect(page.getByText(/Step 2 of 3/)).toBeVisible();
    expect(registers).toHaveLength(1);
    expect(registers[0]).toContain("skill-kit.alt.toml");
  } finally {
    rmSync(kit, { recursive: true, force: true });
  }
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

  // Proposals arrive as typed candidates awaiting review.
  const candidate = page.locator(".ar-proposal", {
    hasText: "Use rg, not grep",
  });
  await expect(candidate).toBeVisible({ timeout: 10_000 });

  // Accept one, reject the other; save. Only the accepted rule renders into
  // AGENTS.md; review history stays local and is returned through the API.
  await candidate.getByRole("button", { name: "Accept change" }).click();
  await page
    .locator(".ar-proposal", { hasText: "No emoji" })
    .getByRole("button", { name: "Dismiss" })
    .click();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Rules saved")).toBeVisible();

  expect(readFileSync(join(dir, "AGENTS.md"), "utf8")).toContain(
    "Use rg, not grep",
  );
  const rules = JSON.parse(
    readFileSync(join(dir, ".duckterm-rules.json"), "utf8"),
  ) as { rules: { text: string; status: string }[] };
  expect(rules.rules.map((r) => r.status).sort()).toEqual([
    "active",
  ]);
  const saved = JSON.parse(readFileSync(join(dir, ".duckterm-rules.json"), "utf8"));
  expect(saved).not.toHaveProperty("proposals");
  expect(saved).not.toHaveProperty("capture_fingerprint");
  const review = await (await api(`/agents-md?dir=${encodeURIComponent(dir)}`)).json();
  expect(review.proposals.map((p: { status: string }) => p.status).sort()).toEqual(["accepted", "rejected"]);
  await apiPost(`/sessions/${key}/delete`);
  rmSync(dir, { recursive: true, force: true });
});
