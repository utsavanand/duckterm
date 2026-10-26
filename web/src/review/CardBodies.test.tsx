import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReviewHeader } from "../api";
import { AuthorCard, BlastCard, GuardrailsCard, TestsCard, type CardActions } from "./CardBodies";
import type { AuthorData, BlastData, GuardrailsData, TestsData } from "./cardModel";

vi.mock("./useMermaid", () => ({ useMermaid: () => undefined }));
afterEach(cleanup);
const header: ReviewHeader = { repo: "/r", repo_name: "r", github: null, ref: "x", branch: "x", base: "main", base_sha: "1", head_sha: "2", commits: 1, previous_head: null, pr: null, row: null, session: null };
const actions = (): CardActions => ({ openFile: vi.fn(), ask: vi.fn(), send: vi.fn(), header });

it("tests card shows slop, missing test files, and says why coverage bars are absent", () => {
  const data: TestsData = {
    coverage: [{ path: "src/duckterm/server.py", tests: ["tests/unit/test_server.py"], covered: "partial", note: "only the happy path" }],
    gaps: ["No test for the 404 branch"], suggested: [{ name: "test_404", path: "tests/unit/test_server.py", body: "asserts 404 on a missing key" }],
    slop: [{ path: "web/src/A.test.tsx", line: 12, kind: "toBeTruthy", text: "expect(x).toBeTruthy();" }],
    existing_tests: { "src/duckterm/server.py": ["tests/unit/test_server.py"], "src/duckterm/new.py": [] },
    report: null, gate: { at: 1, age_minutes: 12, passed: true, summary: "412 passed" }, model_status: "ready", verdict: "1 slop · 0 uncovered · no coverage report",
  };
  const act = actions();
  render(<TestsCard data={data} actions={act} />);
  expect(screen.getByText(/No coverage report on disk/)).toHaveTextContent("Last local gate passed 12 min ago (412 passed)");
  expect(screen.getByText("partially")).toHaveClass("is-partial");
  expect(screen.getByText("no test file named after it")).toBeInTheDocument();
  expect(screen.getByText("toBeTruthy")).toHaveClass("is-false");
  fireEvent.click(screen.getByRole("button", { name: "web/src/A.test.tsx" }));
  expect(act.openFile).toHaveBeenCalledWith("web/src/A.test.tsx", undefined);
  expect(screen.getByText("test_404")).toBeInTheDocument();
});

it("guardrails card lists mechanical rows and model findings with their locations", () => {
  const data: GuardrailsData = {
    checks: [
      { rule: "No Co-Authored-By or generated-with trailers", status: "finding", note: "", items: [{ sha: "abc1234", kind: "trailer", text: "Co-Authored-By: Claude" }] },
      { rule: "Server stays stdlib-only", status: "pass", note: "no new dependency", items: [] },
      { rule: "Docs ride with code", status: "n/a", note: "no files", items: [] },
    ],
    findings: [{ rule: "Over-engineering", status: "finding", location: "src/duckterm/x.py:4", note: "a wrapper with one caller" }],
    rule_files: ["AGENTS.md", ".claude/rules/overengineering-slop.md"], model_status: "omitted", model_reason: "provider down", verdict: "2 findings",
  };
  const act = actions();
  render(<GuardrailsCard data={data} actions={act} />);
  expect(screen.getByText(/Slop-rule review omitted: provider down/)).toBeInTheDocument();
  expect(screen.getAllByText("finding")).toHaveLength(2);
  expect(screen.getByText("Co-Authored-By: Claude")).toBeInTheDocument();
  expect(screen.getByText("n/a")).toHaveClass("is-partial");
  fireEvent.click(screen.getByRole("button", { name: "src/duckterm/x.py" }));
  expect(act.openFile).toHaveBeenCalledWith("src/duckterm/x.py", undefined);
  expect(screen.getByText(/AGENTS.md, .claude\/rules\/overengineering-slop.md/)).toBeInTheDocument();
});

it("blast radius card lists changed symbols with their callers and explains omitted paragraphs", () => {
  const data: BlastData = {
    symbols: [
      { symbol: "helper", kind: "function", path: "src/duckterm/core.py", line: 2, change: "changed", callers_in_diff: [], callers_outside: ["src/duckterm/cli.py"], outside_count: 3, tested: false },
      { symbol: "brand_new", kind: "function", path: "src/duckterm/core.py", line: 14, change: "added", callers_in_diff: ["src/duckterm/server.py"], callers_outside: [], outside_count: 0, tested: true },
    ],
    diagram: "graph LR\n  n0[\"helper\"]", index: { base_files: 5, head_files: 5 }, limits: "Name-based.", consequence: "", risks: [], model_status: "omitted", model_reason: "provider down", verdict: "2 changed symbols · 1 called outside the diff · 1 untested",
  };
  const act = actions();
  render(<BlastCard data={data} actions={act} />);
  expect(screen.getByText(/Consequence paragraph omitted: provider down/)).toBeInTheDocument();
  expect(screen.getByText("+2 more")).toBeInTheDocument();
  expect(screen.getAllByText("no")[0]).toHaveClass("is-false");
  fireEvent.click(screen.getByRole("button", { name: "src/duckterm/server.py" }));
  expect(act.openFile).toHaveBeenCalledWith("src/duckterm/server.py", undefined);
  expect(screen.getByText(/Index: 5 files at base, 5 at head/)).toBeInTheDocument();
});

it("author card shows the model summaries and the numbered record", () => {
  const data: AuthorData = {
    events: [
      { turn: 1, kind: "prompt", text: "Build the list", ts: 1 },
      { turn: 1, kind: "verify", text: "pytest tests -q", ts: 2 },
      { turn: 2, kind: "abandon", text: "git reset --hard HEAD~1", ts: 3 },
    ],
    turns: 2, commits: 1, pushes: 1, verification_runs: [{ turn: 1, kind: "verify", text: "pytest tests -q", ts: 2 }], abandoned_commands: [{ turn: 2, kind: "abandon", text: "git reset --hard HEAD~1", ts: 3 }],
    owner_decisions: [{ text: "Drop the widget approach", ts: 3 }], session: { key: "s1", name: "main-dev", runtime: "claude-code" },
    trajectory: "Started with a **widget**, dropped it at turn 2.", abandoned: ["Widget approach (turn 2)"], decisions: ["Owner: drop the widget approach"], verification: "pytest ran once at turn 1.",
    model_status: "ready", verdict: "2 turns · 1 commits · 1 reverts · 1 owner decisions",
  };
  render(<AuthorCard data={data} />);
  expect(screen.getByText("widget")).toHaveProperty("tagName", "STRONG");
  expect(screen.getByRole("heading", { name: "Abandoned approaches" })).toBeInTheDocument();
  expect(screen.getByText("Widget approach (turn 2)")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Record · main-dev · 2 turns · 1 commits · 1 pushes/ })).toBeInTheDocument();
  expect(screen.getByText("git reset --hard HEAD~1")).toBeInTheDocument();
  expect(screen.getByText("Undone")).toBeInTheDocument();
});
