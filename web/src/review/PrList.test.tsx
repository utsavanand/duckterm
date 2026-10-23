import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type PrListResult, type PrRow } from "../api";
import { PrList, ago } from "./PrList";

vi.mock("../api", () => ({ api: { sessionPrs: vi.fn(), folderPrs: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const base: PrRow = {
  repo: "/Users/me/duckterm", repo_name: "duckterm", github: "utsavanand/duckterm", branch: "duckterm/aaa", base: "main",
  session: { key: "s1", name: "ui-dev" }, author: "bot", status: "open", label: "Open · awaiting review", needs_you: true,
  version: null, checks: { total: 3, failed: 0, pending: 0, passed: 3 }, review: "none", conflict: false, pushed: true, unpushed_commits: 0,
  ahead: 2, behind: 0, size: { files: 4, additions: 120, deletions: 8 }, updated_at: Math.floor(Date.now() / 1000) - 7200, compare_url: null,
  pr: { number: 307, title: "Remote coding wizard", body: "", url: "https://github.com/utsavanand/duckterm/pull/307", state: "open", draft: false, author: "bot", head: "duckterm/aaa", head_sha: "abc", base: "main", merge_commit_sha: null, merged_at: null, updated_at: null, created_at: null },
};
const merged: PrRow = { ...base, branch: "duckterm/bbb", session: { key: "s2", name: "main-dev" }, status: "merged", label: "Merged · v0.4.173", needs_you: false, checks: null, pr: { ...base.pr!, number: 301, title: "Fix checkpoint recency", state: "merged" } };
const unpushed: PrRow = { ...base, branch: "duckterm/ccc", session: { key: "s3", name: "architect" }, status: "unpushed", label: "Not pushed", needs_you: false, checks: null, pr: null, pushed: false, size: { files: 1, additions: 359, deletions: 0 }, ahead: 1 };
const result: PrListResult = { rows: [base, merged, unpushed], repos: [{ path: "/Users/me/duckterm", name: "duckterm", github: "utsavanand/duckterm", default_branch: "main", connected: true, error: null, fetched_at: Math.floor(Date.now() / 1000) }] };

it("renders rows with their status and filters to what needs the owner", async () => {
  vi.mocked(api.folderPrs).mockResolvedValue(result);
  render(<PrList scope={{ kind: "folder", folder: "DuckTerm" }} active={false} onOpenSession={vi.fn()} />);
  const table = await screen.findByRole("table");
  expect(within(table).getAllByRole("row")).toHaveLength(4);
  expect(within(table).getByText("Open · awaiting review")).toHaveClass("is-open");
  expect(within(table).getByText("Merged · v0.4.173")).toHaveClass("is-merged");
  expect(within(table).getByText("3/3 passed")).toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "Remote coding wizard" })).toHaveAttribute("href", "https://github.com/utsavanand/duckterm/pull/307");
  expect(within(table).getByRole("button", { name: "ui-dev" })).toBeInTheDocument();
  expect(api.folderPrs).toHaveBeenCalledWith("DuckTerm", false);
  fireEvent.click(screen.getByRole("button", { name: "Needs you 1" }));
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  expect(screen.queryByText("Merged · v0.4.173")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Merged 1" }));
  expect(screen.getByText("Merged · v0.4.173")).toBeInTheDocument();
});

it("opens the authoring session from the From column and GitHub from the Review link", async () => {
  const open = vi.fn();
  vi.mocked(api.folderPrs).mockResolvedValue(result);
  render(<PrList scope={{ kind: "folder", folder: "DuckTerm" }} active={false} onOpenSession={open} />);
  fireEvent.click(await screen.findByRole("button", { name: "main-dev" }));
  expect(open).toHaveBeenCalledWith("s2");
  const links = screen.getAllByRole("link", { name: "Review on GitHub" });
  expect(links.map(l => l.getAttribute("href"))).toEqual(["https://github.com/utsavanand/duckterm/pull/307", "https://github.com/utsavanand/duckterm/pull/307"]);
  expect(screen.queryByRole("columnheader", { name: "From" })).toBeInTheDocument();
});

it("asks the server to refresh on the Refresh button and shows GitHub problems without hiding git facts", async () => {
  vi.mocked(api.sessionPrs).mockResolvedValue({ rows: [unpushed], repos: [{ ...result.repos[0], connected: false, error: "GitHub rate limit reached; status will refresh later" }] });
  render(<PrList scope={{ kind: "session", key: "s3", name: "architect" }} active={false} />);
  expect(await screen.findByText("Not pushed")).toHaveClass("is-unpushed");
  expect(screen.getByRole("status")).toHaveTextContent("GitHub rate limit reached; status will refresh later");
  expect(screen.queryByRole("columnheader", { name: "From" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(api.sessionPrs).toHaveBeenLastCalledWith("s3", true);
});

it("explains an empty list and surfaces load errors", async () => {
  vi.mocked(api.sessionPrs).mockRejectedValueOnce(new Error("owner credential required")).mockResolvedValue({ rows: [], repos: [] });
  render(<PrList scope={{ kind: "session", key: "s9", name: "qa" }} active={false} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("owner credential required");
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText(/No pull requests yet/)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("formats relative times in the unit the reader expects", () => {
  const now = 1_700_000_000_000;
  expect(ago(null, now)).toBe("");
  expect(ago(now / 1000 - 30, now)).toBe("just now");
  expect(ago(now / 1000 - 125, now)).toBe("2m ago");
  expect(ago(now / 1000 - 7200, now)).toBe("2h ago");
  expect(ago(now / 1000 - 3 * 86400, now)).toBe("3d ago");
});
