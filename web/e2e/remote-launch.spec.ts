import { expect, test } from "@playwright/test";

test("remote destination reopens New Session with the carried task", async ({ page }) => {
  await page.addInitScript(() => {
    window.__rubbertermDesktop = {
      currentTarget: "dev",
      targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }],
      draft: { agent: "codex", command: "codex", name: "Remote task", prompt: "Check the build" },
    };
  });
  await page.goto("/");
  await expect(page.getByRole("combobox", { name: "Run on" })).toHaveValue("dev");
  await expect(page.getByPlaceholder("e.g. login refactor")).toHaveValue("Remote task");
  await expect(page.getByPlaceholder("add a healthcheck endpoint")).toHaveValue("Check the build");
  await expect(page.getByText("Browse…", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByRole("button", { name: "New session", exact: true }).click();
  await expect(page.getByPlaceholder("e.g. login refactor")).toHaveValue("");
  await expect(page.getByRole("combobox", { name: "Run on" })).toHaveValue("dev");
});

test("choosing Remote keeps the current page and form mounted", async ({ page }) => {
  await page.addInitScript(() => {
    window.__rubbertermDesktop = {
      currentTarget: "local",
      targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — dev" }],
    };
    window.webkit = { messageHandlers: {
      remoteSession: { postMessage: () => { throw new Error("Unexpected dashboard switch"); } },
      launchRequest: { postMessage: async (raw: unknown) => {
        if ((raw as { operation: string }).operation === "themes") return { themes: [] };
        return { path: "/home/remote/project", parent: null, is_git: false, entries: [] };
      } },
    } };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByRole("button", { name: "New session", exact: true }).click();
  const name = page.getByPlaceholder("e.g. login refactor");
  await name.fill("Keep my draft");
  await name.evaluate((element) => element.setAttribute("data-qa-original", "yes"));
  const before = page.url();
  await page.getByRole("combobox", { name: "Run on" }).selectOption("dev");
  await expect(name).toHaveAttribute("data-qa-original", "yes");
  await expect(name).toHaveValue("Keep my draft");
  await page.getByText("Browse…", { exact: true }).click();
  await expect(page.getByText("/home/remote/project", { exact: true })).toBeVisible();
  expect(page.url()).toBe(before);
  expect(await page.evaluate(() => window.__rubbertermDesktop?.currentTarget)).toBe("local");
});

test("opening a moved session selects its exact destination among other sessions", async ({ page }) => {
  const { seedSession, apiDelete } = await import("./helpers");
  const selected = `moved-selected-${Date.now()}`;
  const other = `moved-other-${Date.now()}`;
  await seedSession(selected, { name: "Moved destination", test: true });
  await seedSession(other, { name: "Other remote work", test: true });
  await page.addInitScript((key) => {
    window.__rubbertermDesktop = {
      currentTarget: "dev", targets: [{ id: "dev", name: "Remote QA" }], selectedSession: key,
    };
  }, selected);
  try {
    await page.goto("/");
    await expect(page.locator(".rd-row.selected .rd-row-name")).toHaveText("Moved destination");
  } finally {
    await apiDelete(`/sessions/${selected}`);
    await apiDelete(`/sessions/${other}`);
  }
});
