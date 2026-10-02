import { api, TowerInsights } from "./api";
export type StreamState = { status: "loading" | "unavailable"; message?: string } | { status: "ready"; value: unknown };
const LOADING: StreamState = { status: "loading" };
const ABSENT: StreamState = { status: "unavailable", message: "This server does not provide this data." };
const INSIGHTS = ["tokens", "mail", "backup", "remote"];

/** Declared readers share the existing /control-tower cadence. No readers, no timer. */
export class WidgetStreams {
  private values = new Map<string, StreamState>();
  private listeners = new Map<string, Set<() => void>>();
  private timer?: ReturnType<typeof setTimeout>;
  private generation = 0;
  private polling = false;
  constructor() { for (const name of INSIGHTS) this.values.set(name, LOADING); }
  read(name: string): StreamState { return this.values.get(name) ?? ABSENT; }
  emit(name: string, value: StreamState) { this.values.set(name, value); this.listeners.get(name)?.forEach(fn => fn()); }
  subscribe(name: string, listener: () => void): () => void {
    const callbacks = this.listeners.get(name) ?? new Set(); callbacks.add(listener); this.listeners.set(name, callbacks);
    if (INSIGHTS.includes(name) && !this.polling) { this.polling = true; void this.refresh(++this.generation); }
    return () => {
      callbacks.delete(listener);
      if (!INSIGHTS.some(key => this.listeners.get(key)?.size)) { this.polling = false; this.generation++; clearTimeout(this.timer); }
    };
  }
  private async refresh(generation: number) {
    try {
      const data = await api.controlTower();
      if (this.generation !== generation) return;
      for (const name of INSIGHTS) {
        const value = data[name as keyof TowerInsights];
        this.emit(name, value == null || (name === "remote" && !data.remote?.available)
          ? ABSENT : { status: "ready", value });
      }
    } catch (e) {
      if (this.generation === generation) for (const name of INSIGHTS) this.emit(name, { status: "unavailable", message: (e as Error).message });
    } finally {
      if (this.generation === generation && this.polling) this.timer = setTimeout(() => void this.refresh(generation), 60_000);
    }
  }
}
