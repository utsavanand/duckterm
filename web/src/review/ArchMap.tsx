import type { ArchMap, ReleaseCompare } from "../api";

const LANES = ["Clients", "Server", "Runtime"] as const;
const LANE_X = { Clients: 0, Server: 1, Runtime: 2 };
const WIDTH = 900;
const COLUMN = WIDTH / 3;
const BOX_W = 220;
const BOX_H = 44;
const GAP = 14;

// Three lanes of boxes with straight edges; marks colour added, changed and
// removed modules when a comparison is set. Plain SVG, no library.
export function ArchMapView({ map, comparison, otherMap }: { map: ArchMap; comparison: ReleaseCompare | null; otherMap?: ArchMap }) {
  const modules = [...(map.modules ?? [])];
  if (comparison && otherMap) {
    for (const removed of comparison.removed_modules) if (!modules.some(m => m.id === removed.id)) modules.push(removed);
  }
  const positions = new Map<string, { x: number; y: number }>();
  const counts: Record<string, number> = { Clients: 0, Server: 0, Runtime: 0 };
  for (const module of modules) {
    const lane = LANES.includes(module.lane) ? module.lane : "Server";
    const row = counts[lane]++;
    positions.set(module.id, { x: LANE_X[lane] * COLUMN + (COLUMN - BOX_W) / 2, y: 36 + row * (BOX_H + GAP) });
  }
  const height = 36 + Math.max(...Object.values(counts), 1) * (BOX_H + GAP) + 10;
  const edges = [...(map.edges ?? [])];
  if (comparison) for (const edge of comparison.edges_removed) edges.push(edge);
  const removedEdges = new Set(comparison?.edges_removed.map(e => e.join("→")) ?? []);
  const addedEdges = new Set(comparison?.edges_added.map(e => e.join("→")) ?? []);
  return <div className="rd-archmap-wrap">
    <svg className="rd-archmap" viewBox={`0 0 ${WIDTH} ${height}`} role="img" aria-label={`Architecture at ${map.tag}`}>
      {LANES.map(lane => <g key={lane}>
        <rect x={LANE_X[lane] * COLUMN + 6} y={4} width={COLUMN - 12} height={height - 8} rx={10} className="rd-archmap-lane" />
        <text x={LANE_X[lane] * COLUMN + 18} y={22} className="rd-archmap-lane-label">{lane}</text>
      </g>)}
      {edges.map(([from, to], index) => {
        const a = positions.get(from); const b = positions.get(to);
        if (!a || !b) return null;
        const key = `${from}→${to}`;
        return <line key={`${key}-${index}`} x1={a.x + BOX_W / 2} y1={a.y + BOX_H / 2} x2={b.x + BOX_W / 2} y2={b.y + BOX_H / 2}
          className={`rd-archmap-edge${removedEdges.has(key) ? " removed" : addedEdges.has(key) ? " added" : ""}`} />;
      })}
      {modules.map(module => {
        const p = positions.get(module.id)!;
        const mark = comparison?.marks[module.id] ?? "";
        return <g key={module.id} className={`rd-archmap-node${mark ? ` is-${mark}` : ""}`} data-mark={mark || undefined}>
          <title>{module.name}: {module.what}{module.files.length ? ` (${module.files.join(", ")})` : ""}</title>
          <rect x={p.x} y={p.y} width={BOX_W} height={BOX_H} rx={8} />
          <text x={p.x + 12} y={p.y + 19} className="rd-archmap-name">{module.name}</text>
          <text x={p.x + 12} y={p.y + 35} className="rd-archmap-what">{module.what.slice(0, 34)}{module.what.length > 34 ? "…" : ""}</text>
        </g>;
      })}
    </svg>
    {comparison && <p className="rd-archmap-legend"><span className="is-added">added</span><span className="is-changed">changed</span><span className="is-removed">removed</span><span>unchanged</span></p>}
  </div>;
}
