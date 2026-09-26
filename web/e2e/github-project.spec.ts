import { expect, test } from "@playwright/test";

for (const target of ["local", "dev"]) {
  test(`GitHub repository selection clones and launches on ${target}`, async ({ page }) => {
    await page.addInitScript((expectedTarget) => {
      window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — Development" }] };
      window.webkit = { messageHandlers: {
        remoteSession: { postMessage: () => undefined },
        launchRequest: { postMessage: async (raw: unknown) => {
          const request = raw as { operation: string; target: string; params: { page?: number; url?: string; branch?: string; github_repository?: string; destination?: string } };
          if (request.target !== expectedTarget) throw new Error("Wrong destination");
          if (request.operation === "project-repositories") return {
            identity: "fixture-account", next_page: request.params.page === 1 ? 2 : null,
            repositories: request.params.page === 1 ? [{ full_name: "fixture/other", private: false, default_branch: "main" }] : [{ full_name: "fixture/duckterm", private: true, default_branch: "feature" }],
          };
          if (request.operation === "project-preflight") return { destination: request.params.destination };
          if (request.operation === "project-status") return { stage: "cloning" };
          if (request.operation === "project-clone") {
            if (request.params.github_repository !== "fixture/duckterm" || request.params.branch !== "feature" || request.params.url !== "https://github.com/fixture/duckterm.git") throw new Error("Wrong clone identity");
            await new Promise(resolve => setTimeout(resolve, 1500));
            return { id: "synthetic-clone", stage: "ready", destination: request.params.destination };
          }
          if (request.operation === "project-launch") {
            document.documentElement.setAttribute("data-launched-target", request.target);
            return { session_key: "synthetic-clone-session" };
          }
          return { themes: [] };
        } },
      } };
    }, target);
    await page.goto("/");
    await page.getByRole("button", { name: "New", exact: true }).click();
    await page.getByRole("button", { name: "New session", exact: true }).click();
    if (target !== "local") await page.getByRole("combobox", { name: "Run on" }).selectOption(target);
    const source = page.getByRole("combobox", { name: "Project source" });
    if (target === "local") await expect(source.locator("option[value=copy]")).toHaveCount(0);
    await source.selectOption("clone");
    await page.getByRole("button", { name: "Choose from GitHub…" }).click();
    await expect(page.getByText(/fixture-account/)).toBeVisible();
    await page.getByRole("button", { name: "Load more repositories" }).click();
    await page.getByRole("textbox", { name: "Filter GitHub repositories" }).fill("duckterm");
    await expect(page.getByRole("button", { name: "fixture/other", exact: true })).toHaveCount(0);
    await page.screenshot({ path: `/tmp/github-picker-${target}.png`, fullPage: true });
    await page.getByRole("button", { name: "fixture/duckterm · Private", exact: true }).click();
    await expect(page.getByLabel("Repository URL")).toHaveValue("https://github.com/fixture/duckterm.git");
    await expect(page.getByLabel("Clone branch")).toHaveValue("feature");
    const launch = page.getByRole("button", { name: "Launch", exact: true });
    const review = page.getByRole("button", { name: "Review transfer", exact: true });
    await expect(launch).toBeDisabled();
    await expect(review).toBeDisabled();
    await expect(page.getByLabel(target === "local" ? "Local destination folder" : "Remote destination folder")).toHaveValue("");
    await page.screenshot({ path: `/tmp/github-clone-unprepared-${target}.png`, fullPage: true });
    const destination = target === "local" ? "/Users/fixture/projects/duckterm" : "/home/fixture/duckterm";
    await page.getByLabel(target === "local" ? "Local destination folder" : "Remote destination folder").fill(destination);
    await page.getByRole("button", { name: "Review transfer", exact: true }).click();
    await expect(launch).toBeDisabled();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Clone repository", exact: true }).click();
    await expect(page.getByRole("progressbar", { name: "Repository clone progress" })).toBeVisible();
    await expect(launch).toBeDisabled();
    await expect(page.getByText(destination, { exact: true })).toBeVisible();
    await expect(launch).toBeEnabled();
    await page.getByRole("button", { name: "Launch", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-launched-target", target);
  });
}
