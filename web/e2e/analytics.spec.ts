import { expect, test } from "@playwright/test";
import { readFileSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { base } from "./helpers";

test("Analytics opens from Oracle and shows actual transcript model IDs, filters and exact tables", async ({
  page,
}) => {
  const { home } = JSON.parse(
    readFileSync(
      process.env.RD_TEST_STATE_FILE || join(tmpdir(), "rd-e2e-state.json"),
      "utf8",
    ),
  );
  const root = join(home, "test-claude", "projects", "analytics-fixture");
  mkdirSync(root, { recursive: true });
  const stamp = new Date().toISOString();
  writeFileSync(
    join(root, "fixture.jsonl"),
    ["claude-opus-4-1-20250805", "claude-sonnet-4-20250514"]
      .map((model, i) =>
        JSON.stringify({
          type: "assistant",
          timestamp: stamp,
          message: {
            id: "analytics-" + i,
            model,
            usage: {
              input_tokens: 100 * (i + 1),
              cache_read_input_tokens: 300,
              output_tokens: 20,
            },
          },
        }),
      )
      .join("\n") + "\n",
  );
  const codex = join(home, "test-codex", "sessions", "rollout-analytics.jsonl");
  mkdirSync(join(home, "test-codex", "sessions"), { recursive: true });
  writeFileSync(
    codex,
    [
      { type: "turn_context", payload: { model: "gpt-6-astra" } },
      {
        timestamp: stamp,
        type: "event_msg",
        payload: {
          type: "token_count",
          info: {
            total_token_usage: {
              input_tokens: 150,
              cached_input_tokens: 50,
              output_tokens: 8,
            },
          },
        },
      },
    ]
      .map((v) => JSON.stringify(v))
      .join("\n") + "\n",
  );
  try {
    await page.goto(base());
    await page.getByRole("button", { name: /^Oracle/ }).click();
    await page.getByRole("button", { name: "Open token analytics", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Analytics", exact: true }),
    ).toBeVisible();
    const models = page
      .locator(".an-panel")
      .filter({
        has: page.getByRole("heading", { name: "By model", exact: true }),
      });
    await expect(models).toContainText("claude-opus-4-1-20250805");
    await expect(models).toContainText("claude-sonnet-4-20250514");
    await expect(models).toContainText("gpt-6-astra");
    await models.getByRole("button", { name: "Table", exact: true }).click();
    await expect(
      models.getByRole("cell", {
        name: "claude-opus-4-1-20250805",
        exact: true,
      }),
    ).toBeVisible();
    await page.getByLabel("Agent", { exact: true }).selectOption("codex");
    await expect(models).toContainText("gpt-6-astra");
    await expect(models).not.toContainText("claude-opus");
    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(models).toContainText("claude-opus-4-1-20250805");
    await page
      .getByRole("button", { name: "Today (UTC)", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Today (UTC)", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await page.screenshot({
      path: "/tmp/duckterm-analytics-models.png",
      fullPage: true,
    });
    await page.getByRole("tab", { name: "Agent Mail", exact: true }).click();
    await expect(
      page.getByText("Permanent mail history since", { exact: false }),
    ).toBeVisible();
    await expect(page.getByLabel("Agent", { exact: true })).toBeDisabled();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect
      .poll(() =>
        page
          .locator(".rd-analytics")
          .evaluate((el) => el.scrollWidth <= el.clientWidth),
      )
      .toBe(true);
    await page.getByRole("button", { name: "← Oracle", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: /Control tower/ }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Open mail analytics", exact: true }).click();
    await expect(
      page.getByRole("tab", { name: "Agent Mail", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(codex, { force: true });
  }
});

test("Analytics displays loading errors and retries without leaking stale data", async ({
  page,
}) => {
  let fail = true;
  await page.route("**/analytics/tokens?*", (route) =>
    fail
      ? route.fulfill({
          status: 503,
          json: { error: "Temporary analytics failure" },
        })
      : route.continue(),
  );
  await page.goto(base());
  await page.getByRole("button", { name: /^Oracle/ }).click();
  await page.getByRole("button", { name: /Analytics ↗/ }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Temporary analytics failure",
  );
  fail = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "No activity in this view" }),
  ).toBeVisible();
});
