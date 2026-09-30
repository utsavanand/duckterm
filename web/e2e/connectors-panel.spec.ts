import { expect, test } from "@playwright/test";
import { apiDelete, apiPost } from "./helpers";

// Proof of life in the Connectors tab: the owner asked "I don't know if I can
// really use them", so a row must state what the connector serves and when it
// was last used, and Check now must report a measured tool count rather than
// the presence of a config entry. Panel height and reachability are covered by
// context-views.spec.ts, which owns the Session/Connectors tab layout.
test("a connector row proves what it serves and when it was last used", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const result = await apiPost("/sessions/launch", {
    command: "python3 -u -c 'import sys; print(\"READY\"); [line for line in sys.stdin]'",
    cwd: "/tmp",
    name: "connector-proof-check",
    in_terminal: false,
    test: true,
  });
  expect(result.status).toBe(200);
  const key = String(result.body.session_key);
  try {
    await page.route("**/connectors", route => route.fulfill({
      json: {
        connectors: [{
          name: "github", title: "GitHub", description: "Repos, PRs, issues",
          credential: "gh-cli", identity: "Local test account", sources: ["gh-cli"],
          write_access: false, revoke_url: "https://github.com/settings/applications",
          installed: { "claude-code": true, codex: true }, enabled: true, ready: true,
          detail: null, last_used: Date.now() - 2 * 3600 * 1000, use_count: 90,
        }],
      },
    }));
    await page.route("**/connectors/github/verify", route => route.fulfill({
      json: { name: "github", ok: true, tools: 45, detail: null },
    }));

    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: "connector-proof-check" }).click();
    const pane = page.locator(".rd-context-pane");
    await pane.getByRole("tab", { name: "Connectors", exact: true }).click();

    const row = pane.locator(".rd-connector", { hasText: "GitHub" });
    // Before checking, the panel must not imply it verified anything.
    await expect(row.getByText(/not verified this session/)).toBeVisible();
    await expect(row.getByText(/Used 2h ago · 90 calls/)).toBeVisible();

    await row.getByRole("button", { name: "Check now" }).click();
    await expect(row.getByText(/Verified — 45 tools available/)).toBeVisible();
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});

test("a failing connector shows the server's own reason, not a generic error", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const result = await apiPost("/sessions/launch", {
    command: "python3 -u -c 'import sys; print(\"READY\"); [line for line in sys.stdin]'",
    cwd: "/tmp",
    name: "connector-failure-check",
    in_terminal: false,
    test: true,
  });
  expect(result.status).toBe(200);
  const key = String(result.body.session_key);
  try {
    await page.route("**/connectors", route => route.fulfill({
      json: {
        connectors: [{
          name: "porkbun", title: "Porkbun", description: "Domains and DNS",
          credential: "stored", identity: null, sources: ["stored"], write_access: false,
          revoke_url: "https://porkbun.com/account/api",
          installed: { "claude-code": true, codex: true }, enabled: true, ready: true,
          detail: null, last_used: null, use_count: 0,
        }],
      },
    }));
    await page.route("**/connectors/porkbun/verify", route => route.fulfill({
      json: { name: "porkbun", ok: false, tools: 0, detail: "Protocol version rejected" },
    }));

    await page.goto("/");
    await page.locator(".rd-row-name", { hasText: "connector-failure-check" }).click();
    const pane = page.locator(".rd-context-pane");
    await pane.getByRole("tab", { name: "Connectors", exact: true }).click();

    const row = pane.locator(".rd-connector", { hasText: "Porkbun" });
    // Silence in the event log is not evidence the connector went unused.
    await expect(row.getByText(/No recorded use/)).toBeVisible();

    await row.getByRole("button", { name: "Check now" }).click();
    await expect(row.getByText(/Protocol version rejected/)).toBeVisible();
    await expect(row.getByText(/Verified/)).toHaveCount(0);
  } finally {
    await apiDelete(`/sessions/${key}`);
  }
});
