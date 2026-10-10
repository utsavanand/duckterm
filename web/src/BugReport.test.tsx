import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BugReport } from "./BugReport";
import { bugReport } from "./bugReportData";
vi.mock("./bugReportData", async original => ({ ...await original<typeof import("./bugReportData")>(), bugReport: { context: vi.fn(), prepare: vi.fn(), bundle: vi.fn() } }));
const context = { items: [{ id: "version", label: "Duckterm version", text: "0.4.93" }, { id: "model", label: "Recorded model", text: "gpt-6-astra" }], recipient: "support@example.test", limits: { attachments: 5, file_bytes: 5242880, total_bytes: 15728640, body_bytes: 65536 } };
const draft = { status: "draft_prepared" as const, sent: false as const, mailto_url: "mailto:support@example.test?body=exact", recipient: "support@example.test", download_url: "/bugreport/bundles/" + "a".repeat(32), notice: "Nothing sent" };
beforeEach(() => { vi.mocked(bugReport.context).mockResolvedValue(context); vi.mocked(bugReport.prepare).mockResolvedValue(draft); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
async function edit() {
  render(<BugReport session="test-session" onClose={vi.fn()} />);
  await screen.findByText("gpt-6-astra");
  fireEvent.change(screen.getByRole("textbox", { name: "Summary" }), { target: { value: "Copy failed" } });
  fireEvent.change(screen.getByRole("textbox", { name: "What happened?" }), { target: { value: "Selected text did not copy.\nKeep this exact newline." } });
}
it("submits the exact visible text with removed context excluded and calls it a draft", async () => {
  await edit();
  fireEvent.click(screen.getByRole("checkbox", { name: /Recorded model/ }));
  const reviewed = screen.getByLabelText("Complete report").textContent;
  expect(reviewed).not.toContain("gpt-6-astra");
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Prepare mail draft" })); });
  expect(bugReport.prepare).toHaveBeenCalledWith("test-session", "Copy failed", reviewed, []);
  expect(screen.getByRole("heading", { name: "Draft prepared" })).toBeVisible();
  expect(screen.getByText(/Nothing has been sent/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Open mail draft" })).toHaveAttribute("href", draft.mailto_url);
});
it("offers the full ZIP when a mail link is too long and calls out unaddressed drafts", async () => {
  vi.mocked(bugReport.prepare).mockResolvedValue({ ...draft, recipient: "", mailto_url: null });
  await edit();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Prepare mail draft" })); });
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
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Prepare mail draft" })); });
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
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Prepare mail draft" })); });
  expect(bugReport.prepare).toHaveBeenCalledWith("test-session", "Copy failed", reviewed, []);
});
