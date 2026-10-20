import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FloatingSessionShortcuts } from "./FloatingSessionShortcuts";
import { sessionRef } from "./hostTransport";
import type { SessionView } from "./types";

const session = (key: string, extra: Partial<SessionView> = {}) => ({ key, label: key, state: "idle", ...extra } as SessionView);
afterEach(cleanup);

it("keeps same-named remote sessions distinct and applies pin limits per host", () => {
  const local = session("same", { label: "Agent", pinned: true });
  const remote = session(sessionRef("cloud", "same"), { label: "Agent", pinned: true, hostLabel: "Cloud" });
  const next = session(sessionRef("cloud", "next"), { label: "Remote next" });
  const unavailable = session(sessionRef("offline", "one"), { label: "Offline", hostOffline: true });
  const sessions = [local, ...Array.from({ length: 9 }, (_, i) => session(`local-${i}`, { pinned: true })), remote, next, unavailable, session("eleventh")];
  const onOpen = vi.fn(), onToggle = vi.fn().mockResolvedValue(undefined);
  render(<FloatingSessionShortcuts sessions={sessions} selected={local} onOpen={onOpen} onToggle={onToggle} />);
  fireEvent.click(screen.getAllByRole("button", { name: "Open pinned Agent" })[1]);
  expect(onOpen).toHaveBeenCalledWith(remote.key);
  fireEvent.click(screen.getByRole("button", { name: "Choose sessions to pin" }));
  const picker = within(screen.getByRole("dialog"));
  expect(picker.getByRole("button", { name: "Pin eleventh in picker" })).toBeDisabled();
  expect(picker.getByRole("button", { name: "Pin Remote next in picker" })).toBeEnabled();
  expect(picker.getByRole("button", { name: "Pin Offline in picker" })).toBeDisabled();
  fireEvent.click(picker.getByRole("button", { name: "Pin Remote next in picker" }));
  expect(onToggle).toHaveBeenCalledWith(next);
});

it("prevents duplicate pending writes, retains pins on failure, and unpinning never selects another session", async () => {
  const current = session("current", { pinned: true });
  let reject!: (error: Error) => void;
  const onToggle = vi.fn(() => new Promise<void>((_resolve, fail) => { reject = fail; }));
  const onOpen = vi.fn();
  render(<FloatingSessionShortcuts sessions={[current]} selected={current} onToggle={onToggle} onOpen={onOpen} />);
  const unpin = screen.getByRole("button", { name: "Unpin current from shortcuts" });
  fireEvent.click(unpin); fireEvent.click(unpin);
  expect(onToggle).toHaveBeenCalledTimes(1);
  expect(unpin).toBeDisabled();
  await act(async () => reject(new Error("Host unavailable")));
  expect(screen.getByRole("alert")).toHaveTextContent("Host unavailable");
  expect(screen.getByRole("button", { name: "Open pinned current" })).toBeInTheDocument();
  expect(onOpen).not.toHaveBeenCalled();
});

it("searches folder/host names, restores focus on Escape, and excludes unpinned archived sessions", () => {
  const current = session("current");
  render(<FloatingSessionShortcuts sessions={[current, session("hidden", { state: "archived" }), session("kept", { state: "archived", pinned: true, group: "Project", hostLabel: "Cloud" })]} selected={current} onOpen={vi.fn()} onToggle={vi.fn()} />);
  const add = screen.getByRole("button", { name: "Choose sessions to pin" });
  fireEvent.click(add);
  expect(screen.getByRole("searchbox")).toHaveFocus();
  expect(screen.queryByRole("button", { name: "Pin hidden in picker" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Project Cloud" } });
  expect(screen.getByText("No sessions found.")).toBeInTheDocument();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Cloud" } });
  expect(screen.getByRole("button", { name: "Unpin kept in picker" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Pin current in picker" })).not.toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(add).toHaveFocus();
});
