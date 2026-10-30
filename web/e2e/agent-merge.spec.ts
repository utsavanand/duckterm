import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { apiDelete, apiPatch, apiPost, base, expandFolder, postEvent, seedSession } from "./helpers";

test("configure a root-to-agent merge, recover a lost response and preserve source memory", async ({ page }) => {
  const folder = `Merge agents ${Date.now()}`, source = `agent-source-${Date.now()}`, target = `agent-target-${Date.now()}`;
  try {
    await seedSession(target, { name: "Main development", runtime: "generic", group: folder, test: true });
    await seedSession(source, { launched: true, name: "Feature implementation", runtime: "generic", group: folder, test: true });
    await postEvent({ event_type: "Notification", session_key: source, lifecycle: "stopped" });
    await apiPatch(`/sessions/${source}`, { notes: "Preserve the public API." });
    await apiPatch(`/sessions/${target}`, { notes: "Existing destination notes." });
    expect((await apiPost(`/sessions/${target}/collaboration`, { root: folder })).status).toBe(200);
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto(base()); await expandFolder(page, folder);
    await page.locator(".rd-row-name", { hasText: "Main development" }).click();
    const row = page.locator(".rd-row", { has: page.getByText("Feature implementation", { exact: true }) });
    await row.click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Merge back", exact: true })).toHaveCount(0);
    await page.getByRole("menuitem", { name: "Merge with agent", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Merge with agent", exact: true });
    await expect(dialog.getByLabel("Destination agent").locator(`option[value="${target}"]`)).toBeEnabled();
    await dialog.getByLabel("Destination agent").selectOption(target);
    await expect(dialog.getByLabel("Notes to append")).toHaveValue("Preserve the public API.");
    await dialog.getByRole("checkbox", { name: /Conversation context/ }).check();
    const context = "Completed sign-in validation. 🦆 Remaining: verify expiry. \n";
    await dialog.getByLabel("Context to send").fill(context);
    await expect(dialog.getByRole("checkbox", { name: /Code from/ })).toBeDisabled();
    await expect(row).toHaveClass(/context-target/);
    await expect(page.locator(".rd-row.selected")).toContainText("Main development");
    await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    await page.screenshot({ path: "/tmp/duckterm-agent-merge-configure.png" });
    await dialog.getByRole("button", { name: "Review merge", exact: true }).click();
    await expect(dialog.getByText(/receives the request in its inbox/)).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await dialog.getByRole("button", { name: /^Merge and close / }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/duckterm-agent-merge-review-narrow.png" });
    let sent = 0;
    await page.route(`**/sessions/${source}/agent-merge`, async route => {
      if (route.request().method() === "POST" && sent++ === 0) { await route.fetch(); await route.abort("failed"); }
      else await route.continue();
    });
    await dialog.getByRole("button", { name: /^Merge and close / }).click();
    await expect(dialog.getByRole("alert")).toContainText("Retry unchanged");
    await dialog.getByRole("button", { name: /^Merge and close / }).click();
    await expect(dialog.getByRole("region", { name: "Saved merge request" })).toBeVisible();
    await expect(dialog.getByText(/Notes appended once/)).toBeVisible();
    await dialog.getByRole("button", { name: "Done", exact: true }).click();
    await page.reload(); await expandFolder(page, folder);
    await page.locator(".rd-row-name", { hasText: "Main development" }).click();
    await page.getByRole("button", { name: "Timeline", exact: true }).click();
    await page.getByText(/Feature implementation → Main development/).click();
    await page.getByRole("button", { name: "Open source memory ↗" }).click();
    const memory = page.getByRole("dialog", { name: "Source memory", exact: true });
    await expect(memory.getByText(/No saved summary yet/)).toBeVisible();
    await expect(memory.getByRole("button", { name: "Checkpoint", exact: true })).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-linked-source-memory.png" });
    await memory.getByRole("button", { name: "Back", exact: true }).click();
    const data = await (await page.request.get(`${base()}/sessions`)).json();
    const destination = data.sessions.find((s: { session_key: string }) => s.session_key === target);
    expect(destination.notes).toBe(`Existing destination notes.\n\nFrom Feature implementation (${source}):\nPreserve the public API.`);
    expect(data.sessions.find((s: { session_key: string }) => s.session_key === source).state).toBe("merged");
  } finally { await apiDelete(`/sessions/${source}`); await apiDelete(`/sessions/${target}`); await apiDelete(`/folders/${encodeURIComponent(folder)}`); }
});


test("different worktrees offer the exact committed code for review without changing either worktree", async ({ page }) => {
  const root = mkdtempSync(join(tmpdir(), "duckterm-merge-code-"));
  const repo = join(root, "main"), branch = join(root, "feature");
  const git = (path: string, ...args: string[]) => execFileSync("git", ["-C", path, ...args], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  const source = `code-source-${Date.now()}`, target = `code-target-${Date.now()}`, folder = `Code merge ${Date.now()}`;
  try {
    mkdirSync(repo); git(repo, "init", "-b", "main");
    git(repo, "config", "user.name", "QA"); git(repo, "config", "user.email", "qa@example.test");
    writeFileSync(join(repo, "base.txt"), "base"); git(repo, "add", "base.txt"); git(repo, "commit", "-m", "base");
    git(repo, "worktree", "add", "-b", "feature", branch);
    writeFileSync(join(branch, "feature.txt"), "reviewed feature"); git(branch, "add", "feature.txt"); git(branch, "commit", "-m", "feature");
    const original = git(repo, "rev-parse", "HEAD"), reviewed = git(branch, "rev-parse", "HEAD");
    await seedSession(target, { name: "Main worktree", cwd: repo, runtime: "generic", group: folder, test: true });
    await seedSession(source, { launched: true, name: "Feature worktree", cwd: branch, runtime: "generic", group: folder, test: true, parent_session_key: target });
    expect((await apiPost(`/sessions/${target}/collaboration`, { root: folder })).status).toBe(200);
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto(base()); await expandFolder(page, folder);
    await page.locator(".rd-row", { has: page.getByText("Feature worktree", { exact: true }) }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Merge with agent", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Merge with agent" });
    await expect(dialog.getByLabel("Destination agent").locator(`option[value="${target}"]`)).toBeEnabled();
    await dialog.getByLabel("Destination agent").selectOption(target);
    const option = dialog.getByRole("checkbox", { name: /Code from/ });
    await expect(option).toBeEnabled(); await expect(option).not.toBeChecked();
    await option.check();
    await expect(dialog.locator("code")).toContainText(reviewed);
    await expect(dialog.locator("pre")).toContainText("feature.txt");
    await dialog.getByRole("button", { name: "Review merge", exact: true }).click();
    await page.screenshot({ path: "/tmp/duckterm-agent-merge-code-review.png" });
    await dialog.getByRole("button", { name: /^Merge and close /, exact: true }).click();
    await expect(dialog.getByText(/Code integration was requested/)).toBeVisible();
    expect(git(repo, "rev-parse", "HEAD")).toBe(original);
    expect(git(branch, "rev-parse", "HEAD")).toBe(reviewed);
    expect(git(repo, "status", "--porcelain")).toBe("");
  } finally {
    await apiDelete(`/sessions/${source}`); await apiDelete(`/sessions/${target}`); await apiDelete(`/folders/${encodeURIComponent(folder)}`);
    rmSync(root, { recursive: true, force: true });
  }
});

// Exercise the real merge UI and existing count selector without calling a model.
test("empty context recovery keeps drafts, isolates count selection, and never auto-sends", async ({ page }) => {
  const folder = `Merge recovery ${Date.now()}`, source = `recovery-source-${Date.now()}`, target = `recovery-target-${Date.now()}`;
  let checkpointCalls = 0, mergeCalls = 0, generated = false;
  try {
    await seedSession(target, { name: "Recovery main", runtime: "generic", group: folder, test: true });
    await seedSession(source, { launched: true, name: "Recovery notes", runtime: "generic", group: folder, test: true, parent_session_key: target });
    expect((await apiPost(`/sessions/${target}/collaboration`, { root: folder })).status).toBe(200);
    await page.route(`**/sessions/${source}/checkpoint-plan`, route => route.fulfill({ json: { available_records: 8, processed_records: 0, pending_records: 8, default_records: 20, selected_records: 8, remaining_records: 0, batch_records: 20, order: "oldest_unprocessed_first", selection_plan: { source: "fixture" } } }));
    await page.route(`**/sessions/${source}/checkpoint`, async route => {
      checkpointCalls++; generated = true;
      await route.fulfill({ json: { id: "fixture-checkpoint", saved: true, summary_update: { state: "updated" }, record: {}, summary: "Do not use response display text" } });
    });
    await page.route(`**/sessions/${source}/agent-merge**`, async route => {
      if (route.request().method() === "POST" && new URL(route.request().url()).pathname.endsWith("/agent-merge")) { mergeCalls++; await route.abort(); return; }
      const response = await route.fetch(); const data = await response.json();
      if (generated && data.source && data.revision) { data.context = "Canonical saved summary after checkpoint"; data.revision = "after-checkpoint"; }
      await route.fulfill({ response, json: data });
    });
    await page.setViewportSize({ width: 1440, height: 1050 }); await page.goto(base()); await expandFolder(page, folder);
    await page.locator(".rd-row", { has: page.getByText("Recovery notes", { exact: true }) }).click({ button: "right" });
    await page.getByRole("menuitem", { name: "Merge with agent", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Merge with agent", exact: true });
    await expect(dialog.getByLabel("Destination agent").locator(`option[value="${target}"]`)).toBeEnabled();
    await dialog.getByLabel("Destination agent").selectOption(target);
    await expect(dialog.getByText(/No saved conversation summary/)).toBeVisible();
    await dialog.getByRole("checkbox", { name: /^Conversation context/ }).check();
    await dialog.getByRole("checkbox", { name: /^Notes/ }).check();
    await expect(dialog.getByLabel("Context to send")).toHaveAttribute("aria-invalid", "true");
    await expect(dialog.getByLabel("Notes to append")).toHaveAttribute("aria-invalid", "true");
    await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    // Check the rendered secondary buttons in both palettes: a dark-only surface
    // token previously made the light-theme label nearly invisible.
    for (const theme of ["dark", "light"]) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      const contrast = () => dialog.getByRole("button", { name: "Generate summary…" }).evaluate(button => {
        const style = getComputedStyle(button);
        const luminance = (color: string) => {
          const channels = color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(value => {
            const channel = value / 255;
            return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
          });
          return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
        };
        const foreground = luminance(style.color), background = luminance(style.backgroundColor);
        return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
      });
      await expect.poll(contrast, { message: `${theme} secondary button contrast after transition` }).toBeGreaterThanOrEqual(4.5);
    }
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    await page.screenshot({ path: "/tmp/merge-recovery-app-dark.png" });
    await dialog.getByRole("button", { name: "Generate summary…" }).click();
    const chooser = page.getByRole("dialog", { name: "Checkpoint", exact: true });
    await expect(chooser.getByRole("button", { name: "Checkpoint 8 records" })).toBeEnabled();
    await expect(dialog).toHaveCount(0);
    await page.keyboard.press("Escape"); await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Generate summary…" })).toBeFocused();
    expect(checkpointCalls).toBe(0);
    await dialog.getByLabel("Notes to append").fill("Keep this owner draft.");
    await dialog.getByRole("button", { name: "Generate summary…" }).click();
    await chooser.getByRole("button", { name: "Checkpoint 8 records" }).click();
    await expect(dialog.getByLabel("Context to send")).toHaveValue("Canonical saved summary after checkpoint");
    await expect(dialog.getByLabel("Notes to append")).toHaveValue("Keep this owner draft.");
    expect(checkpointCalls).toBe(1); expect(mergeCalls).toBe(0);
    await dialog.getByRole("button", { name: "Review merge", exact: true }).click();
    await expect(dialog.locator("pre").first()).toHaveText("Canonical saved summary after checkpoint");
    expect(mergeCalls).toBe(0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await dialog.getByRole("button", { name: /^Merge and close / }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: "/tmp/merge-recovery-app-light-narrow.png" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    expect(mergeCalls).toBe(0);
  } finally {
    await apiDelete(`/sessions/${source}`); await apiDelete(`/sessions/${target}`); await apiDelete(`/folders/${encodeURIComponent(folder)}`);
  }
});


test("a child can merge back to its parent or send context to another agent", async ({ page }) => {
  const id = Date.now(), folder = `Both merge actions ${id}`;
  const source = `merge-child-${id}`, parent = `merge-parent-${id}`, target = `merge-other-${id}`;
  try {
    await seedSession(parent, { name: "Recorded parent", runtime: "generic", group: folder, test: true });
    await seedSession(target, { name: "Unrelated destination", runtime: "generic", group: folder, test: true });
    await seedSession(source, { launched: true, name: "Child with both actions", runtime: "generic", group: folder, test: true, parent_session_key: parent });
    for (const key of [parent, target]) expect((await apiPost(`/sessions/${key}/collaboration`, { root: folder })).status).toBe(200);
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto(base()); await expandFolder(page, folder);
    const row = page.locator(".rd-row", { has: page.getByText("Child with both actions", { exact: true }) });
    await row.click({ button: "right" });
    await expect(page.getByRole("menuitem", { name: "Merge with agent", exact: true })).toBeEnabled();
    await page.getByRole("menuitem", { name: "Merge back", exact: true }).click();
    const back = page.getByRole("dialog", { name: "Merge back to parent", exact: true });
    await expect(back.getByText("Recorded parent", { exact: true })).toBeVisible();
    await expect(back.getByRole("combobox")).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-restored-merge-back.png" });
    await back.getByRole("button", { name: "Cancel", exact: true }).click();
    await row.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Merge with agent", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Merge with agent", exact: true });
    await expect(dialog.getByLabel("Destination agent").locator(`option[value="${target}"]`)).toBeEnabled();
    await dialog.getByLabel("Destination agent").selectOption(target);
    await dialog.getByRole("checkbox", { name: /^Conversation context/ }).check();
    await dialog.getByLabel("Context to send").fill("Reviewed context for the unrelated destination.");
    await expect(dialog.getByRole("checkbox", { name: /^Notes/ })).not.toBeChecked();
    await page.screenshot({ path: "/tmp/duckterm-restored-general-merge.png" });
    await dialog.getByRole("button", { name: "Review merge", exact: true }).click();
    await expect(dialog.locator(".rd-merge-route")).toHaveText("Child with both actions→Unrelated destination");
    await dialog.getByRole("button", { name: /^Merge and close /, exact: true }).click();
    await expect(dialog.getByRole("region", { name: "Saved merge request" })).toContainText("Unrelated destination");
    const data = await (await page.request.get(`${base()}/sessions`)).json();
    for (const key of [source, parent, target]) expect(data.sessions.find((s: { session_key: string }) => s.session_key === key).state).not.toBe("merged");
  } finally {
    for (const key of [source, target, parent]) await apiDelete(`/sessions/${key}`);
    await apiDelete(`/folders/${encodeURIComponent(folder)}`);
  }
});

// Presentation fixture: authorization and retained-record integrity use backend tests.
test("a destination opens the handed-off source revision and evidence, with explicit deletion errors", async ({ page }) => {
  const target = `linked-reader-${Date.now()}`;
  let writes = 0;
  const reads: string[] = [];
  try {
    await seedSession(target, { name: "Linked memory destination", test: true });
    await page.route(`**/sessions/${target}/agent-merges`, route => route.fulfill({ json: { merges: [{ id: "merge", source: "closed-source", sourceName: "Closed child", target, targetName: "Linked memory destination", memorySource: "closed-source", summaryRevision: "reviewed-revision", closeSource: true, sourceClosed: true, createdAt: Date.now(), delivery: "delivered", context: "Selected context" }] } }));
    await page.route("**/sessions/closed-source/memory**", route => {
      if (route.request().method() !== "GET") { writes++; return route.abort(); }
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/read")) return route.fulfill({ json: { text: "Original decision: preserve the memory link after closing.", next_offset: null } });
      const revision = url.searchParams.get("revision") || "newer-revision"; reads.push(revision);
      return route.fulfill({ json: { state: "available", revisions: [{ id: "reviewed-revision", created_at: 1000 }, { id: "newer-revision", created_at: 2000 }], checkpoint_clock: { state: "never" }, revision: { id: revision, created_at: 1000, summary: revision === "reviewed-revision" ? "The exact summary handed over." : "Later saved work.", groups: [{ field: "decisions", claims: [{ text: "Preserve memory links", record: "decision", evidence: [{ source: "conversation:child", record: "turn-1", title: "Original decision", kind: "conversation", role: "user", retained: true }] }] }] } } });
    });
    await page.setViewportSize({ width: 1440, height: 1000 }); await page.goto(base());
    await page.locator(".rd-row", { hasText: "Linked memory destination" }).locator(".rd-row-click").click();
    await page.getByRole("button", { name: "Timeline", exact: true }).click();
    await page.getByText(/Closed child → Linked memory destination/).click();
    await page.getByRole("button", { name: "Open source memory ↗" }).click();
    const memory = page.getByRole("dialog", { name: "Source memory", exact: true });
    await expect(memory.getByText("The exact summary handed over.")).toBeVisible();
    await memory.getByText("Original decision · conversation").click();
    await expect(memory.getByText("Original decision: preserve the memory link after closing.")).toBeVisible();
    await expect(memory.getByRole("button", { name: "Checkpoint", exact: true })).toHaveCount(0);
    await page.screenshot({ path: "/tmp/duckterm-linked-memory-evidence.png" });
    await memory.getByRole("combobox").selectOption("newer-revision");
    await expect(memory.getByText("Later saved work.")).toBeVisible();
    expect(reads).toEqual(["reviewed-revision", "newer-revision"]);
    await page.route("**/sessions/closed-source/memory**", route => route.fulfill({ status: 404, json: { error: "Source session was deleted; its memory is unavailable" } }));
    await memory.getByRole("combobox").selectOption("reviewed-revision");
    await expect(memory.getByRole("alert")).toContainText("Source session was deleted");
    await expect(memory.getByText("Later saved work.")).toHaveCount(0);
    expect(writes).toBe(0);
  } finally { await apiDelete(`/sessions/${target}`); }
});
