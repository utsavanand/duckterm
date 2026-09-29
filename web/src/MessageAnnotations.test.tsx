import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Messages } from "./Messages";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("loads existing notes, counts only truly unlocated quotes and keeps notes escaped", async () => {
  vi.stubGlobal("fetch", async (url: string) => ({ ok: true, json: async () => String(url).endsWith("annotations") ? { annotations: [
    {id:"old", quote:"Earlier reply", note:"Old note", created_at:1},
    {id:"new", quote:"bold words", note:"<b>literal note</b>", created_at:2},
    {id:"missing", quote:"removed", note:"Retain me", created_at:3},
  ] } : { messages: [
    {id:1, role:"user", blocks:[{type:"text",text:"First"}]},
    {id:2, role:"assistant", blocks:[{type:"text",text:"Earlier reply"}]},
    {id:3, role:"user", blocks:[{type:"text",text:"Next"}]},
    {id:4, role:"assistant", blocks:[{type:"text",text:"Some **bold** words"}]},
  ] } }));
  const {container, rerender} = render(<Messages sessionKey="a" />);
  await screen.findByText("3 comments · 1 not located in transcript");
  const marks = container.querySelectorAll("mark"); expect(marks).toHaveLength(2);
  fireEvent.focus(marks[0]);
  expect(screen.getByRole("tooltip").textContent).toBe("<b>literal note</b>");
  expect(screen.getByRole("tooltip").querySelector("b")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"Previous turn"}));
  await waitFor(() => expect(container.querySelector("mark")?.textContent).toBe("Earlier reply"));
  vi.stubGlobal("fetch", () => new Promise(() => {}));
  rerender(<Messages sessionKey="b" />);
  expect(container.querySelector("mark")).toBeNull();
  expect(screen.queryByText("3 comments · 1 not located in transcript")).toBeNull();
});
