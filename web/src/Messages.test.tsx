import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Messages as ActualMessages } from "./Messages";

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
  missing: { messages: [], transcript: { status: "identity_missing", reason: "No conversation ID has been recorded for this session yet." } },
  absent: { messages: [], transcript: { status: "not_found", reason: "The recorded transcript is unavailable." } },
  empty: { messages: [], transcript: { status: "ready" } },
};

let scope = 0;
function Messages(props: React.ComponentProps<typeof ActualMessages>) {
  return <ActualMessages {...props} sessionKey={`${scope}-${props.sessionKey}`} />;
}
let resolvers: (() => void)[] = [];

beforeEach(() => {
  scope++;
  resolvers = [];
  vi.stubGlobal("fetch", (url: string) => {
    const key = String(url).split("/sessions/")[1]?.split("/")[0]?.replace(/^\d+-/, "") ?? "";
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


describe("Messages transcript availability", () => {
  it("distinguishes an unidentified conversation from a missing local transcript and an empty one", async () => {
    const { rerender } = render(<Messages sessionKey="missing" />);
    flush();
    await waitFor(() => expect(screen.getByText("Conversation not identified yet")).toBeTruthy());
    expect(screen.getByText("No conversation ID has been recorded for this session yet.")).toBeTruthy();
    expect(screen.queryByText(/No agent reply yet/)).toBeNull();

    rerender(<Messages sessionKey="absent" />);
    expect(screen.queryByText("Conversation not identified yet")).toBeNull();
    flush();
    await waitFor(() => expect(screen.getByText("Conversation transcript not found on this machine")).toBeTruthy());
    expect(screen.getByText("The recorded transcript is unavailable.")).toBeTruthy();
    expect(screen.queryByText(/No agent reply yet/)).toBeNull();

    rerender(<Messages sessionKey="empty" />);
    flush();
    await waitFor(() => expect(screen.getByText("No agent reply yet.")).toBeTruthy());
    expect(screen.queryByText(/not found on this machine/)).toBeNull();
  });

  it("clears the unavailable state when the same session reports its conversation", async () => {
    let response: unknown = { messages: [], transcript: { status: "identity_missing" } };
    vi.stubGlobal("fetch", () => Promise.resolve({ json: () => Promise.resolve(response) }));
    render(<Messages sessionKey="recovering" />);
    await waitFor(() => expect(screen.getByText("Conversation not identified yet")).toBeTruthy());
    response = transcript("recovered prompt", "RECOVERED REPLY");
    await waitFor(() => expect(screen.getByText("RECOVERED REPLY")).toBeTruthy(), { timeout: 4500 });
    expect(screen.queryByText("Conversation not identified yet")).toBeNull();
  });
});
