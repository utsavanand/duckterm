import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type DiffFile, type ReviewDiff, type ReviewHeader } from "../api";
import { ReviewWorkspace } from "./ReviewWorkspace";

vi.mock("../api", () => ({ api: { reviewHeader: vi.fn(), reviewDiff: vi.fn(), markViewed: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const header: ReviewHeader = {
  repo: "/Users/me/duckterm", repo_name: "duckterm", github: "utsavanand/duckterm", ref: "307", branch: "duckterm/aaa", base: "main",
  base_sha: "1111111aaaa", head_sha: "2222222bbbb", commits: 3, previous_head: "0000000cccc",
  pr: { number: 307, title: "Remote coding wizard", body: "", url: "https://github.com/utsavanand/duckterm/pull/307", state: "open", draft: false, author: "bot", head: "duckterm/aaa", head_sha: "2222222bbbb", base: "main", merge_commit_sha: null, merged_at: null, updated_at: null, created_at: null },
  row: null,
};
const file = (path: string, extra: Partial<DiffFile> = {}): DiffFile => ({
  path, old_path: null, status: "modified", binary: false, additions: 2, deletions: 1, truncated: false, total_lines: 3,
  hunks: [{ header: "def run():", old_start: 1, old_lines: 2, new_start: 1, new_lines: 3, lines: [{ kind: " ", text: "import os" }, { kind: "-", text: "x = 1" }, { kind: "+", text: "x = 2" }, { kind: "+", text: "y = 3" }] }],
  bucket: "read", bucket_rule: "read", rules: [{ bucket: "read", id: "source", reason: "Source file" }], hash: `hash-${path}`, viewed: false, stale: false, changed_since: false, whitespace_only: false, ...extra,
});
const diff: ReviewDiff = {
  base_sha: "1111111aaaa", head_sha: "2222222bbbb", since: "0000000cccc", whitespace: true,
  files: [
    file("src/duckterm/server.py", { bucket: "high", bucket_rule: "high", rules: [{ bucket: "high", id: "routes", reason: "Adds or changes an HTTP route or an auth check" }], changed_since: true }),
    file("src/duckterm/transfers.py", { viewed: true }),
    file("docs/spec.md", { bucket: "skim", bucket_rule: "skim", rules: [{ bucket: "skim", id: "docs", reason: "Documentation" }] }),
  ],
  counts: { high: 1, read: 1, skim: 1, viewed: 1, changed_since: 1 },
};

it("groups files by risk, explains the bucket, and marks a file viewed by its hunk hash", async () => {
  vi.mocked(api.reviewHeader).mockResolvedValue(header);
  vi.mocked(api.reviewDiff).mockResolvedValue(diff);
  vi.mocked(api.markViewed).mockResolvedValue({});
  render(<ReviewWorkspace repo={header.repo} target="307" from="ui-dev" onBack={vi.fn()} />);
  expect(await screen.findByRole("heading", { name: "#307 Remote coding wizard" })).toBeInTheDocument();
  const files = await screen.findByRole("navigation", { name: "Changed files" });
  expect(within(files).getAllByRole("heading", { level: 3 }).map(h => h.textContent)).toEqual(["High risk 1", "Worth a read 1", "Safe to skim 1"]);
  expect(api.reviewDiff).toHaveBeenCalledWith(header.repo, "307", { whitespace: true, since: "0000000cccc" });
  // The first file (highest risk) opens by default with its reason.
  expect(screen.getByRole("article", { name: "src/duckterm/server.py" })).toHaveTextContent("High risk · Adds or changes an HTTP route or an auth check");
  expect(screen.getByText("1/3 viewed")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Mark viewed" }));
  expect(api.markViewed).toHaveBeenCalledWith(header.repo, "307", { path: "src/duckterm/server.py", hash: "hash-src/duckterm/server.py", viewed: true });
  expect(screen.getByText("2/3 viewed")).toBeInTheDocument();
  expect(within(files).getByRole("checkbox", { name: "Viewed src/duckterm/server.py" })).toBeChecked();
  // Next unviewed skips the already viewed transfers.py and lands on the doc.
  fireEvent.click(screen.getByRole("button", { name: "Next unviewed" }));
  expect(screen.getByRole("article", { name: "docs/spec.md" })).toBeInTheDocument();
  // Only unviewed hides the two viewed files.
  fireEvent.click(screen.getByLabelText("Only unviewed"));
  expect(within(files).queryByText("server.py")).not.toBeInTheDocument();
  expect(within(files).getByText("spec.md")).toBeInTheDocument();
});

it("moves a file to another bucket, filters since the last look, and quotes a hunk into the chat panel", async () => {
  vi.mocked(api.reviewHeader).mockResolvedValue(header);
  vi.mocked(api.reviewDiff).mockResolvedValue(diff);
  vi.mocked(api.markViewed).mockResolvedValue({});
  render(<ReviewWorkspace repo={header.repo} target="307" onBack={vi.fn()} />);
  const files = await screen.findByRole("navigation", { name: "Changed files" });
  fireEvent.change(within(files).getByRole("combobox", { name: "Bucket for docs/spec.md" }), { target: { value: "high" } });
  expect(api.markViewed).toHaveBeenCalledWith(header.repo, "307", { path: "docs/spec.md", hash: "hash-docs/spec.md", bucket: "high" });
  expect(within(files).getAllByRole("heading", { level: 3 }).map(h => h.textContent)).toEqual(["High risk 2", "Worth a read 1"]);
  fireEvent.change(within(files).getByRole("combobox", { name: "Bucket for docs/spec.md" }), { target: { value: "skim" } });
  expect(api.markViewed).toHaveBeenLastCalledWith(header.repo, "307", { path: "docs/spec.md", hash: "hash-docs/spec.md", bucket: "" });
  fireEvent.click(screen.getByLabelText(/Since my last look · 1 file/));
  expect(within(files).getAllByRole("listitem")).toHaveLength(1);
  // Split layout pairs the removed and added lines on one row.
  fireEvent.click(screen.getByRole("button", { name: "Split" }));
  const rows = within(screen.getByRole("article", { name: "src/duckterm/server.py" })).getAllByRole("row");
  expect(rows[1]).toHaveTextContent("2x = 12x = 2");
  // Asking about a hunk opens the chat panel with the hunk quoted.
  fireEvent.click(screen.getByRole("button", { name: "Collapse chat" }));
  expect(screen.queryByRole("textbox", { name: "Ask about this pull request" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ask about this hunk" }));
  expect(screen.getByRole("textbox", { name: "Ask about this pull request" })).toBeInTheDocument();
  expect(screen.getByRole("complementary", { name: "Review chat" })).toHaveTextContent("src/duckterm/server.py @@ def run():");
});

it("goes back to the list and surfaces header errors", async () => {
  const back = vi.fn();
  vi.mocked(api.reviewHeader).mockRejectedValue(new Error("Branch duckterm/zzz is not available locally or on origin"));
  render(<ReviewWorkspace repo="/r" target="duckterm/zzz" onBack={back} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("not available locally");
  fireEvent.click(screen.getByRole("button", { name: "← Pull requests" }));
  expect(back).toHaveBeenCalled();
});
