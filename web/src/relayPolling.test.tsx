import {act,cleanup,renderHook} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {api,RelayState} from './api';
import {useRelay,useRelayCount} from './relay';
vi.mock('./api',()=>({api:{relay:vi.fn(),relayCount:vi.fn()}}));
const fresh:RelayState={notes:[],rules:[],open:7};
beforeEach(()=>{vi.useFakeTimers();Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});});
afterEach(()=>{cleanup();vi.useRealTimers();vi.resetAllMocks();});
it('both loops abort at ten seconds and ignore late stale completions',async()=>{
 let lateRelay!:(x:RelayState)=>void,lateCount!:(x:{open:number})=>void;
 vi.mocked(api.relay).mockReturnValueOnce(new Promise(r=>lateRelay=r)).mockResolvedValue(fresh);
 vi.mocked(api.relayCount).mockReturnValueOnce(new Promise(r=>lateCount=r)).mockResolvedValue({open:7});
 const view=renderHook(()=>({relay:useRelay(),count:useRelayCount()}));
 await act(async()=>{await vi.advanceTimersByTimeAsync(9999)});
 expect(vi.mocked(api.relay).mock.calls[0][0]?.aborted).toBe(false);
 await act(async()=>{await vi.advanceTimersByTimeAsync(1)});
 expect(vi.mocked(api.relay).mock.calls[0][0]?.aborted).toBe(true);
 expect(vi.mocked(api.relayCount).mock.calls[0][0]?.aborted).toBe(true);
 await act(async()=>{await vi.advanceTimersByTimeAsync(5000)});
 expect(view.result.current.relay.open).toBe(7);expect(view.result.current.count).toBe(7);
 await act(async()=>{lateRelay({...fresh,open:1});lateCount({open:1});});
 expect(view.result.current.relay.open).toBe(7);expect(view.result.current.count).toBe(7);
});
it('slow successful polls do not overlap periodic intervals',async()=>{
 vi.mocked(api.relay).mockImplementation(()=>new Promise(resolve=>setTimeout(()=>resolve(fresh),9000)));
 renderHook(()=>useRelay());
 await act(async()=>{await vi.advanceTimersByTimeAsync(12000)});
 expect(api.relay).toHaveBeenCalledTimes(1);
 await act(async()=>{await vi.advanceTimersByTimeAsync(1000)});
 expect(api.relay).toHaveBeenCalledTimes(2);
});
it('hidden updates appear on focus without reload',async()=>{
 vi.mocked(api.relay).mockResolvedValueOnce({...fresh,open:0}).mockResolvedValue(fresh);
 const view=renderHook(()=>useRelay());
 await act(async()=>{await vi.advanceTimersByTimeAsync(0)});
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});
 await act(async()=>{window.dispatchEvent(new Event('focus'));});
 expect(api.relay).toHaveBeenCalledTimes(1);
 Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});
 await act(async()=>{window.dispatchEvent(new Event('focus'));});
 expect(view.result.current.open).toBe(7);
});
it('focus and visibility bursts coalesce instead of restarting every request',async()=>{
 vi.mocked(api.relay).mockReturnValue(new Promise(()=>{}));
 renderHook(()=>useRelay());
 await act(async()=>{
  for(let i=0;i<20;i++){window.dispatchEvent(new Event('blur'));window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));}
 });
 expect(vi.mocked(api.relay).mock.calls.length).toBeLessThanOrEqual(2);
});

it('rapid focus cycles also coalesce when responses settle immediately',async()=>{
 vi.mocked(api.relay).mockResolvedValue(fresh);
 renderHook(()=>useRelay());
 await act(async()=>{await vi.advanceTimersByTimeAsync(0)});
 for(let i=0;i<20;i++){
  await act(async()=>{window.dispatchEvent(new Event('blur'));window.dispatchEvent(new Event('focus'));});
 }
 expect(vi.mocked(api.relay).mock.calls.length).toBeLessThanOrEqual(2);
 await act(async()=>{await vi.advanceTimersByTimeAsync(1000);window.dispatchEvent(new Event('focus'));});
 expect(api.relay).toHaveBeenCalledTimes(3);
});
