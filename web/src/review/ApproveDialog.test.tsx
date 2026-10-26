import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api, type ApprovalRecord, type ApprovalState } from "../api";
import { ApproveDialog, approvalLabel } from "./ApproveDialog";

vi.mock("../api", () => ({ api: { approvalState: vi.fn(), approvalChallenge: vi.fn(), approvalSubmit: vi.fn(), approvalKey: vi.fn() } }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  delete window.__rubbertermDesktop;
  delete window.webkit;
});

const head = "2222222bbbb2222222bbbb2222222bbbb2222222";
const state: ApprovalState = {
  ready: true, head_sha: head, record: null,
  checks: [
    { name: "Pull request open", ok: true, note: "#312" },
    { name: "CI green on this head", ok: true, note: "8/8 passed" },
    { name: "Release session", ok: true, note: "release-dev in Team" },
  ],
  flags: [{ name: "Security or credential path", hits: ["src/duckterm/review/approvals.py"] }],
  release: { key: "rel", name: "release-dev", folder: "Team" },
  author: { key: "dev", name: "pr-review-platform", folder: "Team/features" },
  key: { enrolled: true, fingerprint: "ab12 cd34 ef56 7890", enrolled_at: 1, public_key: "PUBKEY", path: "/h/.duckterm/approval-key.pem", invalid: false },
};
const record: ApprovalRecord = {
  pr: 312, head_sha: head, task_id: "t1", message_id: "m1", approved_at: 2, fingerprint: "ab12 cd34 ef56 7890",
  release: { key: "rel", name: "release-dev", folder: "Team" }, task: { status: "in_progress", note: "approved with Touch ID", updated_at: 2 },
};

function inMacApp(sign: (payload: string) => string) {
  const postMessage = vi.fn(async (message: unknown) => {
    const { operation, payload } = message as { operation: string; payload?: string };
    return operation === "public-key" ? "PUBKEY" : sign(payload ?? "");
  });
  window.__rubbertermDesktop = { currentTarget: "local", targets: [], canApprove: true };
  window.webkit = { messageHandlers: { approval: { postMessage } } };
  return postMessage;
}

it("in a browser, shows the conditions and what the release touches but cannot approve", async () => {
  vi.mocked(api.approvalState).mockResolvedValue(state);
  render(<ApproveDialog repo="/r" target="312" prNumber={312} onClose={vi.fn()} onApproved={vi.fn()} />);
  const checks = await screen.findByRole("list", { name: "Approval conditions" });
  expect(within(checks).getAllByRole("listitem").map(li => li.textContent)).toEqual(["✓ Pull request open #312", "✓ CI green on this head 8/8 passed", "✓ Release session release-dev in Team"]);
  expect(screen.getByRole("note", { name: "Release touches" })).toHaveTextContent("Security or credential path: src/duckterm/review/approvals.py");
  expect(await screen.findByText(/works in the DuckTerm Mac app/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve with Touch ID" })).toBeDisabled();
  expect(api.approvalKey).not.toHaveBeenCalled();
});

it("in the Mac app, binds the key, signs the server's challenge and hands it back", async () => {
  vi.mocked(api.approvalState).mockResolvedValue(state);
  vi.mocked(api.approvalKey).mockResolvedValue(state.key);
  vi.mocked(api.approvalChallenge).mockResolvedValue({ payload: "duckterm-approve v1\n…" });
  vi.mocked(api.approvalSubmit).mockResolvedValue(record);
  const bridge = inMacApp(payload => payload.startsWith("duckterm-approve v1") ? "SIG" : "wrong");
  const onApproved = vi.fn();
  render(<ApproveDialog repo="/r" target="312" prNumber={312} onClose={vi.fn()} onApproved={onApproved} />);
  const approve = screen.getByRole("button", { name: "Approve with Touch ID" });
  await vi.waitFor(() => expect(approve).toBeEnabled());
  fireEvent.click(approve);
  expect(await screen.findByRole("status")).toHaveTextContent("Approved · release-dev: approved with Touch ID");
  expect(bridge).toHaveBeenCalledWith({ operation: "sign", payload: "duckterm-approve v1\n…" });
  expect(api.approvalSubmit).toHaveBeenCalledWith("/r", "312", { payload: "duckterm-approve v1\n…", signature: "SIG" });
  expect(onApproved).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Approve with Touch ID" })).toBeNull();
});

it("stays disabled on a failing condition and names a key that isn't this Mac's", async () => {
  vi.mocked(api.approvalState).mockResolvedValue({ ...state, ready: false, checks: [{ name: "CI green on this head", ok: false, note: "2 failing" }] });
  vi.mocked(api.approvalKey).mockResolvedValue({ ...state.key, public_key: "SOMEONE-ELSES", fingerprint: "dead beef 0000 1111" });
  inMacApp(() => "never");
  render(<ApproveDialog repo="/r" target="312" prNumber={312} onClose={vi.fn()} onApproved={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("isn't this Mac's Touch ID key (dead beef 0000 1111). If you didn't bind it, delete /h/.duckterm/approval-key.pem");
  expect(screen.getByText("CI green on this head").closest("li")).toHaveClass("is-fail");
  expect(screen.getByRole("button", { name: "Approve with Touch ID" })).toBeDisabled();
  expect(api.approvalChallenge).not.toHaveBeenCalled();
});

it("says when no key is bound yet instead of binding one from the page", async () => {
  vi.mocked(api.approvalState).mockResolvedValue(state);
  vi.mocked(api.approvalKey).mockResolvedValue({ ...state.key, enrolled: false, public_key: null, fingerprint: null });
  inMacApp(() => "never");
  render(<ApproveDialog repo="/r" target="312" prNumber={312} onClose={vi.fn()} onApproved={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("No Touch ID key is bound yet. Quit and reopen DuckTerm");
  expect(screen.getByRole("button", { name: "Approve with Touch ID" })).toBeDisabled();
});

it("labels each step the release session reports", () => {
  expect(approvalLabel({ ...record, task: null })).toBe("Approved · handed to release-dev");
  expect(approvalLabel({ ...record, task: { status: "in_progress", note: "releasing v0.4.175", updated_at: 3 } })).toBe("Approved · release-dev: releasing v0.4.175");
  expect(approvalLabel({ ...record, task: { status: "done", note: "installed v0.4.175", updated_at: 4 } })).toBe("Released · installed v0.4.175");
  expect(approvalLabel({ ...record, task: { status: "parked", note: "Head changed since approval", updated_at: 5 } })).toBe("Release stopped: Head changed since approval");
});
