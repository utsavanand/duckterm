import { useState } from "react";
import type { DesignBox, DesignChange, DesignData } from "../cardModel";
import type { FileFilter } from "./FilesTab";

const CATEGORIES: { name: DesignChange["category"]; note?: string }[] = [
  { name: "New capability" }, { name: "Fix and hardening" },
  { name: "Side effects", note: "changes the PR does not explain" },
  { name: "Supporting files", note: "tests, generated code, wording" },
];

// A short label for a change: its solution up to the first clause break.
export function changeLabel(change: DesignChange): string {
  return clip(change.solution.split(/[,.:;(]/)[0].trim(), 64);
}

// SVG text does not wrap; cut to what fits and keep the whole text on hover.
function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

// Rows are the order each component appears in its column.
function rows(items: { id: string; column: number }[]): Map<string, number> {
  const seen = new Map<number, number>(), out = new Map<string, number>();
  for (const item of items) { const row = seen.get(item.column) ?? 0; out.set(item.id, row); seen.set(item.column, row + 1); }
  return out;
}

const NODE_W = 158, NODE_H = 58, NODE_X = (c: number) => 22 + c * 178, NODE_Y = (r: number) => 46 + r * 80;

export function MapDiagram({ design, untested, onFiles }: { design: DesignData; untested: Set<string>; onFiles: (filter: FileFilter) => void }) {
  const [lit, setLit] = useState<number | null>(null);
  const row = rows(design.components);
  const byId = new Map(design.components.map(c => [c.id, c]));
  const hl = lit === null ? null : new Set(design.changes[lit].components);
  const width = NODE_X(design.columns.length) + 4, height = NODE_Y(Math.max(1, ...[...row.values()].map(r => r + 1)));
  const open = (id: string) => { const c = byId.get(id); if (c?.files.length) onFiles({ label: c.label, files: c.files }); };
  return <>
    <div className="rv2-mapbox">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Map of the system with changed parts highlighted">
        {design.columns.map((name, i) => <text key={name + i} className="col" x={NODE_X(i)} y={24}>{name}</text>)}
        {design.edges.map(({ from, to }) => {
          const a = byId.get(from)!, b = byId.get(to)!;
          const ar = row.get(from)!, br = row.get(to)!;
          let d: string;
          if (a.column === b.column) d = `M${NODE_X(a.column) + NODE_W / 2},${NODE_Y(Math.min(ar, br)) + NODE_H} L${NODE_X(b.column) + NODE_W / 2},${NODE_Y(Math.max(ar, br))}`;
          else {
            const [l, r, lr, rr] = a.column < b.column ? [a, b, ar, br] : [b, a, br, ar];
            const x1 = NODE_X(l.column) + NODE_W, y1 = NODE_Y(lr) + NODE_H / 2, x2 = NODE_X(r.column), y2 = NODE_Y(rr) + NODE_H / 2, m = (x1 + x2) / 2;
            d = `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`;
          }
          const on = hl && hl.has(from) && hl.has(to);
          return <path key={`${from}>${to}`} className={`edge${on ? " hl" : hl ? " dim" : ""}`} d={d} />;
        })}
        {design.components.map(c => {
          const x = NODE_X(c.column), y = NODE_Y(row.get(c.id)!);
          // The bar stops short of the line counts printed at its right.
          const total = c.additions + c.deletions, bar = Math.min(NODE_W - 96, Math.round(Math.sqrt(total) * 4)), add = total ? Math.round(bar * c.additions / total) : 0;
          const flag = c.files.length > 0 && c.files.some(path => untested.has(path)) ? "no test" : "";
          const cls = ["node", c.files.length ? "changed" : "still", c.new ? "new" : "", hl ? (hl.has(c.id) ? "hl" : "dim") : ""].filter(Boolean).join(" ");
          return <g key={c.id} className={cls} role={c.files.length ? "button" : undefined} tabIndex={c.files.length ? 0 : undefined}
            aria-label={c.files.length ? `${c.label}: ${c.files.length} files` : `${c.label}, unchanged`} onClick={() => open(c.id)}
            onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(c.id); } }}>
            <title>{`${c.label}\n${c.sub}`}</title>
            <rect className="box" x={x} y={y} width={NODE_W} height={NODE_H} />
            <text x={x + 10} y={y + 19}>{clip(c.label, flag ? 15 : 21)}</text>
            <text className="sub" x={x + 10} y={y + 35}>{clip(c.sub, 20)}</text>
            {total > 0 && <>
              <rect className="bar-add" x={x + 10} y={y + 44} width={add} height={4} /><rect className="bar-del" x={x + 10 + add} y={y + 44} width={bar - add} height={4} />
              <text className="sub" x={x + NODE_W - 8} y={y + 50} textAnchor="end">+{c.additions} −{c.deletions}</text>
            </>}
            {flag && <text className="flag" x={x + NODE_W - 8} y={y + 19} textAnchor="end">{flag}</text>}
          </g>;
        })}
      </svg>
      <div className="rv2-legend"><span><i className="a" />lines added</span><span><i className="d" />lines removed</span><span><i className="n" />new file</span><span><i className="u" />unchanged, shown for context</span><span>No test = no test reaches it</span></div>
    </div>
    {design.changes.length > 0 && <div>
      <h3 className="rv2-lbl">Show a change on the map</h3>
      <div className="rv2-chips">{design.changes.map((change, i) => <button key={i} title={change.solution} aria-pressed={lit === i} onClick={() => setLit(lit === i ? null : i)}>{changeLabel(change)}</button>)}</div>
    </div>}
  </>;
}

