import { describe, expect, it } from "vitest";
import { clearHighlights, highlightAnnotations, locatedAnnotations } from "./annotationHighlights";
const note = (id: string, quote: string, text = "Explain this") => ({ id, quote, note: text, created_at: 1 });

describe("saved comment highlighting", () => {
  it("spans inline markup without damaging links and handles overlapping notes", () => {
    const root = document.createElement("div");
    root.innerHTML = '<p>Use <strong>safe <a href="/docs">text</a></strong> here.</p>';
    const notes = [note("a", "Use safe text"), note("b", "text here")];
    highlightAnnotations(root, notes);
    expect(root.textContent).toBe("Use safe text here.");
    expect(root.querySelector("a")?.getAttribute("href")).toBe("/docs");
    expect(root.querySelector<HTMLElement>("a mark")?.dataset.annotationIds).toBe('["a","b"]');
    expect(root.querySelector("strong mark")?.textContent).toBe("safe ");
    clearHighlights(root);
    expect(root.innerHTML).toBe('<p>Use <strong>safe <a href="/docs">text</a></strong> here.</p>');
  });
  it("marks every occurrence, normalizes whitespace, and can be reapplied without nesting", () => {
    const root = document.createElement("div"); root.textContent = "one  word and one\nword";
    const notes = [note("a", "one word")];
    highlightAnnotations(root, notes); highlightAnnotations(root, notes);
    expect(root.querySelectorAll("mark")).toHaveLength(2);
    expect(root.querySelectorAll("mark mark")).toHaveLength(0);
    expect(root.textContent).toBe("one  word and one\nword");
  });
  it("never parses quotes or notes as markup, and excludes generated diagram content", () => {
    const root = document.createElement("div");
    const malicious = '<img src=x onerror="alert(1)">'; root.textContent = malicious;
    highlightAnnotations(root, [note("a", malicious, '<script>alert(1)</script>')]);
    expect(root.querySelector("img, script")).toBeNull();
    expect(root.querySelector("mark")?.textContent).toBe(malicious);
    expect(root.querySelector("mark")?.getAttribute("aria-label")).toContain('<script>alert(1)</script>');
    root.innerHTML = '<pre class="rd-mermaid">graph TD</pre><p>visible</p>';
    expect([...locatedAnnotations(root, [note("a", "graph TD"), note("b", "visible"), note("c", "gone")])]).toEqual(["b"]);
  });
});
