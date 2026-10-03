import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentTree } from "./AgentTree";
import { SidebarFilters } from "./SidebarFilters";
import { EMPTY_FILTERS, FILTER_KEY, filterValue, matchesFilters, readFilters, sidebarSessions, useSidebarFilters } from "./sidebarFilterState";
import { IDLE_SETTLE_MS } from "./sessions";
import { SessionView } from "./types";

const session = (key: string, overrides: Partial<SessionView> = {}): SessionView => ({
  key, label: key, state: "busy", lastEventType: "SessionStart", startedAt: 0,
  updatedAt: 0, eventCount: 1, runtime: "codex", group: "Project", ...overrides,
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it("uses the row's settled state and never brings archived sessions back", () => {
  const s = session("settling", { idleSince: 1000 });
  const working = { ...EMPTY_FILTERS, status: ["busy"] };
  expect(matchesFilters(s, working, 1000 + IDLE_SETTLE_MS - 1)).toBe(true);
  expect(matchesFilters(s, working, 1000 + IDLE_SETTLE_MS)).toBe(false);
  expect(filterValue(s, "status", 1000 + IDLE_SETTLE_MS)).toBe("idle");
  for (const state of ["stopped", "interrupted", "terminated"] as const) {
    expect(filterValue(session(state, { state }), "status", 0)).toBe("stopped");
  }
  const archived = session("archived", { state: "archived" });
  expect(sidebarSessions([s, archived])).toEqual([s]);
  expect(matchesFilters(archived, EMPTY_FILTERS, 0)).toBe(false);
});

it("combines choices within a group with OR and different groups with AND", () => {
  const filters = { status: ["busy", "waiting"], runtime: ["codex"], location: ["remote"] };
  expect(matchesFilters(session("remote", { host: "build-host" }), filters, 0)).toBe(true);
  expect(matchesFilters(session("waiting", { state: "waiting", host: "build-host" }), filters, 0)).toBe(true);
  expect(matchesFilters(session("local"), filters, 0)).toBe(false);
  expect(matchesFilters(session("idle", { state: "idle", host: "build-host" }), filters, 0)).toBe(false);
  expect(matchesFilters(session("claude", { runtime: "claude-code", host: "build-host" }), filters, 0)).toBe(false);
});

it("validates persisted filters and reports a storage write that silently fails", () => {
  expect(readFilters('{"status":["busy","bogus","busy",3],"runtime":["custom"],"location":["moon"]}'))
    .toEqual({ status: ["busy"], runtime: ["custom"], location: [] });
  expect(readFilters("not json")).toEqual(EMPTY_FILTERS);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {});
  const { result } = renderHook(useSidebarFilters);
  act(() => result.current.toggle("status", "waiting"));
  expect(result.current.filters.status).toEqual(["waiting"]);
  expect(result.current.saveError).toBe(true);
});

it("restores choices on remount and clears their saved value", () => {
  const first = renderHook(useSidebarFilters);
  act(() => first.result.current.toggle("runtime", "codex"));
  first.unmount();
  const second = renderHook(useSidebarFilters);
  expect(second.result.current.filters.runtime).toEqual(["codex"]);
  act(() => second.result.current.clear());
  expect(readFilters(localStorage.getItem(FILTER_KEY))).toEqual(EMPTY_FILTERS);
});

it("counts facets using the other groups and exposes observed harnesses", () => {
  const sessions = [session("local"), session("remote", { host: "build", state: "waiting" }), session("custom", { runtime: "custom" })];
  const view = render(<SidebarFilters sessions={sessions} now={0} filters={{ ...EMPTY_FILTERS, runtime: ["codex"] }} onToggle={vi.fn()} onClear={vi.fn()} saveError={false} />);
  expect(screen.getByRole("button", { name: "Working 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Waiting 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "custom 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remote 1" })).toBeInTheDocument();
  view.rerender(<SidebarFilters sessions={[sessions[0]]} now={0} filters={EMPTY_FILTERS} onToggle={vi.fn()} onClear={vi.fn()} saveError={false} />);
  expect(screen.queryByRole("group", { name: "Location" })).not.toBeInTheDocument();
});

it("keeps folder expansion and selection when filters flatten and clear the tree", () => {
  const onOpen = vi.fn();
  const props = { sessions: [session("active"), session("resting", { state: "idle", group: "Other" })], now: 0,
    folders: ["Project", "Other", "Empty"], selectedKey: "active", onOpen,
    onFoldersChanged: vi.fn(), onSessionMoved: vi.fn(), onOpenGrid: vi.fn(), onNewSessionIn: vi.fn(),
    onOpenFolderInbox: vi.fn(), folderThemes: {}, onSetFolderTheme: vi.fn(), termMode: "dark" as const };
  const view = render(<AgentTree {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Expand Project" }));
  fireEvent.click(screen.getByRole("button", { name: "Idle 1" }));
  expect(screen.getByText("resting", { exact: true })).toBeInTheDocument();
  expect(view.container.querySelector(".rd-row-folder")).toHaveTextContent("Other");
  expect(screen.queryByText("active", { exact: true })).not.toBeInTheDocument();
  expect(onOpen).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("button", { name: "Idle 1" }), { key: "Escape" });
  expect(screen.getByRole("button", { name: "Collapse Project" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Expand Other" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Expand Empty" })).toBeInTheDocument();
  expect(view.container.querySelector(".rd-row.selected")).toHaveTextContent("active");
  expect(screen.getByRole("button", { name: "Working 1" })).toHaveFocus();
});
