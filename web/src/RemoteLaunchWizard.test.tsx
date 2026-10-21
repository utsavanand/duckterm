import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RemoteLaunchWizard } from "./RemoteLaunchWizard";
import { sessionRef } from "./hostTransport";

vi.mock("./api", () => ({ api: { setGroup: vi.fn().mockResolvedValue({ updated: true }) } }));
afterEach(() => { cleanup(); localStorage.clear(); delete window.webkit; delete window.__rubbertermDesktop; });
function setup(failClone = false) {
  const requests = vi.fn(async (raw: unknown) => {
    const { operation, params } = raw as { operation: string; params: { id: string; url: string } };
    if (operation === "project-repositories") return { repositories: [{ full_name: "fixture/sotto", default_branch: "main" }, { full_name: "fixture/duckterm", default_branch: "develop" }], next_page: null };
    if (operation === "project-preflight") return { auto_workspace: true, runtime: "available", destination: `/home/test/projects/${params.url.includes("sotto") ? "sotto" : "duckterm"}/${params.id}` };
    if (operation === "project-clone" && failClone) { failClone = false; throw new Error("Repository access denied"); }
    if (operation === "project-launch") return { stage: "launched", session_key: "remote-task" };
    return { stage: "ready" };
  });
  window.__rubbertermDesktop = { currentTarget: "local", targets: [{ id: "local", name: "This Mac" }, { id: "dev", name: "Remote dev" }], testBuild: true };
  window.webkit = { messageHandlers: { launchRequest: { postMessage: requests } } };
  const created = vi.fn(); const closed = vi.fn();
  render(<RemoteLaunchWizard target="dev" onTarget={vi.fn()} onOtherProject={vi.fn()} onClose={closed} onCreated={created} folders={["Coding"]} group="Coding" initial={{ agent: "claude", command: "claude", name: "", prompt: "" }} />);
  return { requests, created, closed };
}
async function choose(name = "fixture/sotto") {
  fireEvent.click(screen.getByRole("button", { name: /Choose from GitHub|fixture\// }));
  fireEvent.click(await screen.findByRole("button", { name }));
  fireEvent.click(screen.getByRole("button", { name: "Next →" }));
  fireEvent.click(screen.getByRole("button", { name: "Next →" }));
  await screen.findByRole("button", { name: "Launch session" });
}
it("launches a repo in three steps without requiring a folder and stays in the local dashboard", async () => {
  const { requests, created, closed } = setup();
  expect(screen.queryByLabelText("Remote destination folder")).not.toBeInTheDocument();
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "Launch session" }));
  await waitFor(() => expect(created).toHaveBeenCalledWith(sessionRef("dev", "remote-task"), "Coding"));
  expect(closed).toHaveBeenCalledOnce();
  expect(window.__rubbertermDesktop?.currentTarget).toBe("local");
  expect(requests).toHaveBeenCalledWith(expect.objectContaining({ target: "dev", operation: "project-clone", params: expect.objectContaining({ github_repository: "fixture/sotto", branch: "main", auto_workspace: true }) }));
  expect(requests).toHaveBeenCalledWith(expect.objectContaining({ operation: "project-launch", params: expect.objectContaining({ name: "sotto", test: true }) }));
});
it("replaces clone progress with an error and retries the same operation", async () => {
  const { requests, created } = setup(true);
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "Launch session" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Repository access denied");
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  const calls = requests.mock.calls.map(([raw]) => raw as { operation: string; params: { id: string } }).filter(r => r.operation === "project-clone");
  expect(calls[0].params.id).toBe(calls[1].params.id);
});
it("changing repositories invalidates the checkout and uses the selected repo default branch", async () => {
  const { requests } = setup();
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  await choose("fixture/duckterm");
  fireEvent.click(screen.getByRole("button", { name: "Launch session" }));
  await act(async () => undefined);
  const reviews = requests.mock.calls.map(([raw]) => raw as { operation: string; params: { id: string } }).filter(r => r.operation === "project-preflight");
  expect(reviews[0].params.id).not.toBe(reviews[1].params.id);
  expect(requests).toHaveBeenCalledWith(expect.objectContaining({ operation: "project-clone", params: expect.objectContaining({ github_repository: "fixture/duckterm", branch: "develop", destination: expect.stringContaining("/duckterm/") }) }));
});
