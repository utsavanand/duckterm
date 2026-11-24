import { expect, it } from "vitest";
import { dollars, money, priceOf } from "./pricing";

it("matches the most specific model before its family", () => {
  expect(priceOf("claude-opus-5-5", "claude-code").input).toBe(4);
  expect(priceOf("claude-opus-5", "claude-code").input).toBe(5);
  expect(priceOf("claude-fable-5-1", "claude-code").cacheRead).toBe(0.25);
  expect(priceOf("claude-fable-5", "claude-code").cacheRead).toBe(1);
  expect(priceOf("gpt-6-astra", "codex").output).toBe(10);
  expect(priceOf("codex-auto-review", "codex").input).toBe(1.25);
  expect(priceOf("something-new", "codex").input).toBe(1.25);
});

it("charges cache writes at 1.25x input and cache reads at the read rate", () => {
  const row = { day: "d", model: "claude-opus-5-5", agent: "claude-code", session: "s", session_name: "s", folder: "", folder_name: "", input: 1e6, cache_write: 1e6, cache_read: 1e6, output: 1e6 };
  expect(dollars(row)).toBeCloseTo(4 + 5 + 0.2 + 20);
  expect(money(1234.5)).toBe("$1,235");
  expect(money(3.456)).toBe("$3.46");
});
