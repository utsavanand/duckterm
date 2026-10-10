import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export function initializeRunState(env = process.env) {
  env.RD_TEST_RUN_ID ||= randomUUID();
  env.RD_TEST_STATE_FILE ||= join(tmpdir(), `rd-e2e-${env.RD_TEST_RUN_ID}.json`);
}

export function statePath(env = process.env): string {
  if (!env.RD_TEST_RUN_ID || !env.RD_TEST_STATE_FILE) {
    throw new Error("Missing isolated browser run context; load playwright.config.ts first");
  }
  return env.RD_TEST_STATE_FILE;
}

export interface RunState {
  runId: string;
  home: string;
  port: string;
  pid: number;
  tmuxSocket: string;
}

export function readOwnedState(env = process.env): RunState {
  const state: RunState = JSON.parse(readFileSync(statePath(env), "utf8"));
  if (state.runId !== env.RD_TEST_RUN_ID) {
    throw new Error("Browser state belongs to another run; refusing to use or clean it");
  }
  return state;
}

export function writeOwnedState(state: Omit<RunState, "runId">, env = process.env) {
  writeFileSync(statePath(env), JSON.stringify({ ...state, runId: env.RD_TEST_RUN_ID }), { flag: "wx" });
}