export function ChangesBoard({ design, onFiles }: { design: DesignData; onFiles: (filter: FileFilter) => void }) {
  const groups = CATEGORIES.map(category => ({ ...category, items: design.changes.filter(change => change.category === category.name) })).filter(group => group.items.length);
  if (!groups.length) return <p className="rv2-dim">The model found no change to list.</p>;
  return <div className="rv2-cats">{groups.map((group, g) => <section key={group.name} className={`rv2-cat${group.name === "Side effects" ? " warn" : ""}`} aria-label={group.name}>
    <div className="rv2-cathead"><span className="idx">0{g + 1}</span><h3>{group.name}</h3><span className="dot" /><span className="cnt">{group.items.length}</span>{group.note && <span className="sub">{group.note}</span>}</div>
    <ul className="rv2-items">{group.items.map((change, i) => {
      const support = group.name === "Supporting files";
      return <li key={i}><button onClick={() => onFiles({ label: changeLabel(change), files: change.files })} disabled={!change.files.length}>
        <span className="txt">
          <span className={`pr${change.problem ? "" : " none"}`}>{support ? <span className="lb">{change.problem}</span> : <><span className="lb">Problem</span>{change.problem || "Not stated in the PR"}</>}</span>
          <span className="so"><span className="lb">{support ? "" : "Solution"}</span>{change.solution}</span>
        </span>
        {change.files.length > 0 && <span className="more">See files →</span>}
      </button></li>;
    })}</ul>
  </section>)}</div>;
}

const BOX_W = 176, ROW_H = 17, BOX_X = (c: number) => 14 + c * 190;

function shownItems(box: DesignBox, after: boolean) {
  return box.items.filter(item => after ? true : item.state !== "added");
}

