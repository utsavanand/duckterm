// @vitest-environment node
import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, test } from "vitest";
import { waitForOwnedServer } from "../e2e/server-readiness";

const homes: string[] = [];
const children: ChildProcess[] = [];
const servers: Server[] = [];
afterEach(async () => {
  for (const child of children.splice(0)) {
    if (child.exitCode === null && child.signalCode === null) {
      await new Promise<void>((resolve) => { child.once("exit", () => resolve()); child.kill(); });
    }
  }
  for (const server of servers.splice(0)) await new Promise<void>((resolve) => { server.close(() => resolve()); server.closeAllConnections(); });
  for (const home of homes.splice(0)) rmSync(home, { recursive: true, force: true });
});
function fixture(script = "setInterval(() => {}, 1000)") {
  const home = mkdtempSync(join(tmpdir(), "readiness-test-"));
  homes.push(home);
  writeFileSync(join(home, "token"), "private-test-token");
  const child = spawn(process.execPath, ["-e", script], { stdio: "ignore" });
  children.push(child);
  return { home, child };
}
async function listener(token: string) {
  const server = createServer((_req, res) => res.end(`<head><meta name="duckterm-token" content="${token}"></head>`));
  servers.push(server);
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Expected TCP listener");
  return `http://127.0.0.1:${address.port}`;
}
test("rejects another QA server despite HTTP 200 and leaves it running", async () => {
  const { child, home } = fixture();
  const base = await listener("foreign-token");
  await expect(waitForOwnedServer(child, home, base, 150)).rejects.toThrow("identity was not verified");
  expect(await (await fetch(base)).text()).toContain("foreign-token");
});
test("accepts matching private identity while child is alive", async () => {
  const { child, home } = fixture();
  await expect(waitForOwnedServer(child, home, await listener("private-test-token"))).resolves.toBeUndefined();
});
test("fails on child exit instead of accepting the occupied port", async () => {
  const { child, home } = fixture("process.exit(7)");
  await expect(waitForOwnedServer(child, home, await listener("foreign-token"))).rejects.toThrow("exited before readiness (7)");
});
test("missing token times out instead of starting tests", async () => {
  const { child, home } = fixture();
  rmSync(join(home, "token"));
  await expect(waitForOwnedServer(child, home, await listener("private-test-token"), 100)).rejects.toThrow("timed out");
});
test("a dead child cannot pass even when the token matches", async () => {
  const { child, home } = fixture("process.exit(0)");
  await new Promise<void>((resolve) => child.once("exit", () => resolve()));
  await expect(waitForOwnedServer(child, home, await listener("private-test-token"))).rejects.toThrow("exited before readiness (0)");
});
