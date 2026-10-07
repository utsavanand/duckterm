import { StrictMode } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemorySwitchPanel } from "./MemorySwitchPanel";
import { memorySwitchService, type MemoryPreparation } from "./memorySwitchTransport";
import type { SwitchSelection } from "./memorySwitchState";

vi.mock("./memorySwitchTransport", async original => ({ ...await original<typeof import("./memorySwitchTransport")>(), memorySwitchService: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const selection: SwitchSelection = {sessionRef:"qa",sourceHarness:"claude-code",sourceGeneration:"g",harness:"codex",model:{mode:"default"}};
const preparing: MemoryPreparation = {preparationId:"shared-job",sequence:0,result:{phase:"preparing"},coverage:{state:"unknown",available_text:"not_processed",retrieval:"unavailable",retention:"unknown",source_count:0,covered_source_count:0,gap_count:0,gaps:[],has_more:false,details_cursor:null}};
const props = {selection,targetName:"Codex",allowed:true,afterTurn:false,canInterrupt:false,close:vi.fn(),onBusy:vi.fn(),onAccepted:vi.fn()};

it("keeps one live dialog lease after StrictMode effect replay", async () => {
  const leases = new Set<string>();
  const service = {prepare:vi.fn(async (key:string) => { leases.add(key);return preparing; }),
    preparation:vi.fn(async () => preparing),details:vi.fn(),release:vi.fn(async (_id:string,key:string) => {leases.delete(key);}),switch:vi.fn(),operation:vi.fn(),cancel:vi.fn()};
  vi.mocked(memorySwitchService).mockReturnValue(service);
  const view = render(<StrictMode><MemorySwitchPanel {...props} /></StrictMode>);
  await act(async () => { await Promise.resolve(); });
  expect(service.prepare).toHaveBeenCalledTimes(2);
  expect(leases.size).toBe(1);
  view.unmount();
  await act(async () => { await Promise.resolve(); });
  expect(leases.size).toBe(0);
  expect(service.switch).not.toHaveBeenCalled();
});

it("does not retain a lease whose preparation finishes after its dialog unmounts", async () => {
  const leases = new Set<string>();
  let finish!: () => void;
  const service = {prepare:vi.fn((key:string) => new Promise<MemoryPreparation>(resolve => {finish=()=>{leases.add(key);resolve(preparing);};})),preparation:vi.fn(),details:vi.fn(),
    release:vi.fn(async (_id:string,key:string)=>{leases.delete(key);}),switch:vi.fn(),operation:vi.fn(),cancel:vi.fn()};
  vi.mocked(memorySwitchService).mockReturnValue(service);
  const view=render(<MemorySwitchPanel {...props} />);
  view.unmount();
  await act(async()=>{finish();});
  expect(leases.size).toBe(0);
  expect(service.release).toHaveBeenCalledOnce();
  expect(service.switch).not.toHaveBeenCalled();
});
