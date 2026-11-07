import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { SessionLocationDuck } from "./SessionLocationDuck";
import { sessionRef } from "./hostTransport";
import { StaticEdition } from "./edition";
import { SessionView } from "./types";
afterEach(cleanup);
const local: SessionView = { key: "same", label: "Agent", state: "busy", lastEventType: "PreToolUse", startedAt: 1, updatedAt: 1, eventCount: 1 };
it("distinguishes equal local/remote IDs and keeps location accessible without a text badge", () => {
  const view = render(<SessionLocationDuck session={local} pose="busy" />);
  expect(screen.getByRole("group", { name: "This Mac" })).toBeInTheDocument();
  expect(screen.queryByText("This Mac")).toBeNull();
  view.rerender(<SessionLocationDuck session={{ ...local, key: sessionRef("build", "same"), hostLabel: "Build server" }} pose="waiting" />);
  expect(screen.getByRole("group", { name: "Remote · Build server" })).toBeInTheDocument();
  expect(view.container.querySelector('.rd-duck-waiting')).not.toBeNull();
  view.rerender(<SessionLocationDuck session={{ ...local, host: "build", hostLabel: "Build server", hostOffline: true }} pose="sleeping" />);
  expect(screen.getByRole("group", { name: "Remote · Build server · Disconnected" })).toBeInTheDocument();
  expect(view.container.querySelector('.rd-location-cloud path:last-child')).toHaveAttribute('d', 'M5 30L32 6');
});


it("keeps location and context accessible together on remote ducks", () => {
  render(<StaticEdition features={["context"]}><SessionLocationDuck pose="busy" session={{ ...local, host: "build", hostLabel: "Build server", contextTokens: 800, contextWindowTokens: 1000 }} /></StaticEdition>);
  expect(screen.getByRole("group", { name: "Remote · Build server" })).toBeInTheDocument();
  expect(screen.getByRole("img", { name: /Context 80% used, high/ })).toBeInTheDocument();
});
