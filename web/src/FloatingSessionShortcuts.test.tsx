import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FloatingSessionShortcuts } from "./FloatingSessionShortcuts";
import { sessionRef } from "./hostTransport";
import { SHORTCUT_PREFERENCES_KEY } from "./shortcutPreferences";
import type { SessionView } from "./types";

const session = (key: string, extra: Partial<SessionView> = {}) => ({ key, label: key, state: "idle", ...extra } as SessionView);
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

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

it("keeps colors and order tied to host-qualified identity across rename, reload and session switching", () => {
  const local = session("same", { label: "Agent", group: "Local", pinned: true });
  const remote = session(sessionRef("cloud", "same"), { label: "Agent", group: "Cloud", pinned: true });
  const onOpen = vi.fn(), onToggle = vi.fn();
  const props = { sessions: [local, remote], selected: local, onOpen, onToggle };
  const view = render(<FloatingSessionShortcuts {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Color for Cloud / Agent: None" }));
  fireEvent.click(screen.getByRole("button", { name: "Red" }));
  expect(screen.getByRole("button", { name: "Color for Local / Agent: None" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Color for Cloud / Agent: Red" })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("button", { name: "Reorder Cloud / Agent" }), { key: "ArrowUp", altKey: true });
  fireEvent.change(screen.getByRole("combobox", { name: "Shortcut display" }), { target: { value: "expanded" } });
  expect(screen.getByRole("navigation")).toHaveClass("always-expanded");
  expect(onOpen).not.toHaveBeenCalled(); expect(onToggle).not.toHaveBeenCalled();
  view.unmount();
  render(<FloatingSessionShortcuts {...props} sessions={[local, { ...remote, label: "Renamed", group: "Moved" }]} selected={remote} />);
  expect(screen.getAllByRole("button", { name: /Open pinned/ })[0]).toHaveAccessibleName("Open pinned Renamed");
  expect(screen.getByRole("button", { name: "Color for Moved / Renamed: Red" })).toBeInTheDocument();
  expect(screen.getByRole("combobox")).toHaveValue("expanded");
  fireEvent.click(screen.getByRole("button", { name: "Color for Moved / Renamed: Red" }));
  fireEvent.click(screen.getByRole("button", { name: "None" }));
  expect(screen.getByRole("button", { name: "Color for Moved / Renamed: None" })).toBeInTheDocument();
});

it("keeps reordering on the grip without a move menu or session changes", () => {
  const a = session("a", { pinned: true }), b = session("b", { pinned: true });
  const onOpen = vi.fn(), onToggle = vi.fn();
  render(<FloatingSessionShortcuts sessions={[a, b]} selected={a} onOpen={onOpen} onToggle={onToggle} />);
  const grip = screen.getByRole("button", { name: "Reorder Ungrouped / a" });
  grip.focus(); fireEvent.click(grip);
  expect(grip).toHaveAttribute("draggable", "false");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.keyDown(grip, { key: "ArrowUp", altKey: true });
  expect(screen.getAllByRole("button", { name: /Open pinned/ })[0]).toHaveAttribute("aria-label", "Open pinned a");
  fireEvent.keyDown(grip, { key: "ArrowDown", altKey: true });
  expect(screen.getAllByRole("button", { name: /Open pinned/ }).map(e => e.getAttribute("aria-label"))).toEqual(["Open pinned b", "Open pinned a"]);
  fireEvent.keyDown(grip, { key: "ArrowDown", altKey: true });
  expect(grip).toHaveFocus();
  expect(screen.getByRole("status")).toHaveTextContent("Ungrouped / a, position 2 of 2");
  expect(onOpen).not.toHaveBeenCalled(); expect(onToggle).not.toHaveBeenCalled();
});

it("recovers malformed stored preferences, follows another window and reports a failed save", () => {
  localStorage.setItem(SHORTCUT_PREFERENCES_KEY, '{"display":"wrong","colors":{"a":"not-a-color"},"order":[null,3]}');
  const a = session("a", { pinned: true });
  render(<FloatingSessionShortcuts sessions={[a]} selected={a} onOpen={vi.fn()} onToggle={vi.fn()} />);
  expect(screen.getByRole("combobox")).toHaveValue("hover");
  expect(screen.getByRole("button", { name: "Color for Ungrouped / a: None" })).toBeInTheDocument();
  localStorage.setItem(SHORTCUT_PREFERENCES_KEY, JSON.stringify({ display: "expanded", colors: { a: "Yellow" }, order: [] }));
  fireEvent(window, new StorageEvent("storage", { key: SHORTCUT_PREFERENCES_KEY }));
  expect(screen.getByRole("combobox")).toHaveValue("expanded");
  expect(screen.getByRole("button", { name: "Color for Ungrouped / a: Yellow" })).toBeInTheDocument();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage full"); });
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "hover" } });
  expect(screen.getByRole("alert")).toHaveTextContent("could not be saved");
  expect(screen.getByRole("combobox")).toHaveValue("hover");
});
