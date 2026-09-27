import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { useFolders } from "./useFolders";

vi.mock("./api", () => ({ api: { folders: vi.fn() } }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

it("keeps empty-folder rows on connection failure and refreshes on return", async () => {
  vi.useFakeTimers();
  const fetchFolders = vi.mocked(api.folders);
  fetchFolders.mockResolvedValueOnce({ folders: ["Empty"] });
  const { result } = renderHook(useFolders);
  await act(async () => {});
  expect(result.current.folders).toEqual(["Empty"]);
  fetchFolders.mockRejectedValueOnce(new Error("offline"));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(result.current.folders).toEqual(["Empty"]);
  fetchFolders.mockResolvedValueOnce({ folders: ["Empty", "Empty/Child"] });
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(result.current.folders).toEqual(["Empty", "Empty/Child"]);
});

it("ignores an older response that arrives after a completed folder edit", async () => {
  let resolveOld!: (value: { folders: string[] }) => void;
  vi.mocked(api.folders).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
  const { result } = renderHook(useFolders);
  vi.mocked(api.folders).mockResolvedValueOnce({ folders: ["New"] });
  await act(async () => { await result.current.refreshFolders(); });
  await act(async () => { resolveOld({ folders: ["Old"] }); });
  expect(result.current.folders).toEqual(["New"]);
});
