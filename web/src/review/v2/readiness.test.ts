import { describe, expect, it } from "vitest";
import type { ReviewCard, ReviewHeader } from "../../api";
import { overall, readinessRows } from "./readiness";

const header = { repo: "/r", repo_name: "r", github: "o/r", ref: "9", branch: "feat/x", base: "main", base_sha: "a", head_sha: "b", commits: 1,
  pr: { number: 9 }, row: { status: "open" }, previous_head: null, session: { key: "s1", name: "main", progress: null } } as unknown as ReviewHeader;

function card(id: string, data: Record<string, unknown>): ReviewCard {
  return { id, number: 1, title: id, status: "ready", data };
}

const release = card("release", { low_risk: "false", condition: "", verdict: "", checks: [
  { name: "Schema or data path", hits: [], status: "pass", note: "" },
  { name: "Installer, updater or build path", hits: [], status: "pass", note: "" },
  { name: "Security or credential path", hits: ["backend/auth_service.py"], status: "fail", note: "" },
  { name: "Conflict with base", hits: [], status: "fail", note: "merge-tree reports a conflict" },
  { name: "CI", hits: [], status: "conditional", note: "0/0 passed" },
] });
const tests = card("tests", { coverage: [{ path: "llm_manager.py", tests: [], covered: "no", note: "" }], gaps: ["Nothing tests create_user_llm_provider"],
  suggested: [], slop: [{ path: "test_ai.py", line: 140, kind: "no assertion", text: "" }], existing_tests: {}, report: null, gate: null, model_status: "ready", verdict: "" });

describe("readinessRows", () => {
  it("blocks on a conflict, a touched security path and untested code, with a fix request for each", () => {
    const rows = readinessRows(header, [release, tests]);
    const byId = Object.fromEntries(rows.map(row => [row.id, row]));
    expect(byId.merge.status).toBe("block");
    expect(byId.merge.ask).toContain("Rebase feat/x onto main");
    expect(byId.security.status).toBe("block");
    expect(byId.security.lines[0].text).toContain("backend/auth_service.py");
    expect(byId.tests.status).toBe("block");
    expect(byId.tests.ask).toContain("No test reaches llm_manager.py");
    expect(byId.tests.ask).toContain("test_ai.py:140");
    expect(byId.tests.ask).not.toContain("CI:");
    expect(byId.rollback.status).toBe("ok");
    expect(byId.rollback.ask).toBeNull();
  });

  it("says a row is not read yet when its card is missing or still running, and asks nothing", () => {
    const pending: ReviewCard = { id: "blast", number: 4, title: "Blast", status: "pending" };
    const rows = readinessRows(header, [pending]);
    const blast = rows.find(row => row.id === "blast")!;
    expect(blast.status).toBe("wait");
    expect(blast.lines[0].text).toBe("Being read now");
    expect(blast.ask).toBeNull();
    expect(rows.find(row => row.id === "guardrails")!.lines[0].text).toBe("Generate the review to read this");
  });

  describe("test runs on this head decide the Unit tests row", () => {
    const covered = (gate: { passed: boolean } | null) => card("tests", { coverage: [{ path: "a.py", covered: "yes", tests: ["test_a.py"] }], gaps: [], slop: [],
      gate: gate && { at: 1, age_minutes: 3, passed: gate.passed, summary: null } });
    const rest = [card("questions", { items: [] }), card("blast", { symbols: [] }), card("guardrails", { findings: [], checks: [] }),
      card("release", { checks: [{ name: "CI", status: "conditional", note: "", hits: [] }] })];
    const withChecks = (checks: Record<string, number> | null) => ({ ...header, row: { status: "open", checks } }) as unknown as ReviewHeader;
    const run = (checks: Record<string, number> | null, gate: { passed: boolean } | null) => {
      const rows = readinessRows(withChecks(checks), [covered(gate), ...rest]);
      return { row: rows.find(row => row.id === "tests")!, total: overall(rows) };
    };

    it.each([
      ["CI failed", { total: 2, failed: 1, pending: 0, passed: 1 }, { passed: true }, "block", "Not ready to release"],
      ["the gate failed", { total: 2, failed: 0, pending: 0, passed: 2 }, { passed: false }, "block", "Not ready to release"],
      ["CI is still running", { total: 2, failed: 0, pending: 1, passed: 1 }, { passed: true }, "part", "Ready with open items"],
      ["nothing ran on this head", null, null, "part", "Ready with open items"],
      ["CI passed", { total: 2, failed: 0, pending: 0, passed: 2 }, null, "ok", "Ready to release"],
      ["only an unattributed gate passed", null, { passed: true }, "part", "Ready with open items"],
    ] as const)("%s", (_, checks, gate, status, label) => {
      const { row, total } = run(checks, gate);
      expect(row.status).toBe(status);
      expect(total.label).toBe(label);
      expect(row.ask === null).toBe(status === "ok");
    });

    it("asks the author to fix the failure, naming it", () => {
      expect(run({ total: 2, failed: 1, pending: 0, passed: 1 }, null).row.ask).toContain("tests fail on this head (CI: 1 of 2 checks failed on this head)");
      expect(run(null, { passed: false }).row.ask).toContain("tests fail on this head (local gate failed)");
    });

    it("falls back to the release card's CI check when GitHub reported none", () => {
      const rows = readinessRows(withChecks(null), [covered(null), ...rest.slice(0, 3), card("release", { checks: [{ name: "CI", status: "fail", note: "1 check failed", hits: [] }] })]);
      expect(rows.find(row => row.id === "tests")!.status).toBe("block");
    });
  });

  it("summarises: any block wins, then unread rows, then partial", () => {
    expect(overall(readinessRows(header, [release, tests])).label).toBe("Not ready to release");
    const clean = { ...header, row: { status: "open" } } as unknown as ReviewHeader;
    expect(overall(readinessRows(clean, [])).status).toBe("wait");
  });
});
