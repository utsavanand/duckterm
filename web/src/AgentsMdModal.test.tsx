import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentsMdModal, type Rule } from "./AgentsMdModal";
import { sessionFetch } from "./hostTransport";
import { edition, CODE, FULL, LITE } from "./test/editions";

vi.mock("./hostTransport", () => ({ sessionFetch: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const rule: Rule = { id: "gate", text: "Do not pipe the gate.", scope: "all", status: "active", source: "manual", evidence: 1, added: "", heading: "Testing", note: "", title: "Preserve exit status", explanation: "Failures can be hidden.", when: "Running tests", examples: [], exceptions: [], revision: 1, source_refs: [] };
const catalog = { rules: [rule], proposals: [], version: "version-one", drift: false, text: "# AGENTS.md", rendered_text: "# AGENTS.md", managed: true };
const proposal = { id: "proposal", status: "pending", claim: "head also hides failures", memory_kind: "procedural", source_refs: ["event:1"], action: "add_example", reason: "Same principle", target: { rule_id: "gate", base_revision: 1 }, changes: { example: { situation: "Read the log", do: "Read it after the gate exits.", avoid: "gate | head" } }, context: null };

it("disables mutations until the initial read completes", async () => {
  let resolve!: (r: Response) => void;
  vi.mocked(sessionFetch).mockReturnValue(new Promise(r => { resolve = r; }));
  render(<AgentsMdModal dir="/project" onClose={vi.fn()} />);
  expect(screen.getByRole("button", { name: /Add rule/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  await act(async () => resolve(Response.json(catalog)));
  expect(screen.getByRole("button", { name: /Add rule/ })).toBeEnabled();
  expect(screen.getByText("Do not pipe the gate.")).toBeVisible();
});

it.each(["http", "network"])("keeps save disabled after %s load failure", async kind => {
  if (kind === "http") vi.mocked(sessionFetch).mockResolvedValue(Response.json({ error: "Denied" }, { status: 403 }));
  else vi.mocked(sessionFetch).mockRejectedValue(new Error("Offline"));
  await act(async () => { render(<AgentsMdModal dir="/project" onClose={vi.fn()} />); });
  expect(screen.getByRole("alert")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Add rule/ })).toBeDisabled();
});

it("submits accepted example proposals without duplicating the existing rule", async () => {
  vi.mocked(sessionFetch).mockResolvedValueOnce(Response.json({ ...catalog, proposals: [proposal] })).mockResolvedValueOnce(Response.json({ ...catalog, version: "version-two", rules: [{ ...rule, examples: [proposal.changes.example], revision: 2 }], proposals: [{ ...proposal, status: "accepted" }] }));
  await act(async () => { render(<AgentsMdModal dir="/project" onClose={vi.fn()} />); });
  fireEvent.click(screen.getByRole("button", { name: "Suggestions 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Accept change" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save" })));
  const body = JSON.parse(vi.mocked(sessionFetch).mock.calls[1][2]!.body as string);
  expect(body.rules).toEqual([rule]);
  expect(body.proposals[0].status).toBe("accepted");
  expect(body.version).toBe("version-one");
  fireEvent.click(screen.getByRole("button", { name: "Rules 1" }));
  expect(screen.getByText("Read it after the gate exits.", { exact: false })).toBeVisible();
});

it("episodic observations cannot be accepted as rules", async () => {
  const episode = { ...proposal, action: "retain_context", memory_kind: "episodic", target: null, changes: null, context: { destination: "summary", text: "The gate failed today." } };
  vi.mocked(sessionFetch).mockResolvedValue(Response.json({ ...catalog, proposals: [episode] }));
  await act(async () => { render(<AgentsMdModal dir="/project" onClose={vi.fn()} />); });
  fireEvent.click(screen.getByRole("button", { name: "Suggestions 1" }));
  expect(screen.queryByRole("button", { name: "Accept change" })).toBeNull();
  expect(screen.getByText("The gate failed today.")).toBeVisible();
});

it("stale folder responses cannot replace the newly selected directory", async () => {
  let finish!: (r: Response) => void;
  vi.mocked(sessionFetch).mockReturnValueOnce(new Promise(r => { finish = r; })).mockResolvedValueOnce(Response.json(catalog));
  const view = render(<AgentsMdModal dir="/old" onClose={vi.fn()} />);
  await act(async () => view.rerender(<AgentsMdModal dir="/new" onClose={vi.fn()} />));
  await act(async () => finish(Response.json({ ...catalog, rules: [{ ...rule, text: "Stale content" }] })));
  expect(screen.queryByText("Stale content")).toBeNull();
  expect(screen.getByText("/new/AGENTS.md")).toBeVisible();
});

it("requires explicit comparison before overwriting a diverged AGENTS.md", async () => {
  vi.mocked(sessionFetch).mockResolvedValue(Response.json({ ...catalog, drift: true, proposals: [proposal] }));
  await act(async () => { render(<AgentsMdModal dir="/project" onClose={vi.fn()} />); });
  fireEvent.click(screen.getByRole("button", { name: "Suggestions 1" }));
  fireEvent.click(screen.getByRole("button", { name: "Accept change" }));
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Compare files" }));
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
});


it.each([
  ["Lite", LITE, false], ["Code", CODE, true], ["Full", FULL, true],
  ["Code without context", CODE.filter(feature => feature !== "context"), true],
  ["Full without suggestions", FULL.filter(feature => feature !== "rule_suggestions"), false],
] as const)("%s keeps manual editing available while gating model requests", async (_name, features, enabled) => {
  vi.mocked(sessionFetch).mockResolvedValue(Response.json(catalog));
  await act(async () => { render(<AgentsMdModal dir="/project" onClose={vi.fn()} />, { wrapper: edition(features) }); });
  expect(screen.getByRole("button", { name: /Add rule/ })).toBeEnabled();
  expect(!!screen.queryByRole("button", { name: "Suggest from corrections" })).toBe(enabled);
  fireEvent.click(screen.getByRole("button", { name: /Add rule/ }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "New behavior" } });
  fireEvent.change(screen.getByLabelText("Instruction"), { target: { value: "Keep examples concise." } });
  if (!enabled) {
    fireEvent.click(screen.getByRole("button", { name: "Keep new rule" }));
    expect(screen.getByText("Keep examples concise.")).toBeVisible();
    expect(sessionFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  } else {
    expect(screen.getByRole("button", { name: "Compare with existing rules" })).toBeEnabled();
  }
});
