import { afterEach, expect, it, vi } from "vitest";
import { bugReport, captureFiles } from "./bugReportData";
import { sessionRef } from "./hostTransport";
afterEach(() => { delete window.webkit; vi.unstubAllGlobals(); });
it("uses the selected remote for context, exact submission, and binary downloads", async () => {
  const responses = [{ items: [], recipient: "", limits: {} }, { status: "draft_prepared", sent: false }];
  const bridge = vi.fn().mockImplementation(() => Promise.resolve({ status: 200, body: JSON.stringify(responses.shift() ?? {}) }));
  window.webkit = { messageHandlers: { launchRequest: { postMessage: bridge } } };
  const session = sessionRef("remote", "session.id");
  await bugReport.context(session);
  expect(bridge.mock.calls[0][0].params.path).toBe("/bugreport/context?session_key=session.id");
  await bugReport.prepare(session, "Subject", "Exact body\r\n", [{ name: "a.bin", content_base64: "/wCA", size: 3, type: "application/octet-stream" }]);
  expect(bridge.mock.calls[1][0]).toMatchObject({ target: "remote", params: { path: "/bugreport/submit", body: JSON.stringify({ summary: "Subject", body: "Exact body\r\n", attachments: [{ name: "a.bin", content_base64: "/wCA" }] }) } });
  bridge.mockResolvedValue({ status: 200, base64: "/wCA", contentType: "application/zip" });
  const bundle = await bugReport.bundle(session, "/bugreport/bundles/" + "a".repeat(32));
  expect(new Uint8Array(await bundle.arrayBuffer())).toEqual(new Uint8Array([255, 0, 128]));
  await expect(bugReport.bundle(session, "https://example.test/report")).rejects.toThrow("Invalid report download link");
  expect(bridge).toHaveBeenCalledTimes(3);
});
it("rejects attachment budgets before reading or preparing a report", async () => {
  const limits = { attachments: 5, file_bytes: 5, total_bytes: 10, body_bytes: 65536 };
  await expect(captureFiles([new File(["123456"], "large.txt")], [], limits)).rejects.toThrow("Each file");
  await expect(captureFiles(Array.from({ length: 6 }, () => new File(["a"], "one.txt")), [], limits)).rejects.toThrow("at most");
  await expect(captureFiles(Array.from({ length: 3 }, () => new File(["12345"], "five.txt")), [], limits)).rejects.toThrow("total");
});
