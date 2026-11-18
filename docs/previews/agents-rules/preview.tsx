import { createRoot } from "react-dom/client";
import { AgentsMdModal } from "../../../web/src/AgentsMdModal";
import { StaticEdition } from "../../../web/src/edition";
import { ToastProvider } from "../../../web/src/ui";
import "../../../web/src/theme.css";
import fixture from "../../../web/e2e/fixtures/agents-rules.json";

const rules = fixture.map(r => ({ title: "", explanation: "", when: "", examples: [], exceptions: [], source: "manual", evidence: 1, added: "", note: "", revision: 1, source_refs: [], ...r }));
const proposals = [{ id: "preview-example", status: "pending", claim: "Add a concrete example to the gate rule", memory_kind: "procedural", source_refs: ["preview:illustrative-example"], action: "add_example", reason: "The existing rule already covers this behavior. One example makes it clearer without adding another rule.", target: { rule_id: rules[0].id, base_revision: 1 }, changes: { example: { situation: "You only need the last lines of a long gate log.", do: "Run the gate directly, check its exit code, then read the saved log.", avoid: "scripts/gate.sh | tail -20" } }, context: null }];
const text = "# AGENTS.md\n\n" + rules.map(r => `- ${r.text}`).join("\n\n");
// The standalone preview never contacts a server or writes the user's files.
window.fetch = async (_url, init) => init?.method === "POST"
  ? Response.json({ error: "Preview only: saving and model requests are disabled. No files were changed." }, { status: 409 })
  : Response.json({ rules, proposals, version: "preview", drift: false, text, rendered_text: text, managed: true });
createRoot(document.getElementById("root")!).render(<StaticEdition features={["rule_suggestions"]}><ToastProvider><AgentsMdModal dir="/preview/duckterm" onClose={() => { window.location.reload(); }} /></ToastProvider></StaticEdition>);
