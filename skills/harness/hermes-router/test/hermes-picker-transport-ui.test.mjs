import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, symlink, rm } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import os from "node:os";
import { createPickerUiServer } from "../src/hermes-picker-ui.mjs";

async function helper(payload) {
  return await new Promise((resolve,reject)=>{
    const child=spawn("python3",[new URL("../src/hermes-picker-remote.py",import.meta.url).pathname],{stdio:["pipe","pipe","pipe"]});
    let output="";child.stdout.on("data",d=>output+=d);child.on("error",reject);child.on("close",code=>{try{assert.equal(code,0);resolve(JSON.parse(output));}catch(e){reject(e);}});child.stdin.end(JSON.stringify(payload));
  });
}

test("artifact transport preserves hostile-looking names as data and rejects escape/hidden/oversize",async t=>{
  const root=await mkdtemp(path.join(os.tmpdir(),"hermes-artifact-"));t.after(()=>rm(root,{recursive:true,force:true}));
  const outputs=path.join(root,"outputs");await mkdir(outputs);
  const name=path.join(outputs,"report; echo injected.txt");await writeFile(name,"actual bytes");
  const good=await helper({action:"file",path:name,roots:[outputs],maxBytes:100});
  assert.equal(good.status,200);assert.equal(Buffer.from(good.data.base64,"base64").toString(),"actual bytes");
  const secret=path.join(root,"outside.txt");await writeFile(secret,"outside");
  await symlink(secret,path.join(outputs,"link.txt"));
  await writeFile(path.join(outputs,".hidden"),"hidden");
  for(const file of [secret,path.join(outputs,"link.txt"),path.join(outputs,".hidden")])assert.equal((await helper({action:"file",path:file,roots:[outputs],maxBytes:100})).status,500);
  assert.equal((await helper({action:"file",path:name,roots:[outputs],maxBytes:3})).status,500);
});

test("session chooser requires capability, rejects foreign origins and attaches only explicit selection",async t=>{
  const root=await mkdtemp(path.join(os.tmpdir(),"hermes-ui-"));t.after(()=>rm(root,{recursive:true,force:true}));
  const id="11111111-1111-4111-8111-111111111111";
  const mappingPath=path.join(root,"map.json");await writeFile(mappingPath,JSON.stringify({tasks:{[id]:{hermesSessionId:"s1",lastContent:"PRIVATE REPLY"}}}));
  await writeFile(path.join(root,"session_index.jsonl"),JSON.stringify({id,thread_name:"Test task"})+"\n");
  const calls=[];
  const adapter={mappingPath,client:{status:async()=>({compatible:true}),request:async route=>({sessions:[{id:"s1",title:"Canary",messages:["PRIVATE HISTORY"]}]})},handleResponses:async p=>{calls.push(p);p.response.end(JSON.stringify({ok:true}));}};
  const server=createPickerUiServer({adapter,token:"test-capability",codexHome:root});
  await new Promise(r=>server.listen(0,"127.0.0.1",r));t.after(()=>new Promise(r=>server.close(r)));
  const base=`http://127.0.0.1:${server.address().port}`;const auth={Authorization:"Bearer test-capability"};
  assert.equal((await fetch(base+"/api/state")).status,401);
  assert.equal((await fetch(base+"/api/state",{headers:{...auth,Origin:"https://example.com"}})).status,403);
  const state=await (await fetch(base+"/api/state",{headers:auth})).json();
  assert.equal(state.connected,true);assert.equal(state.tasks[0].title,"Test task");assert.ok(!JSON.stringify(state).includes("PRIVATE"));
  const result=await fetch(base+"/api/attach",{method:"POST",headers:{...auth,"Content-Type":"application/json"},body:JSON.stringify({taskId:id,sessionId:"s1"})});
  assert.equal(result.status,200);assert.equal(calls.length,1);assert.equal(calls[0].payload.input[0].content[0].text,"/hermes attach s1");
  assert.equal((await fetch(base+"/api/attach",{method:"POST",headers:auth,body:JSON.stringify({taskId:"bad",sessionId:"s1"})})).status,400);
});
