import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ArchMap, type ReviewHeader } from "../api";
import { HistoryPane } from "./HistoryPane";

vi.mock("../api", () => ({ api: { releases: vi.fn(), archmap: vi.fn(), compareReleases: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const header: ReviewHeader = { repo: "/r", repo_name: "r", github: "o/r", ref: "307", branch: "duckterm/aaa", base: "main", base_sha: "1", head_sha: "2", commits: 1, previous_head: null, pr: null, row: null, session: null };
const maps: Record<string, ArchMap> = {
  "v0.4.172": { tag: "v0.4.172", status: "ready", modules: [{ id: "server", name: "Server", lane: "Server", files: ["src/duckterm/"], what: "HTTP and routes" }, { id: "web", name: "Dashboard", lane: "Clients", files: ["web/src/"], what: "React dashboard" }], edges: [["web", "server"]] },
  "v0.4.173": { tag: "v0.4.173", status: "ready", modules: [{ id: "server", name: "Server", lane: "Server", files: ["src/duckterm/"], what: "HTTP and routes" }, { id: "web", name: "Dashboard", lane: "Clients", files: ["web/src/"], what: "React dashboard" }, { id: "review", name: "Review", lane: "Server", files: ["src/duckterm/review"], what: "PR review" }], edges: [["web", "server"], ["review", "server"]] },
};

it("lists releases, shows the selected release's map, and marks the difference when comparing", async () => {
  vi.mocked(api.releases).mockResolvedValue({
    releases: [
      { tag: "v0.4.172", at: 1_760_000_000, sha: "a", subject: "Release v0.4.172", previous: null, merges: [{ sha: "abc1234", subject: "Merge pull request #301 from x", pr: 301 }] },
      { tag: "v0.4.173", at: 1_760_086_400, sha: "b", subject: "Release v0.4.173", previous: "v0.4.172", merges: [] },
    ],
    maps: { "v0.4.172": "ready", "v0.4.173": "ready" }, touched: ["v0.4.173"], shipped: "v0.4.173",
  });
  vi.mocked(api.archmap).mockImplementation(async (_repo, _ref, tag) => maps[tag]);
  vi.mocked(api.compareReleases).mockResolvedValue({ older: "v0.4.172", newer: "v0.4.173", marks: { server: "changed", web: "unchanged", review: "added" }, removed_modules: [], edges_added: [["review", "server"]], edges_removed: [], files_changed: 14 });
  render(<HistoryPane header={header} active={false} />);
  const timeline = await screen.findByRole("list", { name: "Releases" });
  expect(within(timeline).getAllByRole("listitem")).toHaveLength(2);
  // The newest release opens by default with its map and merges.
  expect(await screen.findByRole("heading", { name: "v0.4.173" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Architecture at v0.4.173" })).toBeInTheDocument();
  expect(screen.getByText(/this change will ship after v0.4.173/)).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText(/Only releases that touched this PR's files/));
  expect(within(screen.getByRole("list", { name: "Releases" })).getAllByRole("listitem")).toHaveLength(1);
  fireEvent.click(screen.getByLabelText(/Only releases that touched this PR's files/));
  // Compare with previous marks the added module and the new edge.
  fireEvent.click(screen.getByRole("button", { name: "Compare with previous" }));
  expect(await screen.findByText(/14 files changed between v0.4.172 and v0.4.173/)).toHaveTextContent("1 modules added · 1 changed · 0 removed");
  const svg = screen.getByRole("img", { name: "Architecture at v0.4.173" });
  expect(svg.querySelectorAll("[data-mark=added]")).toHaveLength(1);
  expect(svg.querySelectorAll("[data-mark=changed]")).toHaveLength(1);
  expect(svg.querySelectorAll(".rd-archmap-edge.added")).toHaveLength(1);
  expect(api.compareReleases).toHaveBeenCalledWith("/r", "307", "v0.4.172", "v0.4.173");
  // Clicking the older release shows its merges and PR link.
  fireEvent.click(screen.getByRole("button", { name: "Clear comparison" }));
  fireEvent.click(within(screen.getByRole("list", { name: "Releases" })).getAllByRole("button")[0]);
  expect(await screen.findByRole("link", { name: "#301" })).toHaveAttribute("href", "https://github.com/o/r/pull/301");
});

it("explains an omitted map and says when none exist", async () => {
  vi.mocked(api.releases).mockResolvedValue({ releases: [{ tag: "v0.1.0", at: 1, sha: "a", subject: "", previous: null, merges: [] }], maps: { "v0.1.0": "omitted" }, touched: [], shipped: "v0.1.0" });
  vi.mocked(api.archmap).mockResolvedValue({ tag: "v0.1.0", status: "omitted", reason: "No Claude Code or Codex CLI is installed to describe the release" });
  render(<HistoryPane header={header} active={false} />);
  expect(await screen.findByText(/Module map omitted: No Claude Code/)).toBeInTheDocument();
  cleanup();
  vi.mocked(api.releases).mockResolvedValue({ releases: [], maps: {}, touched: [], shipped: null });
  render(<HistoryPane header={header} active={false} />);
  expect(await screen.findByText(/No release tags/)).toBeInTheDocument();
});
