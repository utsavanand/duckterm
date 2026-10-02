import { ComponentType, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { authHeaders } from "./api";
import { StreamState, WidgetStreams } from "./widgetStreams";
import "./widgets.css";
export type WidgetSlot = "oracle" | "folder";
export interface WidgetInstance { id: string; type: string; slot: WidgetSlot; position: number; params: Record<string, string>; }
export interface WidgetType { type: string; title: string; slots: WidgetSlot[]; streams: string[]; render: ComponentType<{ streams: Record<string, StreamState>; params: Record<string, string> }>; }
interface Layout { instances: WidgetInstance[]; revision: string; }
export function defaultLayout(slot: WidgetSlot, registry: WidgetType[], folder?: string): WidgetInstance[] {
  return registry.filter(type => type.slots.includes(slot)).map((type, position): WidgetInstance => ({ id: type.type, type: type.type, slot, position, params: folder ? { folder } : {} }));
}
async function request(surface: string, body?: Layout): Promise<Layout> {
  const response = await fetch(`/layouts/${encodeURIComponent(surface)}`, { method: body ? "PUT" : "GET", cache: "no-store", headers: authHeaders({ "Content-Type": "application/json" }), ...(body ? { body: JSON.stringify(body) } : {}) });
  const value = await response.json(); if (!response.ok) throw new Error(value.error || `Layout request failed (${response.status})`); return value;
}
function WidgetContent({ type, instance, source }: { type: WidgetType; instance: WidgetInstance; source: WidgetStreams }) {
  const view = useMemo(() => {
    let snapshot = Object.fromEntries(type.streams.map(name => [name, source.read(name)]));
    return {
      get: () => snapshot,
      subscribe: (notify: () => void) => {
        const changed = () => { snapshot = Object.fromEntries(type.streams.map(name => [name, source.read(name)])); notify(); };
        const cleanups = type.streams.map(name => source.subscribe(name, changed)); changed();
        return () => cleanups.forEach(stop => stop());
      },
    };
  }, [source, type]);
  const streams = useSyncExternalStore(view.subscribe, view.get);
  const missing = type.streams.map(name => streams[name]).find(state => state.status === "unavailable");
  if (missing) return <div className="rd-widget-unavailable"><strong>Unavailable</strong><p>{missing.status === "unavailable" && missing.message}</p></div>;
  if (type.streams.some(name => streams[name].status === "loading")) return <div className="rd-widget-unavailable" role="status">Loading…</div>;
  const Render = type.render;
  return <Render streams={streams} params={instance.params} />;
}
export function Widgets({ surface, slot, registry, source }: { surface: string; slot: WidgetSlot; registry: WidgetType[]; source: WidgetStreams }) {
  const defaults = useMemo(() => defaultLayout(slot, registry, slot === "folder" ? surface.slice(7) : undefined), [slot, registry, surface]);
  const [layout, setLayout] = useState<Layout>({ instances: defaults, revision: "default" });
  const [loading, setLoading] = useState(true), [saving, setSaving] = useState(false), [error, setError] = useState("");
  const [add, setAdd] = useState(false), [menu, setMenu] = useState<string | null>(null), [drag, setDrag] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const root = useRef<HTMLElement>(null), active = useRef(true), saveLock = useRef(false);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { const value = await request(surface); if (active.current) setLayout(value); }
    catch (e) { if (active.current) setError((e as Error).message); }
    finally { if (active.current) setLoading(false); }
  }, [surface]);
  useEffect(() => { active.current = true; void load(); return () => { active.current = false; }; }, [load]);
  useEffect(() => {
    if (!add && !menu) return;
    const close = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault(); event.stopImmediatePropagation();
      const title = registry.find(type => type.type === layout.instances.find(item => item.id === menu)?.type)?.title;
      const target = [...(root.current?.querySelectorAll<HTMLButtonElement>("button") ?? [])].find(button => add ? button.textContent === "Add widget" : button.getAttribute("aria-label") === `Options for ${title}`);
      setAdd(false); setMenu(null); target?.focus();
    };
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) { setAdd(false); setMenu(null); } };
    window.addEventListener("keydown", close, true); window.addEventListener("pointerdown", outside);
    return () => { window.removeEventListener("keydown", close, true); window.removeEventListener("pointerdown", outside); };
  }, [add, menu, registry, layout.instances]);
  async function save(instances: WidgetInstance[]) {
    if (saveLock.current || loading) return;
    const focused = document.activeElement as HTMLElement | null;
    saveLock.current = true; setSaving(true); setError("");
    try {
      const next = await request(surface, { revision: layout.revision, instances: instances.map((item, position) => ({ ...item, position })) });
      if (active.current) { setLayout(next); setMenu(null); setAnnouncement("Widget layout saved"); }
    } catch (e) { if (active.current) setError((e as Error).message); }
    finally { saveLock.current = false; if (active.current) { setSaving(false); requestAnimationFrame(() => { if (active.current && focused?.isConnected) focused.focus(); }); } }
  }
  function move(id: string, target: number) {
    const next = [...layout.instances], index = next.findIndex(item => item.id === id);
    if (index < 0 || target < 0 || target >= next.length) return;
    const [item] = next.splice(index, 1); next.splice(target, 0, item); void save(next);
  }
  const disabled = loading || saving;
  return <section ref={root} className="rd-widgets" aria-label={slot === "oracle" ? "Fleet insights" : "Folder widgets"} onKeyDown={event => {
    if (event.key === "Escape" && (add || menu)) { event.stopPropagation(); setAdd(false); setMenu(null); }
  }}>
    <header className="rd-widgets-heading"><h2>Widgets</h2><div><button className="rd-btn rd-btn-ghost rd-btn-sm" disabled={disabled || !!error} onClick={() => void save(defaults)}>Reset</button><button className="rd-btn rd-btn-ghost rd-btn-sm" aria-expanded={add} disabled={disabled || !!error} onClick={() => setAdd(!add)}>Add widget</button></div>
      {add && <div className="rd-widget-add" role="group" aria-label="Visible widgets">{registry.filter(type => type.slots.includes(slot)).map(type => <label key={type.type}><input type="checkbox" disabled={disabled} checked={layout.instances.some(item => item.type === type.type)} onChange={event => void save(event.target.checked ? [...layout.instances, defaults.find(item => item.type === type.type)!] : layout.instances.filter(item => item.type !== type.type))} />{type.title}</label>)}<p>Built-in {slot === "oracle" ? "Oracle" : "folder"} widgets</p></div>}
    </header>
    {error && <p className="rd-widget-error" role="alert">{error} <button className="rd-btn rd-btn-ghost rd-btn-sm" onClick={() => void load()}>Reload layout</button></p>}
    <div className="rd-widget-grid">{layout.instances.map((instance, index) => {
      const type = registry.find(type => type.type === instance.type && type.slots.includes(slot));
      if (!type) return null;
      return <article key={instance.id} className={`rd-widget${drag === instance.id ? " dragging" : ""}`} aria-label={type.title} onDragOver={event => { if (drag) event.preventDefault(); }} onDrop={event => { event.preventDefault(); if (drag) move(drag, index); setDrag(null); }}>
        <header><h3>{type.title}</h3><div><button disabled={disabled || !!error} className="rd-widget-handle" draggable={!disabled} aria-label={`Reorder ${type.title}`} title="Drag, or use Arrow Up/Down to reorder" onDragStart={event => { setDrag(instance.id); event.dataTransfer.setData("text/plain", instance.id); }} onDragEnd={() => setDrag(null)} onKeyDown={event => { if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); move(instance.id, index + (event.key === "ArrowUp" ? -1 : 1)); } }}>⠿</button><button aria-label={`Options for ${type.title}`} aria-expanded={menu === instance.id} disabled={disabled || !!error} onClick={() => setMenu(menu === instance.id ? null : instance.id)}>⋯</button></div></header>
        {menu === instance.id && <div className="rd-widget-menu" role="group" aria-label={`${type.title} options`}><button disabled={disabled || index === 0} onClick={() => move(instance.id, index - 1)}>Move earlier</button><button disabled={disabled || index === layout.instances.length - 1} onClick={() => move(instance.id, index + 1)}>Move later</button><button disabled={disabled} onClick={() => void save(layout.instances.filter(item => item.id !== instance.id))}>Remove widget</button></div>}
        <WidgetContent type={type} instance={instance} source={source} />
      </article>;
    })}{!layout.instances.length && <p className="rd-widget-empty">No widgets here. Choose Add widget to bring one back.</p>}</div>
    <span className="rd-widget-status" role="status">{saving ? "Saving layout…" : announcement}</span>
  </section>;
}
