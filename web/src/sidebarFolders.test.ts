import { expect, it } from "vitest";
import { sidebarFolders } from "./sidebarFolders";
import type { SessionView } from "./types";

it("offers empty catalog folders and remote ancestors without reviving stale local paths", () => {
  const sessions = [
    { key: "local", group: "Old name" },
    { key: "remote:dev:one", host: "dev", group: "Remote/Projects" },
    { key: "remote:dev:two", host: "dev", group: "Empty folder" },
  ] as SessionView[];
  expect(sidebarFolders(["New name", "Empty folder"], sessions)).toEqual([
    "New name", "Empty folder", "Remote", "Remote/Projects",
  ]);
});
