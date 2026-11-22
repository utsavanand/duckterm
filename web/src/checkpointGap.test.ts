import { expect, it } from "vitest";
import type { CheckpointRecord } from "./api";
import { checkpointCanRetry, checkpointStatus } from "./checkpointState";

it("names a gap entry and never treats it as a ready checkpoint", () => {
  const gap = { periods: [[1, 2]] as [number, number][], first_at: 1, last_at: 2 };
  const one = { saved: true, record: { gap: { ...gap, records: 1 } } } as unknown as Partial<CheckpointRecord>;
  const many = { saved: true, record: { gap: { ...gap, records: 1312 } } } as unknown as Partial<CheckpointRecord>;
  expect(checkpointStatus(one)).toEqual({ label: "1 message not summarized", handoff: "Checked in Restart", ready: false });
  expect(checkpointStatus(many).label).toBe("1,312 messages not summarized");
});

it("offers no summary retry for a gap", () => {
  const cp = { saved: true, record: { gap: { periods: [[1, 2]], records: 3, first_at: 1, last_at: 2 } } } as unknown as Partial<CheckpointRecord>;
  expect(checkpointCanRetry(cp)).toBe(false);
});
