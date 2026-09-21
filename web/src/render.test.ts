// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { html } from "./render";

describe("html (markdown rendering)", () => {
  it("highlights fenced code in a registered language", () => {
    const out = html("```ts\nconst n: number = 1;\n```");
    expect(out).toContain('class="hljs"');
    expect(out).toContain("hljs-keyword"); // `const` got a token span
    expect(out).toContain("hljs-number");
  });

  it("renders unregistered languages as plain escaped code", () => {
    const out = html("```brainfuck\n<+>\n```");
    expect(out).toContain('class="hljs"');
    expect(out).not.toContain("hljs-keyword");
    expect(out).toContain("&lt;+&gt;"); // escaped, not interpreted as HTML
  });

  it("sanitizes script injection", () => {
    const out = html('hello <script>alert(1)</script> <img src=x onerror="x">');
    expect(out).not.toContain("<script>");
    expect(out).not.toContain("onerror");
  });
});

it("tags mermaid fences for client-side rendering instead of highlighting", () => {
  const out = html("```mermaid\nflowchart LR\n  A[App] <--> B[Server]\n```");
  expect(out).toContain('class="rd-mermaid"');
  expect(out).toContain("A[App] &lt;--&gt; B[Server]"); // escaped, still readable
  expect(out).not.toContain("hljs-");
});
