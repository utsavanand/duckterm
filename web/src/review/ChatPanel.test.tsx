import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ReviewHeader } from "../api";
import { ChatPanel } from "./ChatPanel";

vi.mock("../api", () => ({ api: { reviewChat: vi.fn(), reviewAsk: vi.fn(), reviewDraft: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const header: ReviewHeader = {
  repo: "/r", repo_name: "r", github: "o/r", ref: "307", branch: "duckterm/aaa", base: "main", base_sha: "1", head_sha: "2", commits: 1, previous_head: null, pr: null, row: null, session: null,
};

function mount(quote = "") {
  const props = { onClearQuote: vi.fn(), onOpenFile: vi.fn(), onDraft: vi.fn(), onCollapse: vi.fn(), onQuestionTaken: vi.fn() };
  render(<ChatPanel header={header} quote={quote} pendingQuestion="" {...props} />);
  return props;
}

it("asks with the quoted hunk, renders the answer with clickable citations, and clears the quote", async () => {
  vi.mocked(api.reviewChat).mockResolvedValue({ messages: [], suggestions: ["What is the riskiest hunk and why?"] });
  vi.mocked(api.reviewAsk).mockResolvedValue({ q: "Why?", a: "Because of `x` (src/db.py:12).", citations: [{ path: "src/db.py", line: 12 }], hunk: "src/db.py @@ def a", files: [], at: 1 });
  const props = mount("src/db.py @@ def a");
  await screen.findByRole("button", { name: "What is the riskiest hunk and why?" });
  fireEvent.change(screen.getByRole("textbox", { name: "Ask about this pull request" }), { target: { value: "Why?" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Ask about this pull request" }), { key: "Enter" });
  expect(api.reviewAsk).toHaveBeenCalledWith("/r", "307", { question: "Why?", hunk: "src/db.py @@ def a" });
  expect(await screen.findByText("x")).toHaveProperty("tagName", "CODE");
  fireEvent.click(screen.getByRole("button", { name: "src/db.py:12" }));
  expect(props.onOpenFile).toHaveBeenCalledWith("src/db.py", 12);
  expect(props.onClearQuote).toHaveBeenCalled();
});

it("drafts a request-changes review from the suggestion and shows errors inline", async () => {
  vi.mocked(api.reviewChat).mockResolvedValue({ messages: [{ q: "Earlier", a: "Answer", citations: [], hunk: null, files: [], at: 1 }], suggestions: ["Draft a request-changes review from the open questions"] });
  vi.mocked(api.reviewDraft).mockResolvedValue({ body: "Review of duckterm/aaa\n- fix x" });
  vi.mocked(api.reviewAsk).mockRejectedValue(new Error("Automatic preparation is disabled on this host"));
  const props = mount();
  expect(await screen.findByText("Earlier")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Draft a request-changes review from the open questions" }));
  expect(await vi.waitFor(() => props.onDraft.mock.calls[0][0])).toBe("Review of duckterm/aaa\n- fix x");
  expect(api.reviewAsk).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "Ask about this pull request" }), { target: { value: "Hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Automatic preparation is disabled on this host");
  expect(screen.getByRole("textbox", { name: "Ask about this pull request" })).toHaveValue("Hello");
});
