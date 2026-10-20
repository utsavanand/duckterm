import type { SessionView } from "./types";

// Local folders come from the catalog. Stale session snapshots must not revive
// old paths after a rename; remote presentation groups also contribute folders.
export function sidebarFolders(saved: string[], sessions: SessionView[]): string[] {
  return [...new Set([...saved, ...sessions.flatMap(session => {
    if (!session.host) return [];
    const parts = session.group?.split("/") ?? [];
    return parts.map((_, i) => parts.slice(0, i + 1).join("/"));
  })])];
}
