import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Messages } from "./Messages";

// Messages fetches /sessions/:key/messages directly and polls it.
const transcript = (prompt: string, reply: string) => ({
  messages: [
    { id: 1, role: "user", blocks: [{ type: "text", text: prompt }] },
    { id: 2, role: "assistant", blocks: [{ type: "text", text: reply }] },
  ],
});
const transcripts: Record<string, unknown> = {
  a: transcript("alpha prompt", "ALPHA REPLY"),
  b: transcript("bravo prompt", "BRAVO REPLY"),
};

let resolvers: (() => void)[] = [];

beforeEach(() => {
  resolvers = [];
  vi.stubGlobal("fetch", (url: string) => {
    const key = String(url).split("/sessions/")[1]?.split("/")[0] ?? "";
    // Hold each response open so the test can assert what renders BEFORE it lands.
    return new Promise((resolve) => {
      resolvers.push(() =>
        resolve({ json: () => Promise.resolve(transcripts[key] ?? { messages: [] }) } as Response),
      );
    });
  });
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const flush = () => { resolvers.forEach((r) => r()); resolvers = []; };

// Switch sessions WITHOUT a changing React key — the way the dashboard did it
// before the fix. A changed key would unmount the component and hide the bug,
// so these tests deliberately reuse one instance: that is the broken path.
describe("Messages session switching", () => {
  it("never shows the previous session's reply while the next one loads", async () => {
    const { rerender } = render(<Messages sessionKey="a" />);
    flush();
    await waitFor(() => expect(screen.getByText(/ALPHA REPLY/)).toBeTruthy());

    // b's fetch is still in flight. The panel must not still show a's turns.
    rerender(<Messages sessionKey="b" />);
    expect(screen.queryByText(/ALPHA REPLY/)).toBeNull();

    flush();
    await waitFor(() => expect(screen.getByText(/BRAVO REPLY/)).toBeTruthy());
    expect(screen.queryByText(/ALPHA REPLY/)).toBeNull();
  });

  it("shows the original session's reply again when switching back", async () => {
    const { rerender } = render(<Messages sessionKey="a" />);
    flush();
    await waitFor(() => expect(screen.getByText(/ALPHA REPLY/)).toBeTruthy());

    rerender(<Messages sessionKey="b" />);
    flush();
    await waitFor(() => expect(screen.getByText(/BRAVO REPLY/)).toBeTruthy());

    rerender(<Messages sessionKey="a" />);
    expect(screen.queryByText(/BRAVO REPLY/)).toBeNull();
    flush();
    await waitFor(() => expect(screen.getByText(/ALPHA REPLY/)).toBeTruthy());
  });
});
