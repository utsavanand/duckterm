import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { HomeChat } from "./HomeChat";
import { LEAKY, remoteRefs } from "./test/remoteLoads";
vi.mock("./api", () => ({ api: { oracleChat: vi.fn(), fleetAsk: vi.fn(), clearOracleChat: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("renders answers without anything that loads remotely, keeping session links", async () => {
  vi.mocked(api.oracleChat).mockResolvedValue({ messages: [{ q: "who?", a: `**ui-dev** is waiting.\n\n${LEAKY}`, at: 1 }] });
  const { container } = render(<HomeChat sessions={[{ key: "s-ui", name: "ui-dev" }] as never} onOpenTerminal={() => {}} />);
  expect(await screen.findByRole("button", { name: "ui-dev" })).toHaveAttribute("data-session", "s-ui");
  expect(screen.getByText("Leak check").tagName).toBe("STRONG");
  expect(remoteRefs(container)).toEqual([]);
});
