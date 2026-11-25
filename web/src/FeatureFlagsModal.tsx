import { useEffect, useState } from "react";
import { api } from "./api";
import { Modal, Button } from "./ui";

export function FeatureFlagsModal({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.featureFlags>> | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    void api.featureFlags().then(result => { if (active) setData(result); }, (e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [retry]);
  return <Modal title="Feature flags" onClose={onClose} width={760}>
    <p>Availability on this installation. Local overrides stay on this computer.</p>
    {!data && !error && <p role="status">Loading feature flags…</p>}
    {data && <div style={{ overflowX: "auto" }}><table style={{ width: "100%", textAlign: "left", borderSpacing: "12px" }}>
      <thead><tr><th>Feature</th><th>Release default</th><th>This installation</th><th>Reason</th></tr></thead>
      <tbody>{data.flags.map(flag => <tr key={flag.id}><td>{flag.name}</td><td>{flag.default ? "On" : "Off"}</td><td>{flag.enabled ? "On" : "Off"}</td><td>{flag.reason}</td></tr>)}</tbody>
    </table></div>}
    {data?.restart_required && <p>Restart DuckTerm after changing local flag configuration.</p>}
    {error && <div role="alert"><p>{error}</p><Button onClick={() => setRetry(n => n + 1)}>Retry</Button></div>}
    <Button onClick={onClose}>Close</Button>
  </Modal>;
}
