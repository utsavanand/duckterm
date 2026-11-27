import { expect, test } from "@playwright/test";
import { api, apiDelete, apiPost, seedSession } from "./helpers";

// #488 passed at 1280 and 390px but squeezed the question to 8px in a
// 3-column Needs you widget at an 800px window. Every size here checks that
// the question has room to read and no button leaves its row.

const command = "echo " + "a".repeat(55);
const longOption = "Long choice " + "z".repeat(60);

// The e2e server keeps Home off because it replaces the starting page other
// specs drive, so this page alone is told Home is on, with its own layout.
const layout = (needsWidth: number) => ({
  revision: "narrow",
  instances: [["teams", 0, 0, 3, 2], ["autopilot", 0, 2, 3, 2], ["spend", 0, 4, 3, 8], ["chat", 3, 0, 6, 6], ["needs-you", 3, 6, needsWidth, 6], ["prs", 9, 0, 3, 12]]
    .map(([type, x, y, w, h], position) => ({ id: type, type, slot: "home", position, params: { x, y, w, h } })),
});

for (const [width, needsWidth, minText] of [[800, 3, 120], [1280, 3, 200], [1280, 6, 300], [390, 6, 250]] as const) {
  for (const theme of ["light", "dark"]) {
    test(`Needs you is readable at ${width}px with a ${needsWidth}-column widget (${theme})`, async ({ page }) => {
      const key = `e2e-home-narrow-${width}-${needsWidth}-${theme}`;
      await seedSession(key, { name: "Narrow", group: "Narrow Home", test: true });
      const approval = await apiPost("/approvals", { session_key: key, tool_name: "Bash", tool_input: { command } });
      expect(approval.status).toBe(200);
      try {
        await page.route("**/edition", async route => {
          const edition = await (await route.fetch()).json();
          await route.fulfill({ json: { ...edition, features: [...edition.features, "home"] } });
        });
        await page.route("**/layouts/home", route => route.fulfill({ json: layout(needsWidth) }));
        // A question with a long option and a chained approval sit beside the
        // real approval, so every row shape is measured.
        await page.route("**/relay", async route => {
          const state = await (await route.fetch()).json();
          const note = { session_key: key, name: "Narrow", folder: "Narrow Home", runtime: "claude-code", status: "open", created_at: Date.now() };
          await route.fulfill({ json: { ...state, notes: [...state.notes,
            { ...note, id: "e2e-question", kind: "question", question: "When should checkpoints run?", options: [longOption, "On idle", "Hourly"] },
            { ...note, id: "e2e-chained", kind: "approval", tool: "Bash", detail: "echo first; echo second" }] } });
        });
        await page.setViewportSize({ width, height: 1000 });
        await page.addInitScript(value => localStorage.setItem("rd-theme", value), theme);
        await page.goto("/");

        const needs = page.locator(".rd-home-layer").getByRole("article", { name: "Needs you", exact: true });
        await expect(needs.getByText(`Wants to run ${command}`)).toBeVisible();
        await expect(needs.locator("li")).toHaveCount(3);
        const rows = await needs.locator("li").evaluateAll(items => items.map(item => {
          const row = item.getBoundingClientRect();
          return {
            text: item.querySelector(".rd-home-need-text")!.getBoundingClientRect().width,
            spill: [...item.querySelectorAll(".rd-home-need-acts button")].map(button => button.getBoundingClientRect().right - row.right),
          };
        }));
        for (const row of rows) {
          expect(row.text).toBeGreaterThan(minText);
          for (const spill of row.spill) expect(spill).toBeLessThanOrEqual(1);
        }

        // The real approval is answered through the relay and recorded.
        await needs.getByRole("button", { name: "Approve", exact: true }).click();
        await expect(needs.getByText(`Wants to run ${command}`)).toHaveCount(0);
        expect((await (await api(`/approvals/${approval.body.id}/decision`)).json()).status).toBe("approve");
      } finally {
        await apiDelete(`/sessions/${key}`);
      }
    });
  }
}
