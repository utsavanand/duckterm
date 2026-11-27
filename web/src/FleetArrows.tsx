import { RefObject, useEffect, useRef, useState } from "react";
import { api, PeerMail } from "./api";

const POLL_MS = 5_000;
const FLIGHT_MS = 2_400;
// On first load, mail from the last minute still flies; older mail is history.
const FRESH_MS = 60_000;

type Flight = { id: string; d: string; to: { x: number; y: number }; at: number };

// Arrows between ducks when one agent writes to another. Positions come from
// the ducks on screen (data-duck), so an agent in a collapsed or hidden team
// simply has no arrow. Only who wrote to whom is fetched, never the text.
export function FleetArrows({ field, active }: { field: RefObject<HTMLElement | null>; active: boolean }) {
  const [flights, setFlights] = useState<Flight[]>([]);
  const seen = useRef<Set<string> | null>(null);

  useEffect(() => {
    if (!active) return;
    let live = true;
    const load = () => api.fleetMail().then(({ messages }) => {
      if (!live) return;
      const first = seen.current === null;
      const known = seen.current ?? new Set<string>();
      const now = Date.now();
      const fresh = messages.filter((m) => !known.has(m.id) && (!first || now - m.created_at < FRESH_MS));
      messages.forEach((m) => known.add(m.id));
      seen.current = known;
      const launched = fresh.map((m) => flight(field.current, m, now)).filter((f): f is Flight => f !== null);
      if (launched.length) setFlights((fs) => [...fs.filter((f) => now - f.at < FLIGHT_MS), ...launched]);
    }).catch(() => {}); // arrows are decoration; the next poll tries again
    void load();
    const timer = setInterval(load, POLL_MS);
    return () => { live = false; clearInterval(timer); };
  }, [active, field]);

  useEffect(() => {
    if (!flights.length) return;
    const timer = setTimeout(() => setFlights((fs) => fs.filter((f) => Date.now() - f.at < FLIGHT_MS)), FLIGHT_MS);
    return () => clearTimeout(timer);
  }, [flights]);

  if (!flights.length) return null;
  return (
    <svg className="rd-fleet-arrows" aria-hidden="true">
      {flights.map((f) => (
        <g key={f.id} className="rd-fleet-arrow">
          <path d={f.d} />
          <circle r="3.5" cx={f.to.x} cy={f.to.y} />
        </g>
      ))}
    </svg>
  );
}

function flight(field: HTMLElement | null, m: PeerMail, at: number): Flight | null {
  if (!field || m.sender === m.recipient) return null;
  const from = field.querySelector(`[data-duck="${CSS.escape(m.sender)}"] .rd-tower-duck-body`);
  const to = field.querySelector(`[data-duck="${CSS.escape(m.recipient)}"] .rd-tower-duck-body`);
  if (!from || !to) return null;
  const box = field.getBoundingClientRect();
  const centre = (el: Element) => { const r = el.getBoundingClientRect(); return { x: r.left - box.left + r.width / 2, y: r.top - box.top + r.height / 2 }; };
  const a = centre(from), b = centre(to);
  // Bow the line upward so arrows between neighbours don't sit on the row.
  const lift = Math.min(80, 20 + Math.hypot(b.x - a.x, b.y - a.y) / 4);
  const d = `M${a.x},${a.y} Q${(a.x + b.x) / 2},${Math.min(a.y, b.y) - lift} ${b.x},${b.y}`;
  return { id: m.id, d, to: b, at };
}
