import type { KitEntry } from "./api";

// The one body Create team sends, from the MetaHarness card and from New ▾ ›
// New team alike: the installed repository when there is one (the procedure
// is copied as installed), else the added MetaHarness's path.
export function teamRequest(kit: KitEntry, team: string, name: string, project?: string | null, sessions = true) {
  return { project: project ?? kit.installed_in[0]?.project ?? null, kit_path: kit.path, kit: kit.name, team, name, sessions };
}
