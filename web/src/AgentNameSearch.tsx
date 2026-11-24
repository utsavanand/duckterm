import { Fragment, useRef } from "react";

export function matchesAgentName(name: string, query: string): boolean {
  return name.toLowerCase().includes(query.trim().toLowerCase());
}

export function HighlightName({ name, query }: { name: string; query: string }) {
  const needle = query.trim().toLowerCase();
  if (!needle) return <>{name}</>;
  const lower = name.toLowerCase();
  // Lowercase offsets may differ from the label (İ becomes i + combining dot).
  // Search the whole lowercase string to retain contextual casing such as ΟΣ.
  const starts: number[] = [], ends: number[] = [];
  let position = 0;
  for (const char of name) {
    for (let i = 0; i < char.toLowerCase().length; i++) {
      starts.push(position);
      ends.push(position + char.length);
    }
    position += char.length;
  }
  const ranges: { start: number; end: number }[] = [];
  let index = lower.indexOf(needle);
  while (index !== -1) {
    const start = starts[index], end = ends[index + needle.length - 1];
    const previous = ranges[ranges.length - 1];
    // Two folded matches can share an original character. Render it only once.
    if (previous && start < previous.end) previous.end = end;
    else ranges.push({ start, end });
    index = lower.indexOf(needle, index + needle.length);
  }
  let offset = 0;
  const parts = ranges.map(({ start, end }) => {
    const part = <Fragment key={start}>{name.slice(offset, start)}<mark>{name.slice(start, end)}</mark></Fragment>;
    offset = end;
    return part;
  });
  return <>{parts}{name.slice(offset)}</>;
}

export function AgentNameSearch({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return <div className="rd-agent-search" role="search" aria-label="Find agents">
    <svg aria-hidden="true" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" /></svg>
    <input ref={input} type="search" aria-label="Search agents by name" placeholder="Search agents by name…" autoComplete="off" spellCheck={false} value={value} onChange={event => onChange(event.target.value)} onKeyDown={event => {
      if (event.key === "Escape" && value) { event.preventDefault(); event.stopPropagation(); onChange(""); }
    }} />
    {value && <button type="button" aria-label="Clear search" onClick={() => { onChange(""); input.current?.focus(); }}>×</button>}
  </div>;
}
