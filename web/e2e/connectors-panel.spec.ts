import { expect, test } from "@playwright/test";
import { apiDelete, apiPost } from "./helpers";

// Proof of life in the Connectors tab: the owner asked "I don't know if I can
// really use them", so a row must state what the connector serves and when it
// was last used, and Check now must report a measured tool count rather than
// the presence of a config entry. Panel height and reachability are covered by
// context-views.spec.ts, which owns the Session/Connectors tab layout.

// Shape of a connected GitHub row. Both doors open; tests vary one field at a
// time so a failure names the door that broke rather than the whole payload.
const connected = {
  name: "github", title: "GitHub", description: "Repos, PRs, issues",
  credential: "gh-cli", identity: "Local test account", sources: ["gh-cli", "stored"],
  revoke_url: "https://github.com/settings/tokens",
  installed: { "claude-code": true, codex: true },
  harnesses: ["claude-code", "codex"],
  harnesses_present: { "claude-code": true, codex: true },
  enabled: true, duckterm_uses: true, agents_use: true,
  agents_off_by_choice: false, unreachable_harnesses: [],
  runner_ready: true, ready: true, managed: false, detail: null,
  last_used: null, use_count: 0,
};

async function launch(name: string): Promise<string> {
  const result = await apiPost("/sessions/launch", {
    command: "python3 -u -c 'import sys; print(\"READY\"); [line for line in sys.stdin]'",
    cwd: "/tmp",
    name,
    in_terminal: false,
    test: true,
  });
  expect(result.status).toBe(200);
  return String(result.body.session_key);
}

async function openPanel(page: import("@playwright/test").Page, name: string) {
  await page.goto("/");
  await page.locator(".rd-row-name", { hasText: name }).click();
  const pane = page.locator(".rd-context-pane");
  await pane.getByRole("tab", { name: "Connectors", exact: true }).click();
  return pane;
}

test("a connector row proves what it serves and when it was last used", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const key = await launch("connector-proof-check");
  try {
    await page.route("**/connectors", route => route.fulfill({
      json: { connectors: [{ ...connected, last_used: Date.now() - 2 * 3600 * 1000, use_count: 90 }] },
    }));
    await page.route("**/connectors/github/verify", route => route.fulfill({
      json: { name: "github", ok: true, tools: 45, detail: null },
    }));

    const pane = await openPanel(page, "connector-proof-check");
    const row = pane.locator(".rd-card", { hasText: "GitHub" });
    // Before checking, the panel must not imply it verified anything.
    await expect(row.getByText(/Not verified/)).toBeVisible();
    await expect(row.getByText(/Used 2h ago · 90 calls/)).toBeVisible();

    await row.getByRole("button", { name: "Check now" }).click();
    await expect(row.getByText(/✓ 45 tools/)).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});

test("a failing connector shows the server's own reason, not a generic error", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const key = await launch("connector-failure-check");
  try {
    await page.route("**/connectors", route => route.fulfill({ json: { connectors: [connected] } }));
    await page.route("**/connectors/github/verify", route => route.fulfill({
      json: { name: "github", ok: false, tools: 0, detail: "Protocol version rejected" },
    }));

    const pane = await openPanel(page, "connector-failure-check");
    const row = pane.locator(".rd-card", { hasText: "GitHub" });
    // Silence in the event log is not evidence the connector went unused.
    await expect(row.getByText(/No recorded use/)).toBeVisible();

    await row.getByRole("button", { name: "Check now" }).click();
    await expect(row.getByText(/Protocol version rejected/)).toBeVisible();
    await expect(row.getByText(/✓ Verified/)).toHaveCount(0);
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});

test("the two doors report independently when agents are turned off", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const key = await launch("connector-doors-check");
  try {
    // The bug the panel was rebuilt to fix: one "disabled" line stood for two
    // independent facts, so withdrawing from agents read as Duckterm having
    // dropped the credential too.
    await page.route("**/connectors", route => route.fulfill({
      json: { connectors: [{ ...connected, enabled: false, agents_use: false, agents_off_by_choice: true }] },
    }));

    const pane = await openPanel(page, "connector-doors-check");
    const row = pane.locator(".rd-card", { hasText: "GitHub" });
    // The agents door is the switch, off; Duckterm's is stated beside it.
    await expect(row.getByRole("switch", { name: /Share GitHub with agents/ }))
      .toHaveAttribute("aria-checked", "false");
    await expect(row.getByText(/Duckterm still uses this credential/)).toBeVisible();
    // Sharing is the reversible half; the credential survives it, so the
    // card still offers Sign out rather than having signed out for you.
    await expect(row.getByRole("button", { name: "Sign out" })).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});

test("a lost harness entry reads differently from the owner turning agents off", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const key = await launch("connector-lost-entry-check");
  try {
    // Same enabled: false, different cause and different fix, so the panel must
    // not describe a vanished registration in the Disable wording.
    await page.route("**/connectors", route => route.fulfill({
      json: { connectors: [{ ...connected, enabled: false, agents_use: false, agents_off_by_choice: false, unreachable_harnesses: ["codex"] }] },
    }));

    const pane = await openPanel(page, "connector-lost-entry-check");
    const row = pane.locator(".rd-card", { hasText: "GitHub" });
    await expect(row.getByText(/Codex has no entry for it/)).toBeVisible();
    await expect(row.getByText(/Your login stays saved/)).toHaveCount(0);
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});

test("the connect flow and its editor fit the sidebar", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const key = await launch("connector-layout-check");
  try {
    await page.route("**/connectors", route => route.fulfill({
      json: {
        connectors: [{
          ...connected, credential: null, identity: null,
          enabled: false, duckterm_uses: false, agents_use: false,
          agents_off_by_choice: true, ready: false, installed: {},
        }],
      },
    }));
    const pane = await openPanel(page, "connector-layout-check");
    const row = pane.locator(".rd-card", { hasText: "GitHub" });

    // The CLI login is the primary path and needs no editor at all.
    await expect(row.getByRole("button", { name: "Connect" })).toBeVisible();
    await row.getByRole("button", { name: "Paste a token" }).click();

    for (const width of [1440, 1024]) {
      await page.setViewportSize({ width, height: 900 });
      const bounds = await pane.boundingBox();
      for (const control of [
        row.getByRole("button", { name: "Close GitHub connection settings" }),
        row.getByLabel("GitHub credential source"),
        row.getByLabel("GitHub personal access token"),
        row.getByRole("button", { name: "Verify and share" }),
      ]) {
        // Horizontal fit is the subject. In the stacked layout below 1100px the
        // pane is a short grid row, so the editor is taller than it and the
        // submit button sits below the fold until the list is scrolled -- which
        // is the panel working, not a defect. Scroll first, then measure.
        await control.scrollIntoViewIfNeeded();
        await expect(control).toBeInViewport();
        const box = await control.boundingBox();
        expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
        expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
      }
    }
    await page.screenshot({ path: testInfo.outputPath("connector-editor.png") });
    await row.getByRole("button", { name: "Close GitHub connection settings" }).click();
    await expect(row.getByLabel("GitHub credential source")).toHaveCount(0);
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});
