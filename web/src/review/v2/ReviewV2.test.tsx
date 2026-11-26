import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type DiffFile, type ReviewCard, type ReviewDiff, type ReviewHeader } from "../../api";
import { StaticEdition } from "../../edition";
import { ReviewWorkspace } from "../ReviewWorkspace";

vi.mock("../../api", () => ({ api: {
  reviewHeader: vi.fn(), reviewDiff: vi.fn(), markViewed: vi.fn(), reviewCards: vi.fn(), reviewChat: vi.fn(), reviewAsk: vi.fn(),
  reviewSend: vi.fn(), reviewDraft: vi.fn(), regenerateCards: vi.fn(), reviewGithub: vi.fn(), releases: vi.fn(), archmap: vi.fn(), compareReleases: vi.fn(),
} }));

const header: ReviewHeader = {
  repo: "/Users/me/entourage", repo_name: "entourage", github: "o/entourage", ref: "9", branch: "feat/ai-connections", base: "main",
  base_sha: "20fd0de1111", head_sha: "9fc926e2222", commits: 1, previous_head: null,
  pr: { number: 9, title: "Verified AI connections", body: "", url: "https://github.com/o/entourage/pull/9", state: "open", draft: false, author: "bot", head: "feat/ai-connections", head_sha: "9fc926e2222", base: "main", merge_commit_sha: null, merged_at: null, updated_at: null, created_at: null },
  row: { status: "conflict", label: "Conflict with base", checks: null, size: { files: 3, additions: 30, deletions: 3 } } as unknown as ReviewHeader["row"],
  session: { key: "s-main", name: "main", progress: null },
};
const file = (path: string, extra: Partial<DiffFile> = {}): DiffFile => ({
  path, old_path: null, status: "modified", binary: false, additions: 2, deletions: 1, truncated: false, total_lines: 4,
  hunks: [{ header: "message SetApiKeyRequest {", old_start: 112, old_lines: 2, new_start: 112, new_lines: 3, lines: [
    { kind: " ", text: "  string user_id = 1;" }, { kind: "-", text: "  string provider = 2;  // gemini | openai" },
    { kind: "+", text: "  string provider = 2;  // gemini | openai | claude" }, { kind: "+", text: "  string api_key = 3;" }] }],
  bucket: "read", bucket_rule: "read", rules: [], hash: `hash-${path}`, viewed: false, stale: false, changed_since: null, whitespace_only: false, ...extra,
});
const diff: ReviewDiff = { base_sha: "20fd0de1111", head_sha: "9fc926e2222", since: null, whitespace: true,
  files: [file("shared/proto/auth.proto"), file("backend/entourage/services/auth_service.py"), file("backend/entourage/services/ai_connection.py", { status: "added", additions: 69, deletions: 0 })],
  counts: { high: 0, read: 3, skim: 0, viewed: 0, changed_since: 0 } };
const release: ReviewCard = { id: "release", number: 9, title: "Release impact", status: "ready", data: { low_risk: "false", condition: "", verdict: "", checks: [
  { name: "Conflict with base", hits: [], status: "fail", note: "" }, { name: "Security or credential path", hits: [], status: "pass", note: "" },
  { name: "Schema or data path", hits: [], status: "pass", note: "" }, { name: "Installer, updater or build path", hits: [], status: "pass", note: "" }] } };

