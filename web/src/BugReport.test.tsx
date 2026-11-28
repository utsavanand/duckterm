import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BugReport } from "./BugReport";
import { bugReport } from "./bugReportData";
vi.mock("./bugReportData", async original => ({ ...await original<typeof import("./bugReportData")>(), bugReport: { context: vi.fn(), prepare: vi.fn(), bundle: vi.fn(), file: vi.fn() } }));
const context = { items: [{ id: "version", label: "Duckterm version", text: "0.4.93" }, { id: "model", label: "Recorded model", text: "gpt-6-astra" }], recipient: "support@example.test", limits: { attachments: 5, file_bytes: 5242880, total_bytes: 15728640, body_bytes: 65536 } };
const draft = { status: "draft_prepared" as const, sent: false as const, mailto_url: "mailto:support@example.test?body=exact", recipient: "support@example.test", download_url: "/bugreport/bundles/" + "a".repeat(32), notice: "Nothing sent" };
beforeEach(() => { vi.mocked(bugReport.context).mockResolvedValue(context); vi.mocked(bugReport.prepare).mockResolvedValue(draft); vi.mocked(bugReport.file).mockResolvedValue({ state: "filed", issue_number: 42 }); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
async function edit() {
  render(<BugReport session="test-session" onClose={vi.fn()} />);
  await screen.findByText("gpt-6-astra");
  fireEvent.change(screen.getByRole("textbox", { name: "Summary" }), { target: { value: "Copy failed" } });
  fireEvent.change(screen.getByRole("textbox", { name: "What happened?" }), { target: { value: "Selected text did not copy.\nKeep this exact newline." } });
}
it("sends only the reviewed text on explicit submission and shows a confirmed receipt", async () => {
  await edit();
  fireEvent.click(screen.getByRole("checkbox", { name: /Recorded model/ }));
  const reviewed = screen.getByLabelText("Complete report").textContent;
  expect(reviewed).not.toContain("gpt-6-astra");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send report" })); });
  expect(bugReport.prepare).toHaveBeenCalledWith("test-session", "Copy failed", reviewed, []);
  expect(bugReport.file).toHaveBeenCalledWith("test-session", draft.download_url);
  expect(screen.getByRole("heading", { name: "Report #42 filed privately" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Open mail draft" })).not.toBeInTheDocument();
});
it("offers the full ZIP when a mail link is too long and calls out unaddressed drafts", async () => {
  vi.mocked(bugReport.prepare).mockResolvedValue({ ...draft, recipient: "", mailto_url: null });
  vi.mocked(bugReport.file).mockResolvedValue({ state: "failed", message: "Reports are not configured yet." });
  await edit();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send report" })); });
  expect(screen.queryByRole("link", { name: "Open mail draft" })).not.toBeInTheDocument();
  expect(screen.getByText(/complete report/)).toBeVisible();
  expect(screen.getByText(/This draft has no recipient/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Download ZIP" })).toBeEnabled();
});
it("keeps the edited report after submission failure", async () => {
  vi.mocked(bugReport.prepare).mockRejectedValue(new Error("Remote host unavailable"));
  await edit();
  fireEvent.click(screen.getByRole("button", { name: "Remove all" }));
  const reviewed = screen.getByLabelText("Complete report").textContent;
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send report" })); });
  expect(screen.getByRole("alert")).toHaveTextContent("Remote host unavailable");
  expect(screen.getByLabelText("Complete report").textContent).toBe(reviewed);
  expect(screen.getByRole("textbox", { name: "Summary" })).toHaveValue("Copy failed");
  expect(screen.getByRole("checkbox", { name: /Recorded model/ })).not.toBeChecked();
});
it("includes server resume readiness in review and excludes it from submission when removed", async () => {
  vi.mocked(bugReport.context).mockResolvedValue({ ...context, items: [...context.items,
    { id: "resume-readiness", label: "Resume readiness", text: "Session 1: harness=codex; resume id=missing" },
  ] });
  await edit();
  expect(screen.getByLabelText("Complete report")).toHaveTextContent("resume id=missing");
  fireEvent.click(screen.getByRole("checkbox", { name: /Resume readiness/ }));
  const reviewed = screen.getByLabelText("Complete report").textContent;
  expect(reviewed).not.toContain("resume id=missing");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send report" })); });
  expect(bugReport.prepare).toHaveBeenCalledWith("test-session", "Copy failed", reviewed, []);
});

it("keeps ZIP/email preparation local without submitting", async () => {
  await edit();
  expect(bugReport.file).not.toHaveBeenCalled();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save ZIP / email" })); });
  expect(bugReport.file).not.toHaveBeenCalled();
  expect(screen.getByText(/Nothing has been sent/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Open mail draft" })).toHaveAttribute("href", draft.mailto_url);
});
it("retries an uncertain result using the same saved bundle without preparing again", async () => {
  vi.mocked(bugReport.file).mockRejectedValueOnce(new Error("Connection lost"))
    .mockResolvedValueOnce({ state: "filed", issue_number: 42 });
  await edit();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send report" })); });
  expect(screen.getByText(/Report delivery could not be confirmed/)).toBeVisible();
  expect(screen.queryByText(/Nothing has been sent/)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open mail draft" })).toBeVisible();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry report" })); });
  expect(bugReport.prepare).toHaveBeenCalledTimes(1);
  expect(vi.mocked(bugReport.file).mock.calls).toEqual([
    ["test-session", draft.download_url], ["test-session", draft.download_url],
  ]);
  expect(screen.getByRole("heading", { name: "Report #42 filed privately" })).toBeVisible();
});
it("prevents a second send or editing while delivery is in flight", async () => {
  let finish!: (value: { state: "filed"; issue_number: number }) => void;
  vi.mocked(bugReport.file).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await edit();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send report" })); });
  expect(screen.getByRole("button", { name: "Edit report" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Send report" }));
  expect(bugReport.file).toHaveBeenCalledTimes(1);
  await act(async () => { finish({ state: "filed", issue_number: 42 }); });
});
