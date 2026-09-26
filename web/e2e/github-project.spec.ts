import { expect, test } from "@playwright/test";

for (const target of ["local", "dev"]) {
  test(`GitHub repository selection clones and launches on ${target}`, async ({ page }) => {
    await page.addInitScript((expectedTarget) => {
      const created = new Set<string>();
      localStorage.setItem(`remote-project:${expectedTarget}:new:clone`, JSON.stringify({ id: "a".repeat(32), source: "", destination: "/home/fixture/projects/duckterm", url: "https://github.com/fixture/sotto.git", github_repository: "fixture/sotto", branch: "main", selected: [] }));
      window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote — Development" }] };
      window.webkit = { messageHandlers: {
        remoteSession: { postMessage: () => undefined },
        launchRequest: { postMessage: async (raw: unknown) => {
          const request = raw as { operation: string; target: string; params: { path?: string; parent?: string; name?: string; page?: number; url?: string; branch?: string; github_repository?: string; destination?: string } };
          if (request.target !== expectedTarget) throw new Error("Wrong destination");
          const home = expectedTarget === "local" ? "/Users/fixture" : "/home/fixture";
          if (request.operation === "project-mkdir") {
            if (request.params.parent !== `${home}/projects` || request.params.name !== "my-sotto-checkout") throw new Error("Wrong folder creation");
            const path = `${request.params.parent}/${request.params.name}`;
            created.add(path);
            return { path, parent: request.params.parent, empty: true, is_git: false, entries: [] };
          }
          if (request.operation === "browse") {
            const path = request.params.path ?? home;
            const empty = path === `${home}/projects/chosen-folder` || created.has(path);
            if (![home, `${home}/projects`].includes(path) && !empty) throw new Error("Invalid browse path");
            return { path, parent: path === home ? null : home, empty, is_git: false,
              entries: empty ? [] : path === home ? [{ name: "projects", path: `${home}/projects`, is_git: false }] : [{ name: "chosen-folder", path: `${home}/projects/chosen-folder`, is_git: false }],
            };
          }
          if (request.operation === "project-repositories") return {
            identity: "fixture-account", next_page: request.params.page === 1 ? 2 : null,
            repositories: request.params.page === 1 ? [{ full_name: "fixture/sotto", private: false, default_branch: "main" }] : [{ full_name: "fixture/duckterm", private: true, default_branch: "feature" }],
          };
          if (request.operation === "project-preflight") return { destination: request.params.destination };
          if (request.operation === "project-status") return { stage: "cloning" };
          if (request.operation === "project-clone") {
            if (request.params.github_repository !== "fixture/sotto" || request.params.branch !== "main" || request.params.url !== "https://github.com/fixture/sotto.git") throw new Error("Wrong clone identity");
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
    await expect(page.getByLabel(target === "local" ? "Local destination folder" : "Remote destination folder")).toHaveValue("");
    await page.getByRole("button", { name: "Choose from GitHub…" }).click();
    await expect(page.getByText(/fixture-account/)).toBeVisible();
    await page.getByRole("button", { name: "Load more repositories" }).click();
    await page.getByRole("textbox", { name: "Filter GitHub repositories" }).fill("duckterm");
    await expect(page.getByRole("button", { name: "fixture/sotto", exact: true })).toHaveCount(0);
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
    const home = target === "local" ? "/Users/fixture" : "/home/fixture";
    const destination = `${home}/projects/my-sotto-checkout`;
    const destinationInput = page.getByLabel(target === "local" ? "Local destination folder" : "Remote destination folder");
    if (target === "dev") await destinationInput.fill("/home/nonexistent/duckterm");
    await page.getByRole("button", { name: "Browse destination folders" }).click();
    await page.getByText("projects", { exact: true }).click();
    await expect(page.getByRole("button", { name: "Use this folder", exact: true })).toBeDisabled();
    await page.getByText("chosen-folder", { exact: true }).click();
    await page.getByRole("button", { name: "Use this folder", exact: true }).click();
    // Use exactly the selected empty folder, without appending the repo name.
    await expect(destinationInput).toHaveValue(`${home}/projects/chosen-folder`);
    await review.click();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Browse destination folders" }).click();
    await expect(page.getByRole("button", { name: "Use this folder", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).first().click();
    await expect(destinationInput).toHaveValue(`${home}/projects/chosen-folder`);
    await expect(page.getByRole("checkbox")).toBeChecked();
    // Switching repositories clears both the old checkout folder and consent.
    await page.getByRole("button", { name: "Choose from GitHub…" }).click();
    await page.getByRole("button", { name: "fixture/sotto", exact: true }).click();
    await expect(destinationInput).toHaveValue("");
    await expect(page.getByRole("button", { name: "Clone repository", exact: true })).toHaveCount(0);
    await expect(review).toBeDisabled();
    await page.getByRole("button", { name: "Browse destination folders" }).click();
    await page.getByText("projects", { exact: true }).click();
    await page.getByRole("button", { name: "New folder…", exact: true }).click();
    await expect(page.getByLabel("New folder name")).toHaveValue("sotto");
    await page.getByLabel("New folder name").fill("my-sotto-checkout");
    await page.getByRole("button", { name: "Create folder", exact: true }).click();
    await expect(page.getByText(destination, { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Use this folder", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/exact-checkout-picker-${target}.png`, fullPage: true });
    await page.getByRole("button", { name: "Use this folder", exact: true }).click();
    await expect(destinationInput).toHaveValue(destination);
    await review.click();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Clone repository", exact: true }).click();
    await expect(page.getByRole("progressbar", { name: "Repository clone progress" })).toBeVisible();
    await expect(launch).toBeDisabled();
    await expect(page.getByRole("button", { name: "Browse destination folders" })).toBeDisabled();
    await expect(page.getByText(destination, { exact: true })).toBeVisible();
    await expect(launch).toBeEnabled();
    await page.getByRole("button", { name: "Launch", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-launched-target", target);
  });
}
