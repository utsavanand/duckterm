import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, type FolderStats } from "./api";
import { FolderDetails } from "./FolderDetails";
import { edition, CODE, FULL, LITE } from "./test/editions";

vi.mock("./api", () => ({ authHeaders: () => ({}), api: { folderStats: vi.fn() } }));
const stats: FolderStats = {
  folder: "Team", updated_at: 1000, timezone: "UTC", sessions: { busy: 2, idle: 1, waiting: 1 },
  waiting: [{ key: "waiting", name: "Review session" }],
  periods: { "1": { tokens: 100, sent: 3, answered: 2 }, "7": { tokens: 500, sent: 8, answered: 6 } },
  artifacts: { by_kind: { report: 3 }, available: 2, removed: 1 },
};
beforeEach(() => {
  vi.mocked(api.folderStats).mockResolvedValue(stats);
  vi.stubGlobal("fetch", vi.fn(async (path: string) => Response.json(path === "/tasks" ? { tasks: [] } : {
    revision: "default", instances: ["folder-tasks", "folder-stats", "artifacts-by-kind"].map((type, position) => ({ id: type, type, position, slot: "folder", params: { folder: "Team" } })),
  })));
});
afterEach(() => { cleanup(); vi.resetAllMocks(); vi.unstubAllGlobals(); });

it.each([["Lite", LITE], ["Code", CODE], ["Full", FULL]] as const)("%s puts the existing factual widgets and manual actions in the Overview content", async (_name, features) => {
  const open = vi.fn(), message = vi.fn();
  render(<FolderDetails folder="Team" presentation="overview" onOpenSession={open} onMessage={message} />, { wrapper: edition(features) });
  expect(await screen.findByText("4 sessions")).toBeVisible();
  expect(screen.getByText("2 busy · 1 idle · 1 waiting")).toBeVisible();
  expect(screen.getByText(/2 available · 1 removed/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Close folder details" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "7 days" }));
  expect(screen.getByText("500")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Review session ↗" }));
  expect(open).toHaveBeenCalledExactlyOnceWith("waiting");
  fireEvent.click(screen.getByRole("button", { name: "Message folder" }));
  expect(message).toHaveBeenCalledOnce();
  expect(api.folderStats).toHaveBeenCalledExactlyOnceWith("Team");
  expect(vi.mocked(fetch).mock.calls.map(([path]) => path)).toEqual(expect.arrayContaining(["/layouts/folder%3ATeam", "/tasks"]));
});

it("makes no stats, layout or task requests without an advertised overview capability", async () => {
  render(<FolderDetails folder="Team" presentation="overview" />);
  await act(async () => {});
  expect(api.folderStats).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByRole("complementary")).toBeNull();
});
