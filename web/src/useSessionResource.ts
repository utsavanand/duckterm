import { useEffect, useState } from "react";
import { routedFetch } from "./hostTransport";

// Memory-only, keyed by the qualified session URL (including its remote host).
// Retain recent replies between tab mounts without retaining unbounded transcripts.
export interface TranscriptStatus { status?: string; reason?: string; }
interface Snapshot { data: unknown[]; transcript?: TranscriptStatus; signature: string; bytes: number; }
const snapshots = new Map<string, Snapshot>();
const pending = new Map<string, Promise<Snapshot>>();
class UnavailableResource extends Error {}
const MAX_BYTES = 32 * 1024 * 1024;
let retainedBytes = 0;
function forget(path: string) {
  retainedBytes -= snapshots.get(path)?.bytes ?? 0;
  snapshots.delete(path);
}
function remember(path: string, snapshot: Snapshot) {
  forget(path);
  if (snapshot.bytes > MAX_BYTES) return;
  snapshots.set(path, snapshot); retainedBytes += snapshot.bytes;
  while (snapshots.size > 16 || retainedBytes > MAX_BYTES) forget(snapshots.keys().next().value!);
}
function read(path: string, field: string): Promise<Snapshot> {
  const existing = pending.get(path);
  if (existing) return existing;
  const request = (async () => {
    const response = await routedFetch(path, { cache: "no-store" });
    if (response.ok === false) {
      if (response.status === 404 || response.status === 401 || response.status === 403) { forget(path); throw new UnavailableResource(); }
      throw new Error("Could not load session content");
    }
    const body = await response.json();
    if (!Array.isArray(body?.[field])) throw new Error("Invalid session content");
    const data: unknown[] = body[field];
    const metadata = field === "messages" ? body.transcript : undefined;
    const transcript: TranscriptStatus | undefined = metadata && typeof metadata === "object" ? {
      status: typeof metadata.status === "string" ? metadata.status : undefined,
      reason: typeof metadata.reason === "string" ? metadata.reason : undefined,
    } : undefined;
    // Metadata-only changes (including disappearance after recovery) must refresh
    // an empty transcript too. Store the list and its explanation atomically.
    const signature = JSON.stringify({ data, transcript });
    const previous = snapshots.get(path);
    const snapshot = previous?.signature === signature ? previous : { data, transcript, signature, bytes: signature.length * 4 };
    remember(path, snapshot);
    return snapshot;
  })();
  pending.set(path, request);
  void request.finally(() => { if (pending.get(path) === request) pending.delete(path); }).catch(() => undefined);
  return request;
}

export function useSessionResource<T>(key: string, field: "messages" | "annotations", active: boolean, revision = 0) {
  const path = `/sessions/${encodeURIComponent(key)}/${field}`;
  const [state, setState] = useState<{ path: string; snapshot?: Snapshot; error: boolean }>(() => ({ path, snapshot: snapshots.get(path), error: false }));
  const current = state.path === path ? state : { path, snapshot: snapshots.get(path), error: false };
  useEffect(() => {
    let live = true, running = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const visible = () => active && document.visibilityState !== "hidden";
    setState(previous => previous.path === path ? previous : { path, snapshot: snapshots.get(path), error: false });
    async function refresh() {
      if (!live || !visible() || running) return;
      running = true;
      try {
        // A saved annotation invalidates an older in-flight read. Wait for it,
        // then fetch again so it cannot overwrite the owner's new comment.
        if (revision && pending.has(path)) await pending.get(path)?.catch(() => undefined);
        if (!live || !visible()) return;
        const snapshot = await read(path, field);
        if (live && visible()) setState(previous => previous.path === path && previous.snapshot === snapshot && !previous.error ? previous : { path, snapshot, error: false });
      } catch (cause) {
        if (live && visible()) setState(previous => ({ path, snapshot: cause instanceof UnavailableResource ? undefined : snapshots.get(path) ?? (previous.path === path ? previous.snapshot : undefined), error: true }));
      } finally {
        running = false;
        if (live && visible()) timer = setTimeout(() => { void refresh(); }, 3000);
      }
    }
    const visibility = () => { clearTimeout(timer); if (visible()) void refresh(); };
    document.addEventListener("visibilitychange", visibility);
    void refresh();
    return () => { live = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [path, field, active, revision]);
  return { items: current.snapshot?.data as T[] | undefined, transcript: current.snapshot?.transcript, error: current.error };
}
