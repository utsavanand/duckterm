import { Fragment, useRef } from "react";

export function matchesAgentName(name: string, query: string): boolean {
  return name.toLowerCase().includes(query.trim().toLowerCase());
}

export function HighlightName({ name, query }: { name: string; query: string }) {
  const needle = query.trim().toLowerCase();
  if (!needle) return <>{name}</>;
  const lower = name.toLowerCase();
  const parts = [];
  let offset = 0;
  let index = lower.indexOf(needle);
  while (index !== -1) {
    parts.push(<Fragment key={index}>{name.slice(offset, index)}<mark>{name.slice(index, index + needle.length)}</mark></Fragment>);
    offset = index + needle.length;
    index = lower.indexOf(needle, offset);
  }
  return <>{parts}{name.slice(offset)}</>;
}

export function AgentNameSearch({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return <div className="rd-agent-search" role="search" aria-label="Find agents">
    <svg aria-hidden="true" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="8.5" cy="8.5" r="5.5" /><path d="m13 13 4 4" /></svg>
    <input ref={input} type="search" aria-label="Search agents by name" placeholder="Search agents by name…" autoComplete="off" spellCheck={false} value={value} onChange={event => onChange(event.target.value)} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onChange(""); }
    }} />
    {value && <button type="button" aria-label="Clear search" onClick={() => { onChange(""); input.current?.focus(); }}>×</button>}
  </div>;
}
