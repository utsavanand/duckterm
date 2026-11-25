import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { HighlightName, matchesAgentName } from "./AgentNameSearch";

afterEach(cleanup);

it.each([
  ["A [qa]+.* B", "[qa]+.*", ["[qa]+.*"]],
  ["CAFÉ 東京", "café", ["CAFÉ"]],
  ["Agent 😺", "😺", ["😺"]],
  ["İx", "x", ["x"]],
  ["İx", "İ", ["İ"]],
  ["İxİx", "x", ["x", "x"]],
  ["ΟΣ", "ος", ["ΟΣ"]],
  ["İİİ", "\u0307i", ["İİİ"]],
])("preserves literal name and highlights original characters: %s / %s", (name, query, marks) => {
  expect(matchesAgentName(name, query)).toBe(true);
  const { container } = render(<HighlightName name={name} query={query} />);
  expect(container.textContent).toBe(name);
  expect([...container.querySelectorAll("mark")].map(mark => mark.textContent)).toEqual(marks);
});