export function DesignHistory({ design, base, head }: { design: DesignData; base: string; head: string }) {
  const [after, setAfter] = useState(true);
  // Before hides what the PR adds; After keeps what it removes, struck through.
  const boxes = design.boxes.filter(box => after || box.state !== "added");
  // Stack boxes down each column, leaving room for each one's lines.
  const geom = new Map<string, { x: number; y: number; h: number }>(), next = new Map<number, number>();
  for (const box of boxes) {
    const h = 30 + shownItems(box, after).length * ROW_H + 6, y = next.get(box.column) ?? 40;
    geom.set(box.id, { x: BOX_X(box.column), y, h }); next.set(box.column, y + h + 26);
  }
  const width = BOX_X(design.columns.length) + 4, height = Math.max(120, ...next.values());
  const pick = (state: "added" | "changed" | "removed") => design.boxes.flatMap(box => box.items.filter(item => item.state === state)
    .map(item => `${box.title}: ${state === "removed" ? item.before : state === "changed" ? `${item.before} → ${item.after}` : item.after}`));
  const delta: [string, string, string[]][] = [["a", "Added", pick("added")], ["c", "Changed", pick("changed")], ["r", "Removed", pick("removed")]];
  return <>
    <div className="rv2-dhead">
      <div><h3 className="rv2-lbl">Design of the service under review</h3>
        <p className="rv2-dim">{design.scope ? `${design.scope}, read` : "Read"} from the diff between <code>{base}</code> and this PR's head <code>{head}</code>.</p></div>
      <span className="rv2-seg" role="group" aria-label="Design view">
        <button aria-pressed={!after} onClick={() => setAfter(false)}>Before · {base.split(" ")[0]}</button>
        <button aria-pressed={after} onClick={() => setAfter(true)}>After · with this PR</button>
      </span>
    </div>
    <div className="rv2-mapbox">
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Design ${after ? "after" : "before"} this PR`}>
        <defs><marker id="rv2-arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" className="arrow" /></marker></defs>
        {design.columns.map((name, i) => <text key={name + i} className="col" x={BOX_X(i)} y={24}>{name}</text>)}
        {design.box_edges.filter(edge => after ? edge.state !== "removed" : edge.state !== "added").map(edge => {
          const a = geom.get(edge.from), b = geom.get(edge.to);
          if (!a || !b) return null;
          let d: string;
          if (a.x === b.x) d = a.y < b.y ? `M${a.x + BOX_W / 2},${a.y + a.h} L${b.x + BOX_W / 2},${b.y - 2}` : `M${a.x + BOX_W / 2},${a.y} L${b.x + BOX_W / 2},${b.y + b.h + 2}`;
          else {
            const [l, r] = a.x < b.x ? [a, b] : [b, a];
            const y1 = l.y + l.h / 2, y2 = r.y + r.h / 2, x1 = l.x + BOX_W, x2 = r.x - 3, m = (x1 + x2) / 2;
            d = a.x < b.x ? `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}` : `M${x2 + 3},${y2} C${m},${y2} ${m},${y1} ${x1 + 3},${y1}`;
          }
          return <path key={`${edge.from}>${edge.to}`} className={`dedge${after && edge.state === "added" ? " added" : ""}`} d={d} markerEnd="url(#rv2-arr)" />;
        })}
        {boxes.map(box => {
          const g = geom.get(box.id)!;
          return <g key={box.id} className={`dbox${after && box.state !== "same" ? ` ${box.state}` : ""}`}>
            <rect className="frame" x={g.x} y={g.y} width={BOX_W} height={g.h} />
            <text className="t" x={g.x + 10} y={g.y + 19}>{clip(box.title, 20)}<title>{box.title}</title></text>
            {shownItems(box, after).map((item, i) => {
              const mark = after ? ({ added: "+ ", changed: "~ ", removed: "− ", same: "" })[item.state] : "";
              const line = mark + (after ? (item.state === "removed" ? item.before : item.after) : item.before);
              return <text key={i} className={`it ${after ? item.state : "same"}`} x={g.x + 10} y={g.y + 38 + i * ROW_H}>{clip(line, 26)}<title>{line}</title></text>;
            })}
          </g>;
        })}
      </svg>
      <div className="rv2-legend">{after
        ? <><span className="add">+ added</span><span className="warn">~ changed</span><span className="del">− removed</span><span>grey: unchanged</span><span>dashed: new part or new call</span></>
        : <span>The design on {base.split(" ")[0]} before this PR. Switch to After to see what this PR adds.</span>}</div>
    </div>
    <div className="rv2-delta">{delta.map(([cls, title, list]) => <div key={cls} className={cls}><h3>{title} · {list.length}</h3>
      {list.length ? <ul>{list.map((line, i) => <li key={i}>{line}</li>)}</ul> : <p className="rv2-dim">Nothing</p>}</div>)}</div>
  </>;
}
