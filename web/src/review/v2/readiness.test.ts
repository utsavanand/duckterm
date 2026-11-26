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

  it("summarises: any block wins, then unread rows, then partial", () => {
    expect(overall(readinessRows(header, [release, tests])).label).toBe("Not ready to release");
    const clean = { ...header, row: { status: "open" } } as unknown as ReviewHeader;
    expect(overall(readinessRows(clean, [])).status).toBe("wait");
  });
});
