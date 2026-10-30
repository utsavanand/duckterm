import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ReviewCards, type ReviewHeader } from "../api";
import { CardsPane } from "./CardsPane";

vi.mock("../api", () => ({ api: { reviewCards: vi.fn(), regenerateCards: vi.fn(), reviewSend: vi.fn() } }));
vi.mock("./useMermaid", () => ({ useMermaid: () => undefined }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const header: ReviewHeader = {
  repo: "/r", repo_name: "r", github: "o/r", ref: "307", branch: "duckterm/aaa", base: "main", base_sha: "1", head_sha: "2222222bbbb", commits: 1, previous_head: null, pr: null, row: null,
  session: { key: "s1", name: "ui-dev", progress: null },
};
const cards: ReviewCards = {
  head_sha: "2222222bbbb", stale: false, running: false,
  cards: [
    { id: "intent", number: 1, title: "Intent vs diff", status: "ready", data: { summary: "Adds **table a**.", claims: [{ claim: "Adds table a", in_diff: "true", evidence: ["src/db.py"] }, { claim: "Adds tests", in_diff: "false", evidence: [], evidence_missing: ["tests/x.py"] }], unclaimed: [], diagram: "graph LR\n a-->b", verdict: "2 claims · 1 not in diff" } },
    { id: "walkthrough", number: 2, title: "Walkthrough", status: "ready", data: { stops: [{ path: "src/db.py", hunk: 2, title: "Table", why: "Data first", bucket: "high" }], minutes: { high: 1.2, read: 0.5, skim: 0.1 }, verdict: "1 stops · about 2 min" } },
    { id: "risk", number: 3, title: "Risk map", status: "ready", data: { files: [{ path: "src/db.py", bucket: "high", rules: [{ bucket: "high", id: "schema", reason: "Touches persistence" }], lines: 30, reverts: 1, hotspot: null }, { path: "docs/a.md", bucket: "skim", rules: [], lines: 3, reverts: 0, hotspot: null }], counts: { high: 1, read: 0, skim: 1 }, reasons: [{ path: "src/db.py", reason: "Schema drift" }], verdict: "1 high-risk files" } },
    { id: "blast", number: 4, title: "Blast radius", status: "later", reason: "Arrives with the symbol index (build step 5)" },
    { id: "behaviour", number: 5, title: "Behaviour changes", status: "pending" },
    { id: "tests", number: 6, title: "Tests", status: "later", reason: "Later" },
    { id: "guardrails", number: 7, title: "Guardrails", status: "later", reason: "Later" },
    { id: "author", number: 8, title: "How the author got here", status: "later", reason: "Later" },
    { id: "release", number: 9, title: "Release impact", status: "ready", data: { checks: [{ name: "Schema or data path", hits: ["src/db.py"], status: "fail", note: "" }, { name: "CI", hits: [], status: "pass", note: "2/2 passed" }], low_risk: "false", condition: "Outside the low-risk rule: Schema or data path", verdict: "Not low risk" } },
    { id: "questions", number: 10, title: "Open questions", status: "omitted", reason: "provider down" },
  ],
};

it("shows every card's verdict in the strip, opens files from a card, and steps with the arrows", async () => {
  vi.mocked(api.reviewCards).mockResolvedValue(cards);
  const openFile = vi.fn();
  render(<CardsPane header={header} active={false} openFile={openFile} ask={vi.fn()} />);
  const strip = await screen.findByRole("tablist", { name: "Review cards" });
  const tabs = within(strip).getAllByRole("tab");
  expect(tabs).toHaveLength(10);
  expect(tabs[0]).toHaveTextContent("2 claims · 1 not in diff");
  expect(tabs[0]).toHaveClass("is-danger");
  expect(tabs[4]).toHaveTextContent("Writing…");
  expect(tabs[8]).toHaveClass("is-danger");
  expect(tabs[9]).toHaveTextContent("Omitted: provider down");
  // Card 1 is open: the claim without evidence is flagged and the summary is markdown.
  const card = screen.getByRole("tabpanel", { name: "1 Intent vs diff" });
  expect(within(card).getByText("table a")).toHaveProperty("tagName", "STRONG");
  expect(within(card).getByText("no")).toHaveClass("is-false");
  fireEvent.click(within(card).getByRole("button", { name: "src/db.py" }));
  expect(openFile).toHaveBeenCalledWith("src/db.py", undefined);
  fireEvent.click(screen.getByRole("button", { name: "Next card" }));
  fireEvent.click(within(screen.getByRole("tabpanel", { name: "2 Walkthrough" })).getByRole("button", { name: /Table/ }));
  expect(openFile).toHaveBeenLastCalledWith("src/db.py", 2);
  fireEvent.click(tabs[2]);
  expect(screen.getByRole("tabpanel", { name: "3 Risk map" })).toHaveTextContent("Schema drift");
  expect(screen.getByTitle("src/db.py · 30 lines")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Previous card" }));
  expect(screen.getByRole("tabpanel", { name: "2 Walkthrough" })).toBeInTheDocument();
});

it("explains omitted and later cards, shows the release verdict, and regenerates on request", async () => {
  const settled = { ...cards, cards: cards.cards.map(card => card.id === "behaviour" ? { ...card, status: "omitted" as const, reason: "provider down" } : card) };
  vi.mocked(api.reviewCards).mockResolvedValue(settled);
  vi.mocked(api.regenerateCards).mockResolvedValue({ ...settled, started: true, running: true });
  render(<CardsPane header={header} active={false} openFile={vi.fn()} ask={vi.fn()} />);
  const strip = await screen.findByRole("tablist", { name: "Review cards" });
  fireEvent.click(within(strip).getAllByRole("tab")[3]);
  expect(screen.getByRole("tabpanel", { name: "4 Blast radius" })).toHaveTextContent("Arrives with the symbol index");
  fireEvent.click(within(strip).getAllByRole("tab")[8]);
  const release = screen.getByRole("tabpanel", { name: "9 Release impact" });
  expect(release).toHaveTextContent("Not low risk · Outside the low-risk rule: Schema or data path");
  expect(within(release).getByText("fail")).toBeInTheDocument();
  fireEvent.click(within(strip).getAllByRole("tab")[9]);
  expect(screen.getByRole("tabpanel", { name: "10 Open questions" })).toHaveTextContent("Omitted: provider down");
  fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
  expect(api.regenerateCards).toHaveBeenCalledWith("/r", "307");
  expect(await screen.findByText(/writing/)).toBeInTheDocument();
});

it("sends open questions to the authoring session and asks the chat", async () => {
  const questions = { ...cards, cards: cards.cards.map(card => card.id === "questions" ? { ...card, status: "ready" as const, reason: undefined, data: { items: [{ kind: "unverified", text: "Where are the tests?", paths: ["src/db.py"] }, { kind: "manual", text: "Run it", paths: [] }], verdict: "2 open" } } : card) };
  vi.mocked(api.reviewCards).mockResolvedValue(questions);
  vi.mocked(api.reviewSend).mockResolvedValue({ delivered: "inbox", message_id: "m1", session: "s1" });
  const ask = vi.fn();
  render(<CardsPane header={header} active={false} openFile={vi.fn()} ask={ask} />);
  const strip = await screen.findByRole("tablist", { name: "Review cards" });
  fireEvent.click(within(strip).getAllByRole("tab")[9]);
  const card = screen.getByRole("tabpanel", { name: "10 Open questions" });
  expect(within(card).getByRole("heading", { name: "Not shown in the diff" })).toBeInTheDocument();
  fireEvent.click(within(card).getAllByRole("button", { name: "Ask" })[0]);
  expect(ask).toHaveBeenCalledWith("Where are the tests?");
  fireEvent.click(within(card).getByRole("button", { name: "Send all to ui-dev" }));
  expect(api.reviewSend).toHaveBeenCalledWith("/r", "307", { card: "questions", items: ["Where are the tests? (src/db.py)", "Run it"], session: "s1" });
  expect(await screen.findByRole("button", { name: "Send all to ui-dev" })).toBeEnabled();
});

it("says which cards a pending card is being written with", async () => {
  const grouped = { ...cards, running: true, cards: cards.cards.map(card => card.id === "behaviour" ? { ...card, with: ["Intent vs diff", "Walkthrough"] } : card) };
  vi.mocked(api.reviewCards).mockResolvedValue(grouped);
  render(<CardsPane header={header} active={false} openFile={vi.fn()} ask={vi.fn()} />);
  const tab = within(await screen.findByRole("tablist", { name: "Review cards" })).getAllByRole("tab")[4];
  expect(tab).toHaveTextContent("Writing with Intent vs diff, Walkthrough…");
  fireEvent.click(tab);
  expect(screen.getByRole("tabpanel", { name: "5 Behaviour changes" })).toHaveTextContent("Writing this card together with Intent vs diff and Walkthrough…");
});
