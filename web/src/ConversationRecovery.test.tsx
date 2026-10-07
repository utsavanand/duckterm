import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { ConversationIdentityNotice, ConversationRecoveryDialog, SessionConversationRecovery } from "./ConversationRecovery";
import { identityPresentation, type ConversationIdentity, type ConversationRecoveryService } from "./conversationRecoveryState";
const missing: ConversationIdentity = { status: "missing", source: "none", transcript: "unknown", hooks: { status: "missing", canInstall: true }, canAdopt: true, canResume: false };
const recorded: ConversationIdentity = { ...missing, status: "recorded", source: "adopted", canAdopt: false };
function service(): ConversationRecoveryService {
  return { identity: vi.fn().mockResolvedValue(missing), installHooks: vi.fn(), candidates: vi.fn().mockResolvedValue({ revision: "r1", candidates: [
    { handle: "new", label: "Monday conversation", firstPrompt: "Build navigation", lastPrompt: "Check links", modifiedAt: 1791300000000, available: true },
    { handle: "old", label: "Earlier conversation", firstPrompt: "Fix recovery", lastPrompt: "Review status", modifiedAt: 1791200000000, available: true },
  ] }), adopt: vi.fn().mockResolvedValue(recorded) };
}
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); }; });
afterEach(cleanup);
it("never turns configured hooks or an assigned identity into transcript readiness", () => {
  expect(identityPresentation({ ...missing, status: "pending", hooks: { status: "configured", canInstall: false } }).title).toBe("Resume not ready yet");
  expect(identityPresentation({ ...recorded, source: "assigned" }).detail).toContain("has not been verified");
  expect(identityPresentation({ ...recorded, status: "contested", transcript: "present", canResume: true }).title).toBe("Resume blocked");
  expect(identityPresentation({ ...recorded, transcript: "missing" }).detail).toContain("could not be found");
});
it("offers hooks separately from recovery and never offers adoption while running", () => {
  const props = { identity: missing, computer: "Build Mac", busy: false, onInstall: vi.fn(), onChoose: vi.fn() };
  const { rerender } = render(<ConversationIdentityNotice {...props} stopped={false} />);
  expect(screen.getByRole("button", { name: "Install hooks" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Choose a conversation" })).toBeNull();
  rerender(<ConversationIdentityNotice {...props} stopped />);
  expect(screen.getByRole("button", { name: "Choose a conversation" })).toBeVisible();
});
it("requires choice and review, preserves an older choice, submits only handle and revision", async () => {
  const api = service(), onAdopted = vi.fn(), onClose = vi.fn();
  render(<ConversationRecoveryDialog service={api} sessionName="ui-dev" computer="Build Mac" onClose={onClose} onAdopted={onAdopted} />);
  const old = await screen.findByRole("radio", { name: "Earlier conversation" });
  expect(screen.getAllByRole("radio").every(r => !(r as HTMLInputElement).checked)).toBe(true);
  expect(screen.getByRole("button", { name: "Review selection" })).toBeDisabled();
  fireEvent.click(old); fireEvent.click(screen.getByRole("button", { name: "Review selection" }));
  expect(api.adopt).not.toHaveBeenCalled();
  expect(screen.getByText("Attach Earlier conversation to ui-dev?")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Attach this conversation" }));
  await waitFor(() => expect(onAdopted).toHaveBeenCalledWith(recorded));
  expect(api.adopt).toHaveBeenCalledExactlyOnceWith("old", "r1");
  expect(onClose).toHaveBeenCalledOnce();
});
it("refreshes identity after an uncertain write and refuses a second adoption if the first succeeded", async () => {
  const api = service(), updated = vi.fn();
  vi.mocked(api.adopt).mockRejectedValue(new Error("connection lost"));
  render(<ConversationRecoveryDialog service={api} sessionName="ui-dev" computer="This Mac" onClose={vi.fn()} onAdopted={updated} />);
  fireEvent.click(await screen.findByRole("radio", { name: "Earlier conversation" }));
  fireEvent.click(screen.getByRole("button", { name: "Review selection" }));
  fireEvent.click(screen.getByRole("button", { name: "Attach this conversation" }));
  await screen.findByRole("alert");
  vi.mocked(api.identity).mockResolvedValue(recorded);
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() => expect(updated).toHaveBeenCalledWith(recorded));
  expect(api.candidates).toHaveBeenCalledOnce();
  expect(screen.queryByRole("radio")).toBeNull();
  expect(api.adopt).toHaveBeenCalledOnce();
});

// A poll started before Install must not overwrite the mutation result.
it("ignores a stale status response after installing hooks", async () => {
  const apiModule = await import("./api");
  const api = service();
  const configured = { ...missing, hooks: { status: "configured" as const, canInstall: false } };
  vi.mocked(api.installHooks).mockResolvedValue(configured);
  const spy = vi.spyOn(apiModule, "conversationRecoveryService").mockReturnValue(api);
  let poll!: () => void;
  const originalInterval = window.setInterval.bind(window);
  const timer = vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
    if (delay === 10000) { poll = callback as () => void; return originalInterval(() => {}, delay) as unknown as ReturnType<typeof setInterval>; }
    return originalInterval(callback, delay) as unknown as ReturnType<typeof setInterval>;
  });
  const updated = vi.fn();
  const session = { key: "orphan", label: "Orphan", runtime: "claude-code", conversationIdentity: { status: "missing", source: "none", assignable: false } } as import("./types").SessionView;
  try {
    render(<SessionConversationRecovery session={session} stopped onIdentity={updated} />);
    await screen.findByRole("button", { name: "Install hooks" });
    let resolvePoll!: (value: ConversationIdentity) => void;
    vi.mocked(api.identity).mockImplementationOnce(() => new Promise(resolve => { resolvePoll = resolve; })).mockResolvedValue(configured);
    act(() => poll());
    fireEvent.click(screen.getByRole("button", { name: "Install hooks" }));
    await waitFor(() => expect(updated).toHaveBeenLastCalledWith(configured));
    await act(async () => { resolvePoll(missing); });
    expect(updated).toHaveBeenLastCalledWith(configured);
    expect(screen.queryByRole("button", { name: "Install hooks" })).toBeNull();
  } finally { cleanup(); timer.mockRestore(); spy.mockRestore(); }
});
