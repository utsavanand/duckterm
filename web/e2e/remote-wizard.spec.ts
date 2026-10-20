import { expect, test } from "@playwright/test";

test("three short steps create a remote checkout and show retry after a failed clone", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.addInitScript(() => {
    window.__rubbertermDesktop = { currentTarget: "local", testBuild: true, targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — duckterm-dev" }] };
    let attempts = 0;
    window.webkit = { messageHandlers: {
      remoteSession: { postMessage: () => { throw new Error("Unexpected dashboard switch"); } },
      launchRequest: { postMessage: async (raw: unknown) => {
        const r = raw as { operation: string; params: { id: string } };
        if (r.operation === "project-repositories") return { repositories: [{ full_name: "utsavanand/sotto", default_branch: "main", private: true }, { full_name: "utsavanand/duckterm", default_branch: "main", private: true }], next_page: null };
        if (r.operation === "project-preflight") return { auto_workspace: true, runtime: "available", destination: `/home/duckterm/projects/utsavanand/sotto/session-${r.params.id}` };
        if (r.operation === "project-clone") { attempts++; if (attempts === 1) throw new Error("GitHub connection interrupted; retry the clone"); return { stage: "ready" }; }
        if (r.operation === "project-launch") throw new Error("Synthetic test stops before creating a real agent");
        if (r.operation === "themes") return { themes: [] };
        return {};
      } },
    } };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.getByRole("button", { name: "New session", exact: true }).click();
  await page.getByRole("combobox", { name: "Run on" }).selectOption("dev");
  await page.getByRole("button", { name: "Choose from GitHub…" }).click();
  await page.getByRole("button", { name: "utsavanand/sotto · Private" }).click();
  await expect(page.getByLabel("Remote destination folder")).toHaveCount(0);
  await page.screenshot({ path: "/tmp/remote-wizard-step1.png" });
  await page.getByRole("button", { name: "Next →" }).click();
  await page.getByRole("textbox", { name: "Task", exact: true }).fill("Add a healthcheck endpoint");
  await page.screenshot({ path: "/tmp/remote-wizard-step2.png" });
  await page.getByRole("button", { name: "Next →" }).click();
  await expect(page.getByRole("button", { name: "Launch session" })).toBeVisible();
  await page.screenshot({ path: "/tmp/remote-wizard-step3.png" });
  const box = await page.getByRole("button", { name: "Launch session" }).boundingBox();
  expect(box!.y + box!.height).toBeLessThan(800);
  await page.getByRole("button", { name: "Launch session" }).click();
  await expect(page.getByRole("alert")).toHaveText("GitHub connection interrupted; retry the clone");
  await expect(page.getByRole("progressbar")).toHaveCount(0);
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Synthetic test stops before creating a real agent");
  expect(await page.evaluate(() => window.__rubbertermDesktop?.currentTarget)).toBe("local");
});
