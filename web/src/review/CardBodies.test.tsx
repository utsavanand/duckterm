import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReviewHeader } from "../api";
import { GuardrailsCard, TestsCard, type CardActions } from "./CardBodies";
import type { GuardrailsData, TestsData } from "./cardModel";

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
