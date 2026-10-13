import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, type Page } from "@playwright/test";
import { readOwnedState } from "./run-state";
import { seedSession } from "./helpers";

// Native history belongs only to the disposable browser-test home. No provider
// credentials or real conversations are used; global teardown removes the files.
export async function seedCheckpointSession(key: string, fields: Record<string, unknown> = {}, count = 3) {
  const directory = join(readOwnedState().home, "test-claude", "projects", "checkpoint-fixtures");
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, key + "-native.jsonl"), Array.from({ length: count }, (_, i) => JSON.stringify({
    uuid: `${key}-message-${i}`, type: "user", message: { role: "user", content: `Owner requirement ${i}: preserve the selected session and its evidence.` },
  })).join("\n") + "\n");
  await seedSession(key, { ...fields, session_id: key + "-native" });
}

export async function confirmCheckpoint(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Checkpoint", exact: true });
  await expect(dialog).toBeVisible();
  const submit = dialog.getByRole("button", { name: /^Checkpoint \d[\d,]* records$/ });
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(dialog).toHaveCount(0);
}
