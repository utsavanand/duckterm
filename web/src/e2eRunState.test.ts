// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { initializeRunState, readOwnedState, statePath, writeOwnedState } from "../e2e/run-state";
import teardown from "../e2e/global-teardown";

const roots: string[] = [];
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function context() {
  const root = mkdtempSync(join(tmpdir(), "rd-state-regression-"));
  roots.push(root);
  const env = { RD_TEST_STATE_FILE: join(root, "state.json") } as NodeJS.ProcessEnv;
  initializeRunState(env);
  return env;
}
const fixture = { home: "/unused-test-home", pid: 12345, port: "4490", tmuxSocket: "unused-test-socket" };

it("independent invocations get separate paths while workers inherit one context", () => {
  const first: NodeJS.ProcessEnv = {}, second: NodeJS.ProcessEnv = {};
  initializeRunState(first); initializeRunState(second);
  expect(first.RD_TEST_STATE_FILE).not.toBe(second.RD_TEST_STATE_FILE);
  const worker = { ...first }; initializeRunState(worker);
  expect(worker).toEqual(first);
  expect(() => statePath({})).toThrow("Missing isolated");
});

it("exclusive creation cannot overwrite another run's state", () => {
  const env = context();
  writeOwnedState(fixture, env);
  const before = readFileSync(statePath(env), "utf8");
  expect(() => writeOwnedState({ ...fixture, pid: 98765 }, env)).toThrow();
  expect(readFileSync(statePath(env), "utf8")).toBe(before);
  expect(readOwnedState(env).pid).toBe(fixture.pid);
});

it("foreign state cannot be used by helpers or trigger teardown", async () => {
  const env = context();
  writeFileSync(statePath(env), JSON.stringify({ ...fixture, runId: "other-run" }));
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value!);
  const kill = vi.spyOn(process, "kill").mockReturnValue(true);
  expect(() => readOwnedState()).toThrow("another run");
  await expect(teardown()).rejects.toThrow("another run");
  expect(kill).not.toHaveBeenCalled();
  expect(JSON.parse(readFileSync(statePath(env), "utf8")).runId).toBe("other-run");
});
