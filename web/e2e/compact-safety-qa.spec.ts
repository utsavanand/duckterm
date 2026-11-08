import {expect,test} from '@playwright/test';
import {mkdtempSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {api,apiDelete,apiPost,base} from './helpers';
for(const runtime of ['codex','claude-code']) test(`Compact idle submission and busy/approval safety (${runtime})`,async({page})=>{
 const dir=mkdtempSync(join(tmpdir(),'compact-qa-')); const script=join(dir,'agent.py'), journal=join(dir,'receipts.jsonl'), bytes=join(dir,'input.bin'); let key='';
 writeFileSync(journal,'');writeFileSync(bytes,'');
 writeFileSync(script,`import os,tty,json\ntty.setraw(0)\nprint('QA_READY',flush=True)\nbuf=b''\nwhile True:\n b=os.read(0,1)\n with open(${JSON.stringify(bytes)},'ab') as f:f.write(b)\n if b==b'\\r':\n  text=buf.replace(b'\\x1b[200~',b'').replace(b'\\x1b[201~',b'')\n  with open(${JSON.stringify(journal)},'a') as f:f.write(json.dumps(text.decode())+'\\n')\n  buf=b''\n else:buf+=b\n`);
 let nativeId: string | undefined, generation: string | undefined;
 const event = async (fields: Record<string,unknown>) => { const result=await apiPost('/events',{...fields,session_key:key,session_id:nativeId,launch_generation:generation,runtime,test:true});expect(result.status).toBe(200);expect(result.body).not.toHaveProperty('dropped'); };
 const receipts=()=>readFileSync(journal,'utf8').trim().split('\n').filter(Boolean).map(s=>JSON.parse(s));
 try{
 const launch=await apiPost('/sessions/launch',{command:`python3 -u ${script}`,cwd:dir,name:`QA compact ${runtime}`,runtime,test:true,in_terminal:false});expect(launch.status).toBe(200);key=String(launch.body.session_key);
 const listing=await (await api('/sessions')).json();const binding=JSON.parse(listing.sessions.find((s: {session_key:string})=>s.session_key===key).restart_json || '{}').native_binding;nativeId=binding?.native_id;generation=binding?.generation;
 await page.goto(base());await page.locator('.rd-row-name',{hasText:`QA compact ${runtime}`}).click();await expect(page.locator('.rd-terminal-slot:visible .xterm-rows')).toContainText('QA_READY');
 const context=page.locator('details',{has:page.locator('summary',{hasText:'Active context'})});if(await context.getAttribute('open')===null)await context.locator('summary').click();
 const button=context.getByRole('button',{name:'Compact',exact:true});
 // The synthetic agent is initially busy. Disabled clicks must send no bytes.
 await expect(button).toBeDisabled();await button.dispatchEvent('click');expect(readFileSync(bytes).length).toBe(0);
 await event({event_type:'Notification',notification_type:'idle_prompt'});await expect(button).toBeEnabled();await button.click();
 await expect.poll(receipts).toEqual(['/compact']);await expect(button).toBeEnabled();
 // An empty Enter yields an empty receipt, proving no command tail remains.
 expect((await apiPost(`/sessions/${key}/input`,{text:'\r'})).status).toBe(200);await expect.poll(receipts).toEqual(['/compact','']);
 for(const transition of [{event_type:'UserPromptSubmit',prompt:'synthetic busy turn'},{event_type:'PermissionRequest',tool_name:'Bash',tool_input:{command:'synthetic approval'}}]){
 await event(transition);await expect(button).toBeDisabled();const before=readFileSync(bytes);
 await button.dispatchEvent('click');
 // Even a stale UI or direct request must not type into a busy/approval prompt.
 const rejected=await apiPost(`/sessions/${key}/input`,{text:'/compact',submit:true});expect(rejected.status).toBe(409);
 await page.waitForTimeout(250);expect(readFileSync(bytes)).toEqual(before);expect(receipts()).toEqual(['/compact','']);
 }
 }finally{if(key)await apiDelete(`/sessions/${key}`);rmSync(dir,{recursive:true,force:true})}
});
