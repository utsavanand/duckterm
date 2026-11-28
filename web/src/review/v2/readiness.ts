import type { ReviewCard, ReviewHeader } from "../../api";
import { TEST } from "./tree";
import type { BlastData, GuardrailsData, QuestionsData, ReleaseCheck, ReleaseData, TestsData } from "../cardModel";

export type ReadyStatus = "block" | "part" | "ok" | "wait";
export interface ReadyLine { text: string; where?: string }
// card: the review card the row is built from, so a fix request is recorded
// against it (the send route keys sent items by card).
export interface ReadyRow { id: string; name: string; sub: string; status: ReadyStatus; lines: ReadyLine[]; card: string; ask: string | null }

export const STATUS_LABELS: Record<ReadyStatus, string> = { block: "Blocking", part: "Partial", ok: "Ready", wait: "Not read yet" };

function ready<T>(cards: ReviewCard[], id: string): T | null {
  const card = cards.find(item => item.id === id);
  return card?.status === "ready" && card.data ? card.data as unknown as T : null;
}

function check(release: ReleaseData | null, name: string): ReleaseCheck | null {
  return release?.checks.find(item => item.name === name) ?? null;
}

function waiting(id: string, name: string, sub: string, card: string, cards: ReviewCard[]): ReadyRow {
  const state = cards.find(item => item.id === card)?.status;
  const text = state === "pending" || state === "stale" ? "Being read now" : state === "omitted" || state === "failed" ? "Could not be read for this head" : "Generate the review to read this";
  return { id, name, sub, status: "wait", lines: [{ text }], card, ask: null };
}

// Test runs on this head. The header's checks are read fresh from GitHub, so
// they win over the release card's copy. Gate logs have no repository/head
// identity: a newer timestamp alone cannot establish a passing run for this head.
type RunResult = "fail" | "pending" | "pass" | "none";
function ciResult(header: ReviewHeader, release: ReleaseData | null): { result: RunResult; text: string } {
  const checks = header.row?.checks;
  if (checks && checks.total > 0) {
    if (checks.failed) return { result: "fail", text: `CI: ${checks.failed} of ${checks.total} checks failed on this head` };
    if (checks.pending) return { result: "pending", text: `CI: ${checks.pending} of ${checks.total} checks still running` };
    return { result: "pass", text: `CI: ${checks.passed}/${checks.total} passed on this head` };
  }
  const ci = check(release, "CI");
  if (ci?.status === "fail") return { result: "fail", text: `CI: ${ci.note || "failed"}` };
  if (ci?.status === "pass") return { result: "pass", text: `CI: ${ci.note || "passed"}` };
  return { result: "none", text: "CI: no result for this head" };
}

function request(pr: string, lead: string, lines: ReadyLine[]): string {
  return `${pr}: ${lead}\n${lines.map(line => `- ${line.text}${line.where ? ` (${line.where})` : ""}`).join("\n")}`;
}

