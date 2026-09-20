import http from "node:http";
import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const PAGE = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hermes sessions</title>
<style>body{font:16px system-ui;color:#22312d;background:#f3f5f2;margin:0;padding:40px 22px}main{max-width:700px;margin:auto}h1{font-size:32px;letter-spacing:-1px}p{line-height:1.6;color:#54645e}section{background:white;border:1px solid #d8e0db;border-radius:16px;padding:24px;margin:20px 0}label{display:block;font-weight:600;margin:20px 0 8px}select,input,button{box-sizing:border-box;font:inherit;border:1px solid #c2cfc7;border-radius:8px;padding:12px;width:100%;background:white}button{background:#20563e;color:white;border:0;cursor:pointer;margin-top:22px}button:disabled{opacity:.5;cursor:wait}#status{font-size:14px}#result{white-space:pre-wrap;overflow-wrap:anywhere}small{display:block;line-height:1.5;color:#637169;margin-top:12px}a{color:#20563e}</style>
<main><div>CODEX · HERMES</div><h1>Your Hermes conversations, here.</h1><p>Select <b>Hermes (VPS)</b> in the Codex model picker. A new task creates its own Hermes session when you send its first message. To continue an existing conversation, attach it below.</p>
<section><div id="status" role="status">Connecting…</div><label for="task">Codex task</label><select id="task"></select><label for="session">Hermes session</label><select id="session"><option value="">Choose a session…</option></select><button id="attach" disabled>Attach this session</button><small>Attachment changes the selected task’s destination. It does not send a message to Hermes or import old messages into Codex.</small></section>
<section><strong>Current connection</strong><p id="mapping">Choose a task.</p><div id="result" role="status"></div><small>Hermes runs on the VPS. Generated files in its configured output folders can be returned to this Mac. If a turn is interrupted, its remote outcome must be checked before sending it again.</small></section></main>
<script>
const token=new URLSearchParams(location.hash.slice(1)).get('token')||'';
history.replaceState(null,'',location.pathname+location.search);
const el=id=>document.getElementById(id); let mappings={};
async function api(route,body){const r=await fetch(route,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
function option(select,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;select.append(o);}
function mapping(){const m=mappings[el('task').value];el('mapping').textContent=m?'Hermes session: '+m.hermesSessionId:'No session attached yet. The first message will create one.';el('attach').disabled=!el('task').value||!el('session').value;}
async function load(){try{const d=await api('/api/state');mappings=d.mappings;el('status').textContent=d.connected?'Connected to Hermes on the VPS':'Hermes connection unavailable';el('task').replaceChildren();for(const t of d.tasks)option(el('task'),t.id,t.title||t.id);const wanted=new URLSearchParams(location.search).get('task');if(wanted)el('task').value=wanted;for(const s of d.sessions)option(el('session'),s.id,(s.title||'Untitled')+' · '+s.id);mapping();}catch(e){el('status').textContent=e.message;}}
el('task').onchange=mapping;el('session').onchange=mapping;
el('attach').onclick=async()=>{el('attach').disabled=true;el('result').textContent='Attaching…';try{await api('/api/attach',{taskId:el('task').value,sessionId:el('session').value});mappings[el('task').value]={hermesSessionId:el('session').value};mapping();el('result').textContent='Attached. Return to that Codex task, select Hermes (VPS), and send your next message.';}catch(e){el('result').textContent=e.message;mapping();}};
load();</script></html>`;

async function readJson(file, fallback) {
  try { return JSON.parse(await readFile(file, "utf8")); }
  catch (error) { if (error.code === "ENOENT") return fallback; throw error; }
}

export function createPickerUiServer({ adapter, token, codexHome = process.env.CODEX_HOME || path.join(os.homedir(), ".codex") }) {
  const json = (res, status, data) => { res.writeHead(status, {"Content-Type":"application/json", "Cache-Control":"no-store"}); res.end(JSON.stringify(data)); };
  return http.createServer(async (req, res) => {
    const expectedHost = `127.0.0.1:${req.socket.localPort}`;
    if (req.headers.host !== expectedHost || (req.headers.origin && req.headers.origin !== `http://${expectedHost}`)) return json(res,403,{error:"Invalid local origin"});
    res.setHeader("X-Content-Type-Options", "nosniff");
    res.setHeader("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
    const url = new URL(req.url, `http://${expectedHost}`);
    if (req.method === "GET" && url.pathname === "/") { res.writeHead(200,{"Content-Type":"text/html; charset=utf-8", "Cache-Control":"no-store"}); res.end(PAGE); return; }
    const supplied = Buffer.from(req.headers.authorization || "");
    const expected = Buffer.from(`Bearer ${token}`);
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) return json(res,401,{error:"Reopen the session chooser from Codex to connect."});
    try {
      if (req.method === "GET" && url.pathname === "/api/state") {
        const health = await adapter.client.status();
        const remote = await adapter.client.request("/api/sessions?limit=50");
        const sessions = (remote.sessions || remote.data || []).map(s=>({id:s.id,title:s.title||""})).filter(s=>s.id);
        const store = await readJson(adapter.mappingPath, {tasks:{}});
        const mappings = Object.fromEntries(Object.entries(store.tasks || {}).map(([id,m])=>[id,{hermesSessionId:m.hermesSessionId}]));
        let tasks = [];
        try {
          const lines = (await readFile(path.join(codexHome,"session_index.jsonl"),"utf8")).trim().split("\n").slice(-100);
          const unique = new Map();
          for (const line of lines) { try { const t=JSON.parse(line); if(t.id) unique.set(t.id,{id:t.id,title:t.thread_name||t.title||t.id}); } catch {} }
          tasks = [...unique.values()].reverse();
        } catch(error) { if(error.code!=="ENOENT") throw error; }
        for (const id of Object.keys(mappings)) if(!tasks.some(t=>t.id===id)) tasks.push({id,title:id});
        return json(res,200,{connected:health.compatible,tasks,mappings,sessions});
      }
      if (req.method === "POST" && url.pathname === "/api/attach") {
        let raw="";
        for await(const chunk of req) {raw+=chunk;if(raw.length>4096)return json(res,413,{error:"Request too large"});}
        const data=JSON.parse(raw);
        if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(data.taskId||"") || !/^[A-Za-z0-9_.:@/-]{1,256}$/.test(data.sessionId||"")) return json(res,400,{error:"Choose a valid task and session"});
        const chunks=[];
        const sink={statusCode:200,headersSent:false,setHeader(){},writeHead(status){this.statusCode=status;this.headersSent=true;},write(chunk){chunks.push(chunk);return true;},end(chunk){if(chunk)chunks.push(chunk);this.writableEnded=true;}};
        await adapter.handleResponses({request:{headers:{"thread-id":data.taskId,"idempotency-key":randomUUID()}},response:sink,payload:{model:"hermes-vps/agent",stream:false,input:[{type:"message",role:"user",content:[{type:"input_text",text:"/hermes attach "+data.sessionId}]}]}});
        if(sink.statusCode>=400) {let message="Could not attach session";try{message=JSON.parse(chunks.join("")).error.message;}catch{}return json(res,sink.statusCode,{error:message});}
        return json(res,200,{attached:true});
      }
      json(res,404,{error:"Not found"});
    } catch { json(res,502,{error:"Hermes connection failed. Check the VPS connection and try again."}); }
  });
}

let singleton;
export async function startPickerUi({adapter, port=8879, runtimeDir=path.join(os.homedir(),".codex","hermes-picker")}={}) {
  if(singleton) return singleton;
  const token=randomBytes(32).toString("hex");
  const server=createPickerUiServer({adapter,token});
  await new Promise((resolve,reject)=>{server.once("error",reject);server.listen(port,"127.0.0.1",resolve);});
  server.unref();
  const url=`http://127.0.0.1:${server.address().port}/#token=${token}`;
  await mkdir(runtimeDir,{recursive:true,mode:0o700});
  await writeFile(path.join(runtimeDir,"ui.json"),JSON.stringify({url,pid:process.pid}),{mode:0o600});
  singleton={server,url};
  return singleton;
}
