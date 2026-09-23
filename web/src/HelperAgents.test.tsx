import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { HelperAgents } from "./HelperAgents";
afterEach(cleanup);
it("keeps active helpers visible, scopes expansion per session, and preserves completed history", () => {
  const done = { agent_id: "done", agent_type: "security", state: "done" as const, started_at: 1 };
  const active = { ...done, agent_id: "active", agent_type: "general-purpose", state: "running" as const };
  render(<><HelperAgents sessionKey="a" agents={[active, done]} /><HelperAgents sessionKey="b" agents={[{ ...done, agent_type: "review" }, { ...done, agent_id: "done2", agent_type: "research" }]} /></>);
  expect(screen.getByText("general-purpose")).toBeVisible();
  expect(screen.getByText("security")).not.toBeVisible();
  expect(screen.getByText("review")).not.toBeVisible();
  fireEvent.click(screen.getByLabelText("1 completed helper"));
  expect(screen.getByText("security")).toBeVisible();
  expect(screen.getByText("review")).not.toBeVisible();
  fireEvent.click(screen.getByLabelText("2 completed helpers"));
  expect(screen.getByText("review")).toBeVisible();
  expect(screen.getByText("general-purpose")).toBeVisible();
});
it("moves a newly completed helper into collapsed history without losing its prompt", () => {
  const agent = { agent_id: "a", agent_type: "reviewer", agent_prompt: "Review the release", state: "running" as const, started_at: 1 };
  const { rerender } = render(<HelperAgents sessionKey="a" agents={[agent]} />);
  expect(screen.getByText("Review the release")).toBeVisible();
  expect(document.querySelector("summary")).toBeNull();
  rerender(<HelperAgents sessionKey="a" agents={[{ ...agent, state: "done" }]} />);
  expect(screen.getByText("Review the release")).not.toBeVisible();
  fireEvent.click(screen.getByLabelText("1 completed helper"));
  expect(screen.getByText("Review the release")).toBeVisible();
});
