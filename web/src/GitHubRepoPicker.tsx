import { useEffect, useRef, useState } from "react";
import { destinationRequest } from "./desktop";
import { Button, inputStyle } from "./ui";

export type GitHubRepository = { full_name: string; private: boolean; default_branch: string };
type Catalog = { repositories: GitHubRepository[]; next_page: number | null; identity?: string };

export function GitHubRepoPicker({ target, onPick, onCancel }: {
  target: string; onPick: (repository: GitHubRepository) => void; onCancel: () => void;
}) {
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [query, setQuery] = useState("");
  const [next, setNext] = useState<number | null>(1);
  const [identity, setIdentity] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function load(page: number) {
    setBusy(true); setError("");
    try {
      const data = await destinationRequest<Catalog>(target, "project-repositories", { page });
      if (!mounted.current) return;
      setRepositories(old => [...new Map([...old, ...data.repositories].map(repo => [repo.full_name, repo])).values()]);
      setNext(data.next_page); setIdentity(data.identity ?? "");
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { if (mounted.current) setBusy(false); }
  }
  useEffect(() => { void load(1); /* Each target gets its own picker instance. */
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const matches = repositories.filter(repo => repo.full_name.toLowerCase().includes(query.toLowerCase()));
  return <section aria-label="GitHub repositories" style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, marginBottom: 12 }}>
    <p style={{ marginTop: 0 }}>GitHub{identity ? ` · ${identity}` : ""} · repositories available through this destination’s connector</p>
    <input aria-label="Filter GitHub repositories" style={inputStyle} placeholder="Filter loaded repositories…" value={query} onChange={e => setQuery(e.target.value)} />
    <div style={{ maxHeight: 200, overflowY: "auto", marginBlock: 8 }}>
      {matches.map(repo => <button key={repo.full_name} type="button" className="rd-btn rd-btn-ghost" style={{ display: "block", width: "100%", textAlign: "left", marginBottom: 4 }} onClick={() => onPick(repo)}>{repo.full_name}{repo.private ? " · Private" : ""}</button>)}
      {!busy && !matches.length && <p>{repositories.length ? "No matching repositories loaded." : "No repositories loaded."}</p>}
    </div>
    {busy && <p role="status">Loading GitHub repositories…</p>}
    {error && <p role="alert">{error}. Manage GitHub in Connectors on the destination computer, then retry.</p>}
    <div style={{ display: "flex", gap: 8 }}>
      {next !== null && <Button size="sm" disabled={busy} onClick={() => void load(next)}>{error ? "Retry" : "Load more repositories"}</Button>}
      <Button size="sm" variant="ghost" onClick={onCancel}>Close repository picker</Button>
    </div>
    <small>Only repositories granted to the connected account are listed. Load more to search older repositories.</small>
  </section>;
}
