const $ = id => document.getElementById(id);
let eventCache = [];

async function api(url, options={}) {
  const response = await fetch(url, {headers:{"Content-Type":"application/json"}, ...options});
  const data = await response.json().catch(()=>({error:"Unexpected server response"}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function esc(s){return String(s ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function say(message, bad=false){$("portMessage").textContent=message;$("portMessage").style.color=bad?"#ff9eac":"#a7d7ff";}
async function loadPorts(){
  const box=$("portList"); box.innerHTML='<div class="muted">Searching for serial ports…</div>';
  try{
    const data=await api("/api/ports");
    if(!data.ports.length){box.innerHTML='<div class="muted">No serial ports detected. Connect the device and click Refresh ports.</div>';return;}
    box.innerHTML=data.ports.map(p=>{
      const selected=data.selected.includes(p.device);
      const connected=data.connected.includes(p.device);
      const detail=[p.description,p.manufacturer?`Manufacturer: ${p.manufacturer}`:"",p.vid?`VID:PID ${p.vid}:${p.pid}`:""].filter(Boolean).join(" · ");
      return `<label class="port-option"><input type="checkbox" value="${esc(p.device)}" ${selected?"checked":""}><div><div class="port-name">${esc(p.device)}</div><div class="port-desc">${esc(detail)}</div><span class="port-state ${connected?"":"off"}">${connected?"CONNECTED":selected?"SELECTED":"AVAILABLE"}</span></div></label>`;
    }).join("");
  }catch(e){box.innerHTML=`<div class="muted">Could not enumerate ports: ${esc(e.message)}</div>`;}
}
async function connectSelected(){
  const ports=[...document.querySelectorAll('#portList input[type=checkbox]:checked')].map(x=>x.value);
  try{
    const d=await api("/api/ports/connect",{method:"POST",body:JSON.stringify({ports})});
    say(d.errors?.length ? d.errors.join(" | ") : `Selected ${d.selected.length} port(s). Connected: ${d.connected.join(", ") || "none yet"}`,!!d.errors?.length);
    await loadPorts(); await refreshStatus();
  }catch(e){say(e.message,true);}
}
async function disconnectAll(){
  try{await api("/api/ports/disconnect",{method:"POST",body:"{}"});say("All selected serial ports disconnected.");await loadPorts();await refreshStatus();}
  catch(e){say(e.message,true);}
}
function renderRecords(records){
  const body=$("recordsBody");
  if(!records.length){body.innerHTML='<tr><td colspan="8" class="muted">No records yet. Connect an Arduino or insert a demo record.</td></tr>';return;}
  body.innerHTML=records.map(r=>`<tr><td>${r.data_id}</td><td>${r.module_id}</td><td>${esc(r.source_port||"—")}</td><td>${esc(JSON.stringify(r.values))}</td><td><span class="badge ${esc(r.priority)}">${esc(r.priority)}</span></td><td><span class="badge">${esc(r.status)}</span></td><td>${r.attempt_count}</td><td>${esc(r.received_at)}</td></tr>`).join("");
}
async function refreshStatus(){
  try{
    const d=await api("/api/status");
    $("totalRecords").textContent=d.records_received;
    $("queueDepth").textContent=d.queue_depth;
    $("workers").textContent=d.workers;
    $("connectedCount").textContent=d.connected_ports.length;
    $("highCount").textContent=d.priority_counts.HIGH||0;
    $("mediumCount").textContent=d.priority_counts.MEDIUM||0;
    $("lowCount").textContent=d.priority_counts.LOW||0;
    renderRecords(d.recent_records);
    const health=await api("/api/health");
    $("dbStatus").textContent="Database connected";$("dbDot").className="dot ok";
    const newEvents=d.statuses||[];
    $("eventLog").innerHTML=newEvents.length?newEvents.map(x=>`<div class="event"><time>${esc(x.time)}</time>${esc(x.message)}</div>`).join(""):'<div class="muted">No system events yet.</div>';
  }catch(e){$("dbStatus").textContent="Database unavailable";$("dbDot").className="dot bad";}
}
$("refreshPorts").addEventListener("click",loadPorts);
$("connectBtn").addEventListener("click",connectSelected);
$("disconnectBtn").addEventListener("click",disconnectAll);
$("refreshData").addEventListener("click",refreshStatus);
$("demoBtn").addEventListener("click",async()=>{
  try{await api("/api/demo-record",{method:"POST",body:JSON.stringify({module_id:101,values:[28.4,64.9,554,28.5]})});say("Demo record inserted and queued.");await refreshStatus();}
  catch(e){say(e.message,true);}
});
loadPorts();refreshStatus();setInterval(refreshStatus,2500);setInterval(loadPorts,8000);