beforeEach(() => {
  vi.mocked(api.reviewHeader).mockResolvedValue(header);
  vi.mocked(api.reviewDiff).mockResolvedValue(diff);
  vi.mocked(api.reviewCards).mockResolvedValue({ head_sha: "9fc926e2222", stale: false, running: false, cards: [release] });
  vi.mocked(api.reviewChat).mockResolvedValue({ messages: [], suggestions: [] });
  vi.mocked(api.releases).mockResolvedValue({ releases: [], maps: {}, touched: [], shipped: null });
  localStorage.clear();
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function open() {
  render(<StaticEdition features={["pr_review", "review_v2"]}><ReviewWorkspace repo={header.repo} target="9" onBack={vi.fn()} /></StaticEdition>);
}

it("shows the five boards behind the flag, with chat folded until asked for", async () => {
  open();
  const tabs = await screen.findByRole("tablist", { name: "Review views" });
  expect(within(tabs).getAllByRole("tab").map(tab => tab.textContent?.replace(/\d+\/\d+/, ""))).toEqual(["Map", "Changes", "Files", "Release readinessnot ready", "History"]);
  expect(screen.queryByRole("complementary", { name: "Review chat" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Chat/ }));
  expect(await screen.findByRole("complementary", { name: "Review chat" })).toBeInTheDocument();
});

it("lists files in folders and asks about the clicked line with that line quoted", async () => {
  vi.mocked(api.reviewAsk).mockResolvedValue({ q: "", a: "Claude becomes a valid provider.", citations: [{ path: "backend/entourage/models/api_key.py", line: 19 }], hunk: null, files: [], at: 1 });
  open();
  fireEvent.click(await screen.findByRole("tab", { name: /Files/ }));
  const tree = await screen.findByRole("navigation", { name: "Changed files" });
  expect(within(tree).getByRole("button", { name: /backend\/entourage\/services/ })).toHaveTextContent("2 · +71 −1");
  fireEvent.click(within(tree).getByRole("button", { name: /auth\.proto/ }));
  fireEvent.click(screen.getAllByRole("button", { name: "Line 113" })[0]);
  const ask = screen.getByRole("textbox", { name: "Question about the selected lines" });
  fireEvent.change(ask, { target: { value: "What else must accept claude?" } });
  fireEvent.submit(ask.closest("form")!);
  expect(await screen.findByText("Claude becomes a valid provider.")).toBeInTheDocument();
  const sent = vi.mocked(api.reviewAsk).mock.calls[0][2];
  expect(sent.question).toBe("What else must accept claude?");
  expect(sent.hunk).toBe("shared/proto/auth.proto:113\n-  string provider = 2;  // gemini | openai\n+  string provider = 2;  // gemini | openai | claude");
});

it("collects line notes into the request-changes review", async () => {
  vi.mocked(api.reviewGithub).mockResolvedValue({ id: 1, url: "u", state: "CHANGES_REQUESTED" });
  open();
  fireEvent.click(await screen.findByRole("tab", { name: /Files/ }));
  await screen.findByRole("navigation", { name: "Changed files" });
  fireEvent.click(screen.getAllByRole("button", { name: "Line 114" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "Note for the author" }));
  const note = screen.getByRole("textbox", { name: "Note for the author" });
  fireEvent.change(note, { target: { value: "api_key needs a length cap" } });
  fireEvent.submit(note.closest("form")!);
  fireEvent.click(screen.getByRole("button", { name: "Request changes · 1 note" }));
  const body = screen.getByRole("textbox", { name: "Review body" }) as HTMLTextAreaElement;
  expect(body.value).toBe("**backend/entourage/services/ai_connection.py:114**\napi_key needs a length cap");
  fireEvent.click(screen.getByRole("button", { name: "Post as review" }));
  expect(api.reviewGithub).toHaveBeenCalledWith(header.repo, "9", { event: "REQUEST_CHANGES", body: body.value });
});

it("sends a blocking readiness item to the authoring session's inbox in one click", async () => {
  vi.mocked(api.reviewSend).mockResolvedValue({ delivered: "inbox", message_id: "m1", session: "s-main" });
  open();
  fireEvent.click(await screen.findByRole("tab", { name: /Release readiness/ }));
  expect(await screen.findByText("Not ready to release")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ask main to fix →" }));
  const text = screen.getByRole("textbox", { name: "Fix request" }) as HTMLTextAreaElement;
  expect(text.value).toContain("Rebase feat/ai-connections onto main");
  fireEvent.click(screen.getByRole("button", { name: "Send to main" }));
  expect(await screen.findByText("Sent to main · waiting")).toBeInTheDocument();
  expect(api.reviewSend).toHaveBeenCalledWith(header.repo, "9", { card: "release", items: [text.value], session: "s-main" });
});
