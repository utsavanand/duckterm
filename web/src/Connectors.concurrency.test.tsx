import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Connectors } from "./Connectors";
import { api, type Connector } from "./api";

vi.mock("./api", () => ({ api: { connectors: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function pending() {
  let resolve!: (value: { connectors: Connector[] }) => void;
  const promise = new Promise<{ connectors: Connector[] }>(r => { resolve = r; });
  return { promise, resolve };
}

it("coalesces focus refresh while the same host status request is pending", async () => {
  const request = pending();
  vi.mocked(api.connectors).mockReturnValue(request.promise);
  render(<Connectors sessionKey="local-a" />);
  for (let i = 0; i < 5; i++) fireEvent(window, new Event("focus"));
  const callsBeforeResponse = vi.mocked(api.connectors).mock.calls.length;
  await act(async () => { request.resolve({ connectors: [] }); });
  expect(callsBeforeResponse).toBe(1);
});

it("does not render an obsolete context response after selecting a different context", async () => {
  const previous = pending();
  const current = pending();
  vi.mocked(api.connectors).mockReturnValueOnce(previous.promise).mockReturnValueOnce(current.promise);
  const view = render(<Connectors sessionKey="local-a" />);
  view.rerender(<Connectors sessionKey="local-b" />);
  await act(async () => { current.resolve({ connectors: [] }); });
  expect(screen.getByText("Connectors (0) · This Mac")).toBeInTheDocument();
  await act(async () => {
    previous.resolve({ connectors: [{ name: "github", title: "Obsolete connector result", description: "old", credential: null, identity: null, sources: [], write_access: false, enabled: false, installed: {}, ready: false, detail: null, managed: false, revoke_url: "", last_used: null, use_count: 0, harnesses: [], harnesses_present: {} }] });
  });
  expect(screen.queryByText("Obsolete connector result")).not.toBeInTheDocument();
});