export function readinessRows(header: ReviewHeader, cards: ReviewCard[]): ReadyRow[] {
  const pr = header.pr ? `PR #${header.pr.number}` : header.branch;
  const release = ready<ReleaseData>(cards, "release");
  const tests = ready<TestsData>(cards, "tests");
  const blast = ready<BlastData>(cards, "blast");
  const guard = ready<GuardrailsData>(cards, "guardrails");
  const questions = ready<QuestionsData>(cards, "questions");
  const rows: ReadyRow[] = [];

  const conflict = header.row?.status === "conflict" || check(release, "Conflict with base")?.status === "fail";
  const moved = release?.base_moved;
  const drift: ReadyLine[] = moved && moved.commits > 0
    ? [{ text: `${header.base} has ${moved.commits} ${moved.commits === 1 ? "commit" : "commits"} since the branch point${moved.overlap.length ? `, changing ${moved.overlap.length} of this PR's files` : ""}`, where: "git log" },
      ...moved.overlap.slice(0, 8).map(path => ({ text: `Also changed on ${header.base}: ${path}` }))]
    : [];
  rows.push(conflict
    ? { id: "merge", name: "Merge", sub: "git", status: "block", card: "release", lines: [{ text: `Conflicts with ${header.base}`, where: "merge-tree" }, ...drift],
      ask: `${pr} conflicts with ${header.base}. Rebase ${header.branch} onto ${header.base}, regenerate any generated files, rerun the tests and push.${moved?.overlap.length ? ` Files changed on both sides: ${moved.overlap.join(", ")}.` : ""}` }
    : { id: "merge", name: "Merge", sub: "git", status: "ok", card: "release", lines: [{ text: `Merges cleanly into ${header.base}`, where: "merge-tree" }, ...drift], ask: null });

  if (!tests) rows.push(waiting("tests", "Unit tests", "changed code", "tests", cards));
  else {
    const uncovered = tests.coverage.filter(row => row.covered === "no");
    const partial = tests.coverage.filter(row => row.covered === "partial");
    const lines: ReadyLine[] = [
      ...uncovered.map(row => ({ text: `No test reaches ${row.path}` })),
      ...tests.gaps.map(text => ({ text })),
      ...tests.slop.map(item => ({ text: `Test with ${item.kind}`, where: `${item.path}:${item.line}` })),
    ];
    const fix = [...lines];
    if (blast?.symbols.some(symbol => !TEST.test(symbol.path))) {
      // Tests and test fixtures are not code that needs a test of its own.
      const live = blast.symbols.filter(symbol => symbol.change !== "removed" && !TEST.test(symbol.path));
      const reached = live.filter(symbol => symbol.tested);
      const missed = live.filter(symbol => !symbol.tested);
      lines.unshift({ text: `${reached.length} of ${live.length} changed functions have a test that reaches them`, where: "symbol index" });
      if (missed.length) {
        const named = { text: `No test: ${missed.slice(0, 6).map(symbol => symbol.symbol).join(", ")}${missed.length > 6 ? ` and ${missed.length - 6} more` : ""}` };
        lines.splice(1, 0, named); fix.unshift(named);
      }
    }
    const ci = ciResult(header, release);
    const gate = tests.gate ? (tests.gate.passed ? "pass" : "fail") : "none";
    const failed = ci.result === "fail" || gate === "fail";
    // Ready needs a passing CI result. An unattributed gate may block on failure,
    // but cannot supply positive evidence that this head passed.
    const ran = !failed && ci.result === "pass";
    if (!lines.length) lines.push({ text: "Every changed file has a test that reaches it" });
    lines.push({ text: ci.text, where: "CI" });
    lines.push({ text: tests.gate ? `Local gate log ${tests.gate.passed ? "passed" : "failed"} (not tied to this head), ${tests.gate.age_minutes} min ago` : "Gate: no run since this head was committed", where: "gate" });
    const status: ReadyStatus = uncovered.length || tests.slop.length || failed ? "block" : partial.length || tests.gaps.length || !ran ? "part" : "ok";
    const lead = failed
      ? `tests fail on this head (${[ci.result === "fail" ? ci.text : "", gate === "fail" ? "local gate failed" : ""].filter(Boolean).join("; ")}). Fix the failures, then rerun the gate and push.`
      : fix.length ? "add the missing tests, then push so CI runs on this head." : "CI has not passed on this head yet. Push so CI runs, or wait for it to finish.";
    rows.push({ id: "tests", name: "Unit tests", sub: "changed code", status, lines, card: "tests",
      ask: status === "ok" ? null : request(pr, lead, fix) });
  }

  if (!release) rows.push(waiting("security", "Security", "credentials and secrets", "release", cards));
  else {
    const security = check(release, "Security or credential path");
    const touched = security && (security.status === "fail" || security.status === "conditional");
    rows.push(touched
      ? { id: "security", name: "Security", sub: "credentials and secrets", status: "block", card: "release",
        lines: [{ text: `Touches ${security.hits.slice(0, 3).join(", ") || "the credentials path"}: needs a security owner's sign-off`, where: "rule: security" }],
        ask: `${pr} changes the security or credentials path (${security.hits.slice(0, 5).join(", ")}). Write a short note for the reviewer: every place secrets could reach logs, errors or responses, and how each is prevented, with file:line.` }
      : { id: "security", name: "Security", sub: "credentials and secrets", status: "ok", card: "release", lines: [{ text: "No security or credentials path touched", where: "rules" }], ask: null });
  }

  if (!questions) rows.push(waiting("manual", "Manual tests", "by a person", "questions", cards));
  else {
    const manual = questions.items.filter(item => item.kind === "manual");
    rows.push(manual.length
      ? { id: "manual", name: "Manual tests", sub: "by a person", status: "part", card: "questions",
        lines: manual.map(item => ({ text: item.text, where: item.paths[0] })),
        ask: request(pr, "run these hand tests against a real build and post what you saw.", manual.map(item => ({ text: item.text }))) }
      : { id: "manual", name: "Manual tests", sub: "by a person", status: "ok", card: "questions", lines: [{ text: "Nothing in this diff needs a hand test" }], ask: null });
  }

  if (!blast) rows.push(waiting("blast", "Blast radius", "code outside the diff", "blast", cards));
  else {
    const outside = blast.symbols.filter(symbol => symbol.outside_count > 0);
    const untested = outside.filter(symbol => !symbol.tested);
    const lines: ReadyLine[] = [{ text: `${outside.length} of ${blast.symbols.length} changed symbols are called from outside the diff`, where: "symbol index" }];
    lines.push(...untested.slice(0, 4).map(symbol => ({ text: `${symbol.symbol}: ${symbol.outside_count} outside callers, no test`, where: `${symbol.path}:${symbol.line}` })));
    rows.push({ id: "blast", name: "Blast radius", sub: "code outside the diff", status: untested.length ? "part" : "ok", lines, card: "blast",
      ask: untested.length ? request(pr, "these changed symbols have callers outside the diff and no test. Add a test for each, or say why the callers are unaffected.", lines.slice(1)) : null });
  }

  if (!guard) rows.push(waiting("guardrails", "Guardrails", "AGENTS.md and slop rules", "guardrails", cards));
  else {
    const findings = guard.findings.filter(item => item.status === "finding");
    const passed = guard.checks.filter(item => item.status === "pass").length;
    const lines: ReadyLine[] = findings.map(item => ({ text: `${item.rule}: ${item.note}`, where: item.location || undefined }));
    lines.push({ text: `${passed} ${passed === 1 ? "check" : "checks"} passed`, where: "rules" });
    rows.push({ id: "guardrails", name: "Guardrails", sub: "AGENTS.md and slop rules", status: findings.length ? "part" : "ok", lines, card: "guardrails",
      ask: findings.length ? request(pr, "fix these guardrail findings.", lines.slice(0, -1)) : null });
  }

  if (!release) {
    rows.push(waiting("rollback", "Rollback", "how to undo it", "release", cards));
    rows.push(waiting("data", "Data and schema", "stored data", "release", cards));
    rows.push(waiting("deploy", "Config and deploy", "installer, updater, build", "release", cards));
  } else {
    const schema = check(release, "Schema or data path");
    const installer = check(release, "Installer, updater or build path");
    const dataTouched = schema?.status === "fail" || schema?.status === "conditional";
    const installTouched = installer?.status === "fail" || installer?.status === "conditional";
    const lines: ReadyLine[] = [];
    if (dataTouched) lines.push({ text: "Stored data changes: reverting the merge commit does not undo it", where: "rule: schema" });
    if (installTouched) lines.push({ text: "Installer, updater or build changes: a revert reaches installed copies only through a new release", where: "rule: installer" });
    if (!lines.length) lines.push({ text: "Code only: no stored data, installer or build change", where: "rules" }, { text: "Revert the merge commit to roll back", where: "git" });
    rows.push({ id: "rollback", name: "Rollback", sub: "how to undo it", status: dataTouched ? "block" : installTouched ? "part" : "ok", lines, card: "release",
      ask: dataTouched || installTouched ? request(pr, "write the rollback plan for these changes: what a revert leaves behind and how to undo it.", lines) : null });
    const dataLines = dataTouched ? schema!.hits.slice(0, 8).map(path => ({ text: `Changes ${path}`, where: "rule: schema" })) : [{ text: "No schema, migration or stored-data path changed", where: "rules" }];
    rows.push({ id: "data", name: "Data and schema", sub: "stored data", status: dataTouched ? "block" : "ok", lines: dataLines, card: "release",
      ask: dataTouched ? request(pr, "changes stored data. Say what each change does to existing rows, whether old builds can still read them, and test the upgrade on a copy.", dataLines) : null });
    const deployLines = installTouched ? installer!.hits.slice(0, 8).map(path => ({ text: `Changes ${path}`, where: "rule: installer" })) : [{ text: "No installer, updater, build or workflow changed", where: "rules" }];
    rows.push({ id: "deploy", name: "Config and deploy", sub: "installer, updater, build", status: installTouched ? "part" : "ok", lines: deployLines, card: "release",
      ask: installTouched ? request(pr, "changes how DuckTerm is built or installed. Build the package from a fresh worktree and install it on a clean machine, then post what you saw.", deployLines) : null });
  }
  return rows;
}

export function overall(rows: ReadyRow[]): { status: ReadyStatus; label: string; counts: Record<ReadyStatus, number> } {
  const counts: Record<ReadyStatus, number> = { block: 0, part: 0, ok: 0, wait: 0 };
  for (const row of rows) counts[row.status] += 1;
  if (counts.block) return { status: "block", label: "Not ready to release", counts };
  if (counts.wait) return { status: "wait", label: "Not read yet", counts };
  if (counts.part) return { status: "part", label: "Ready with open items", counts };
  return { status: "ok", label: "Ready to release", counts };
}
