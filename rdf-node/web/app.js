'use strict';
const $=id=>document.getElementById(id);
function readShutdownUncertainty(){try{return localStorage.getItem('rdf-node-edge-shutdown-uncertain')==='1';}catch{return false;}}
function setShutdownUncertainty(value){shutdownUncertain=value;try{if(value)localStorage.setItem('rdf-node-edge-shutdown-uncertain','1');else localStorage.removeItem('rdf-node-edge-shutdown-uncertain');}catch{}}
let snapshot={}, csrf=null, authenticated=false, lastSeq=null, lastProgress=0, lastApi=0, linkData=false, hostPage=false, lastTouch=Date.now(), lastAdminSessionRefresh=0, adminSessionRefreshPending=false, pinKeyHandler=null, modalReturnFocus=null, mqttKeyboardDismiss=null, pollPending=false, pollAgain=false, pollTimer=null, shutdownUncertain=readShutdownUncertainty(), shutdownHistoryChecked=false, shutdownHistoryAt=0, shutdownHistoryRequest=null;
const good=['UP','CONNECTED','HEALTHY','SYNCED','APPLIED','REPLY','PRESENT'];
const bad=['ERROR','LOST','DEGRADED','FAILED','UNAVAILABLE'];
const ADMIN_SESSION_REFRESH_MS=20000, ADMIN_SESSION_ACTIVITY_MS=30000, ADMIN_SESSION_CHECK_MS=10000;
const pppProbeNames={UNKNOWN:'BELUM DICEK',NO_INTERFACE:'TANPA INTERFACE',REPLY:'BALASAN',NO_REPLY:'TANPA BALASAN',ERROR:'ERROR PROBE'};
function label(id,text,code){const el=$(id);if(!el)return;el.textContent=text;el.classList.remove('good','warn','bad','neutral');el.classList.add(good.includes(code)?'good':bad.includes(code)?'bad':code?'warn':'neutral');}
function fmt(v,dec=1){return typeof v==='number'&&Number.isFinite(v)?v.toFixed(dec):'--';}
function age(ms){return typeof ms==='number'?`${fmt(ms/1000)} dtk`:'--';}
function row(label,value,code){const r=document.createElement('div');r.className='row';const l=document.createElement('label');l.textContent=label;const v=document.createElement('b');v.textContent=String(value??'--');if(code)v.className=good.includes(code)?'good':bad.includes(code)?'bad':'warn';r.append(l,v);return r;}
function rows(id,values){const e=$(id);e.replaceChildren(...values.map(v=>row(...v)));}
const mqttTopics=[
 ['KELUAR / RASPBERRY -> GROUND',[
  ['availability','QoS 1 / retained','Online JSON: v,sid,online,t; offline LWT: v,sid,online,reason.'],
  ['capabilities','QoS 1 / retained','Payload includes local gates (config_patch,processing,restart,reboot,shutdown,ppp_restart) and Ground availability (remote_commands,remote_config_patch,remote_processing,remote_restart,remote_reboot,remote_shutdown,remote_ppp_restart).'],
  ['state','QoS 1 / retained','Payload: v,sid,boot,instance,t,run,daq,cfg,profile,clock.'],
  ['settings/reported','QoS 1 / non-retained / request-only','Payload: v,sid,boot,id,rev,t,settings_json; TLS wajib, maks. 8192 byte, expiry 30 dtk.'],
  ['telemetry/health','QoS 0 / ~1 dtk','Payload: v,sid,q,t,run,daq,drop,age,temp,clk,rev; run/DAQ/clock dikirim sebagai kode.'],
  ['telemetry/health/detail','QoS 0 / ~10 dtk','Payload: v,sid,t,usb,sync,cpu,mem,disk_free,throt,uv,tx,rx,adrop,parse.'],
  ['telemetry/doa','QoS 0','Payload: v,sid,q,t,f,a,c,p,rev,ok. f=Hz, a=DoA relatif, c=confidence dB, p=power dB.'],
  ['telemetry/diagnostic/doa','QoS 0 / heartbeat tiap 3 dtk bila XML tersedia','Sampel doa.xml diulang meski tidak berubah; raw angle + source/observation timestamps; UNVERIFIED; bukan deteksi LIVE.'],
  ['telemetry/angular','QoS 0 / JSON','Satu object per pesan, metadata dan tepat 360 nilai source. Flags 63 untuk LIVE; flags parsial untuk UNVERIFIED. Bukan raw IQ.'],
  ['ack/config','QoS 1','Payload ACK: v,sid,id,status,t,rev,result.'],
  ['ack/operation','QoS 1','Payload ACK: v,sid,id,status,t,rev,result.']
 ],true],
 ['MASUK / GROUND -> RASPBERRY',[
  ['cmd/config/get','QoS 1','Minta file settings asli; balasan settings/reported hanya via TLS.'],
  ['cmd/config/patch','QoS 1','Safe-field patch to the fixed settings target; no per-command root approval, but single-writer and digest checks remain.'],
  ['cmd/processing/set','QoS 1','Start/stop the audited SDR unit; no per-command Ground root approval.'],
  ['cmd/service/restart','QoS 1','Restart the audited SDR unit; no per-command Ground root approval.'],
  ['cmd/service/ppp/restart','QoS 1','Restart fixed T900 unit; helper checks loaded/active/no-job per request.'],
  ['cmd/system/reboot/prepare','QoS 1','Ground prepare needs no root grant; execute retains one-use challenge and operator confirmation.'],
  ['cmd/system/reboot/execute','QoS 1','Execute uses one-use challenge; no Ground root grant or per-action approval.'],
  ['cmd/system/shutdown/prepare','QoS 1','Prepare needs no root approval; Ground UI retains operator confirmation.'],
  ['cmd/system/shutdown/execute','QoS 1','Execute retains one-use challenge; Ground needs no root approval. Edge-local shutdown uses persistent root approval and Admin PIN.'],
  ['cmd/operation/get','QoS 1','Permintaan status operation.'],
  ['cmd/stream/set','QoS 1','Perubahan profile Ground langsung tanpa remote grant.']
 ]]
];
let renderedTopicPrefix=null;const topicStatusCells=new Map();
function renderTopicList(){
 const host=$('topiclist');if(!host)return;const fragment=document.createDocumentFragment();topicStatusCells.clear();
 for(const [title,topics,outbound] of mqttTopics){
  const group=document.createElement('section');group.className='topic-group';
  const heading=document.createElement('h3');heading.textContent=title;group.append(heading);
  if(outbound){
   const caption=document.createElement('p');caption.className='sr-only';
   caption.textContent='Topik keluar dari Raspberry ke Ground. QoS 0 berarti write socket lokal tanpa ACK; QoS 1 berarti PUBACK broker, bukan konfirmasi pemrosesan Ground.';
   group.append(caption);
   for(const [suffix,quality,description] of topics){
    const item=document.createElement('details'),summary=document.createElement('summary');
    const nameBlock=document.createElement('span');nameBlock.className='topic-name';
    const name=document.createElement('code');name.textContent=suffix;
    const qos=document.createElement('span');qos.textContent=quality;nameBlock.append(name,qos);
    const delivery=document.createElement('span');delivery.className='delivery-cell';
    const result=document.createElement('span');result.className='delivery-result';
    const dot=document.createElement('span');dot.className='delivery-dot';dot.setAttribute('aria-hidden','true');
    const label=document.createElement('strong'),info=document.createElement('small');
    result.append(dot,label,info);delivery.append(result);
    summary.append(nameBlock,delivery);
    const note=document.createElement('p');note.textContent=description;
    item.append(summary,note);group.append(item);
    topicStatusCells.set(suffix,{cell:delivery,label,info});
   }
  }else{
   for(const [suffix,quality,description] of topics){
    const detail=document.createElement('details');const summary=document.createElement('summary');
    const name=document.createElement('span');name.className='topic-name';
    const code=document.createElement('code');code.textContent=suffix;name.append(code);
    const qos=document.createElement('span');qos.className='topic-qos';qos.textContent=quality;
    summary.append(name,qos);const note=document.createElement('p');note.textContent=description;
    detail.append(summary,note);group.append(detail);
   }
  }
  fragment.append(group);
 }
 host.replaceChildren(fragment);
}
function setTopicDelivery(entry,status){
 const state=status?.state||'NONE',label=entry.label,info=entry.info;
 entry.cell.className=`delivery-cell delivery-cell--${state.toLowerCase()}`;
 if(state==='SENT'){
  label.textContent='TERKIRIM';
  info.textContent=status.qos===1?'PUBACK broker':'Socket lokal';
 }else if(state==='ERROR'){
  label.textContent='GAGAL';info.textContent=status.error||'PUBLISH_FAILED';
 }else if(state==='PENDING'){
  label.textContent='MENUNGGU';info.textContent='Publish MQTT';
 }else{
  label.textContent='BELUM DIKIRIM';info.textContent='Belum ada percobaan';
 }
 const updated=status?.updated_ms;
 entry.cell.title=typeof updated==='number'?`Terakhir diperbarui ${age(Date.now()-updated)} lalu`:'';
 const recency=typeof updated==='number'?`; diperbarui ${age(Date.now()-updated)} lalu`:'';
 entry.cell.setAttribute('aria-label',`${label.textContent}: ${info.textContent}${recency}`);
}
function updateTopicStatuses(statuses){
 for(const [suffix,entry] of topicStatusCells)setTopicDelivery(entry,statuses?.[suffix]);
}
function mqttPairState(l){
 const states=[l.mqtt_control?.state||'DISABLED',l.mqtt_bulk?.state||'DISABLED'];
 return states.includes('ERROR')?'ERROR':states.every(value=>value==='CONNECTED')?'CONNECTED':states.every(value=>value==='DISABLED')?'':'CONNECTING';
}
const SVG_NS='http://www.w3.org/2000/svg';
let needleTurn=null;
function buildDial(){
 const ticks=$('dialticks');if(!ticks)return;
 for(let deg=0;deg<360;deg+=10){
  const major=deg%30===0,theta=deg*Math.PI/180,inner=major?55:59,line=document.createElementNS(SVG_NS,'line');
  line.setAttribute('x1',(inner*Math.sin(theta)).toFixed(2));line.setAttribute('y1',(-inner*Math.cos(theta)).toFixed(2));
  line.setAttribute('x2',(64*Math.sin(theta)).toFixed(2));line.setAttribute('y2',(-64*Math.cos(theta)).toFixed(2));
  line.setAttribute('class',major?'dial-tick major':'dial-tick');ticks.append(line);
 }
}
// Only a gate-valid relative DoA moves the needle. Raw/UNVERIFIED angles use a different
// convention and stay numeric-only so the dial never implies a verified bearing.
function updateDial(angle,unverified){
 const dial=$('dial');if(!dial)return;
 const valid=typeof angle==='number'&&Number.isFinite(angle);
 dial.dataset.state=valid?'valid':unverified?'unverified':'none';
 $('dialnote').textContent=valid?'':unverified?'UNVERIFIED':'TANPA DATA';
 if(!valid){dial.setAttribute('aria-label',unverified?'Dial arah relatif: sudut belum terverifikasi, jarum disembunyikan':'Dial arah relatif: belum ada pengukuran valid');return;}
 const target=((angle%360)+360)%360;
 needleTurn=needleTurn===null?target:needleTurn+((target-needleTurn)%360+540)%360-180;
 $('needle').style.setProperty('--a',`${needleTurn.toFixed(2)}deg`);
 dial.setAttribute('aria-label',`Dial arah relatif ${fmt(target)} derajat, 0 di atas searah jarum jam`);
}
buildDial();
function renderData(s){
 const l=s.link||{};
 const control=l.mqtt_control||{},bulk=l.mqtt_bulk||{};
 label('mqttclients',`${control.state||'DISABLED'} / ${bulk.state||'DISABLED'}`,mqttPairState(l));
 if($('mqttsettings'))$('mqttsettings').textContent=authenticated?'Atur':'Login';
 const node=s.node_id||'--',prefix=`${s.mode==='DEMO'?'sdr/demo/v2':'sdr/v2'}/${node}/`;
 if(prefix!==renderedTopicPrefix){renderedTopicPrefix=prefix;$('topicprefix').textContent=prefix;renderTopicList();}
 updateTopicStatuses(l.mqtt_topic_delivery||{});
}
function render(s){
 const d=s.detection||{},l=s.link||{},h=s.host||{},q=s.daq||{},c=s.config||{},p=s.processing||{};
 const diag=s.diagnostic_doa||{},showDiagnostic=!d.valid&&diag.available;
 const diagnosticReason=Array.isArray(diag.validation_reasons)&&diag.validation_reasons.find(reason=>reason!=='DIAGNOSTIC_UNVERIFIED')||'UNVERIFIED';
 $('mode').textContent=s.mode==='DEMO'?'DEMO':'';
 const names={RUNNING:'RDF BERJALAN',STOPPED:'RDF BERHENTI',STARTING:'MEMULAI RDF',STOPPING:'MENGHENTIKAN',ERROR:'RDF ERROR',UNKNOWN:'RDF UNKNOWN'};
 label('run',names[p.observed]||'MENUNGGU',p.observed==='RUNNING'?'HEALTHY':p.observed);
 label('ppp',l.ppp||'--',l.ppp);label('mqtt',l.mqtt_control?.state||'DISABLED',l.mqtt_control?.state);
 $('angle-label').textContent=showDiagnostic?'DOA RAW / UNVERIFIED':'ARAH RELATIF';
 $('angle').textContent=showDiagnostic?fmt(diag.raw_doa_deg):d.valid?fmt(d.relative_doa_deg):'--';
 updateDial(d.valid?d.relative_doa_deg:null,showDiagnostic);
 $('angle').className=showDiagnostic?'warn':d.valid?'good':'neutral';
 $('age').textContent=showDiagnostic?`doa.xml / umur ${age(diag.source_age_ms)}`:d.valid?`Umur ${age(d.source_age_ms)}`:`${d.state||'MENUNGGU'} / ${age(d.source_age_ms)}`;
 $('frequency-label').textContent=showDiagnostic?'FREKUENSI XML':'FREKUENSI VFO';
 $('freq').textContent=showDiagnostic?fmt(diag.frequency_mhz,3):d.frequency_hz?fmt(d.frequency_hz/1e6,3):'--';
 $('quality').textContent=showDiagnostic?`Gate: ${diagnosticReason}`:`PAPR ${fmt(d.confidence_native_db,2)} dB / P ${fmt(d.power_native_db)} dB`;
 label('daq',q.state==='HEALTHY'?'SINKRON':q.state||'UNKNOWN',q.state);
 label('sync',c.sdr_revision==null?'--':`r${c.sdr_revision}`,'');
 const op=s.last_operation;$('command').textContent=op?`${op.op} / ${op.stage}`:'Belum ada operasi';
 const alerts=s.active_alerts||[];const a=alerts.find(x=>x.severity==='error')||alerts[0];
 $('alert').textContent=a?a.text:'Status lokal normal';$('alertbox').className=`alert ${a?(a.severity==='error'?'bad':'warn'):'good'}`;
 $('alerticon').textContent=a?'!':'\u2713';$('temp').textContent=`${fmt(h.temperature_c)}\u00b0C`;
 if(!linkData){rows('linkrows',[["T900 USB",l.usb,l.usb],["PPP / interface",`${l.ppp||'--'} / ${l.interface||'--'}`,l.ppp],[`Ping ${l.ppp_peer||'peer'}`,pppProbeNames[l.ppp_probe]||'BELUM DICEK',l.ppp_probe],["MQTT CTRL / BULK",`${l.mqtt_control?.state||'--'} / ${l.mqtt_bulk?.state||'--'}`,mqttPairState(l)],["TX / RX (PPP/IP)",`${fmt(l.tx_kbit_s,2)} / ${fmt(l.rx_kbit_s,2)} kbit/s`]]);}
 else rows('linkrows',[["Profil diminta",l.profile],["Grafik pause",l.bulk_pause||'STREAMING'],["Queue CTRL / BULK",`${l.mqtt_control?.depth||0} / ${l.mqtt_bulk?.depth||0}`],["Parse / angular drop",`${d.parse_errors||0} / ${l.angular_aborted||0}`]]);
 const syn=q.sync||{},syncFlags=[syn.frame,syn.sample_delay,syn.iq];
 const syncCode=syncFlags.every(flag=>flag===true)?'SYNCED':syncFlags.includes(false)?'DEGRADED':'UNKNOWN';
 if(!hostPage) rows('sysrows',[["Engine / desired",`${p.observed||'--'} / ${p.desired||'BELUM DIAMBIL'}`,p.observed==='RUNNING'?'HEALTHY':p.observed],["Frame / Delay / IQ",`${syn.frame??'?'} / ${syn.sample_delay??'?'} / ${syn.iq??'?'}`,syncCode],["Frame / progress",`${q.frame_index??'--'} / ${q.frame_progressing?'MAJU':'BELUM'}`,q.frame_progressing?'HEALTHY':'UNKNOWN'],["Drop total / delta",`${q.dropped_frames??'--'} / ${q.drop_delta??'--'}`],["DAQ umur / USB SDR",`${age(q.source_age_ms)} / ${h.usb_count??'--'} terdeteksi`]]);
 else rows('sysrows',[["CPU / RAM",`${fmt(h.cpu_percent)}% / ${fmt(h.memory_percent)}%`],["Suhu / disk kosong",`${fmt(h.temperature_c)} C / ${fmt(h.disk_free_percent)}%`],["Throttle / under-voltage",`${h.throttled??'unknown'} / ${h.undervoltage??'unknown'}`],["Uptime",h.uptime_s==null?'--':`${Math.floor(h.uptime_s/60)} menit`],["Jam sistem",h.clock_state,h.clock_state]]);
 rows('configrows',[["Node / profil",`${s.node_id||'--'} / ${c.profile||'--'}`],["Source / MQTT",`${c.source_configured?'OK':'SETUP'} / ${c.mqtt_configured?'CONFIGURED':'OFF'}`],["RF revision / proof",`${c.sdr_revision??'--'} / ${c.proof||'--'}`],["Akses / helper",`${authenticated?'ADMIN':'READ ONLY'} / ${s.capabilities?.helper_available?'SIAP':'OFF'}`]]);
 $('admin').textContent=authenticated?'Logout':'Login';$('adminchip').hidden=!authenticated;
 $('configreason').textContent='Kontrol lokal memerlukan approval root satu kali; aksi tetap meminta PIN Admin dan konfirmasi layar.';
 const prefs={theme:'dark',accent:'teal',font:'system',...(c.preferences||{})};
 document.body.classList.toggle('light',prefs.theme==='light');
 document.body.dataset.accent=prefs.accent;document.body.dataset.font=prefs.font;
 renderData(s);
}
async function get(path){const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(1200)});if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();}
async function post(path,body,timeout=6000){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf||''},body:JSON.stringify(body),signal:AbortSignal.timeout(timeout)});let j=await response.json();if(!response.ok){const error=new Error(j.error||j.result?.error||j.stage||'Permintaan gagal');error.status=response.status;throw error;}return j;}
const shutdownFailureStages=new Set(['FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED']);
async function checkShutdownHistory(force=false,clearIfEmpty=false){
 if(!force&&shutdownHistoryChecked&&Date.now()-shutdownHistoryAt<5000)return shutdownUncertain;
 if(shutdownHistoryRequest)return shutdownHistoryRequest;
 shutdownHistoryRequest=(async()=>{
  const status=await get('/api/v2/operations/pending-shutdowns');
  if(typeof status?.pending!=='boolean')throw new Error('Invalid shutdown status');
  shutdownHistoryAt=Date.now();shutdownHistoryChecked=true;
  if(status.pending)setShutdownUncertainty(true);
  else if(clearIfEmpty)setShutdownUncertainty(false);
  return status.pending;
 })();
 try{return await shutdownHistoryRequest;}finally{shutdownHistoryRequest=null;}
}
async function shutdownOutcome(id){for(let i=0;i<12;i++){try{const outcome=await post('/api/v2/operation/result',{id},1500);if(['SHUTDOWN_SCHEDULED','FAILED','REJECTED','OUTCOME_UNKNOWN'].includes(outcome.stage))return outcome;}catch{return null;}await new Promise(r=>setTimeout(r,250));}return null;}
async function waitForShutdownPrepare(id){
 const deadline=Date.now()+10000;
 while(Date.now()<deadline){
  try{
   const result=await post('/api/v2/operation/result',{id},1500);
   if(['APPLIED','FAILED','REJECTED','OUTCOME_UNKNOWN'].includes(result.stage))return result;
  }catch(error){if(error.status>=400&&error.status<500)throw error;}
  await new Promise(r=>setTimeout(r,250));
 }
 return null;
}
async function showShutdownOutcome(outcome,fallbackStage='OUTCOME_UNKNOWN',detail=''){
 const stage=outcome?.stage||fallbackStage;
 if(shutdownFailureStages.has(stage)){try{await checkShutdownHistory(true,true);}catch{setShutdownUncertainty(true);}}
 else setShutdownUncertainty(true);
 const reason=outcome?.result?.error||detail;
 const body=stage==='SHUTDOWN_SCHEDULED'
  ?'SHUTDOWN_SCHEDULED: host belum terbukti mati. Periksa Raspberry secara lokal; jangan ulangi saat unit systemd masih aktif.'
  :shutdownUncertain
   ?`Status ${stage}${reason?` (${reason})`:''}. Periksa Raspberry lokal. Untuk intent tidak aktif, jalankan sudo rdf-node controls shutdown-reconcile pada Pi.`
   :`Status ${stage}${reason?` (${reason})`:''}. Permintaan ini gagal/ditolak dan tidak ada shutdown baru yang dijadwalkan.`;
 const status=modal(stage==='SHUTDOWN_SCHEDULED'?'Shutdown dijadwalkan':shutdownUncertain?'Status shutdown':'Shutdown tidak dijadwalkan');
 text(status,body);
}
async function poll(manual=false){
 if(pollPending){if(manual)pollAgain=true;return;}
 pollPending=true;
 const retry=$('retry');
 if(manual&&retry){retry.disabled=true;retry.textContent='Mencoba lagi...';}
 try{const s=await get('/api/v2/snapshot');lastApi=Date.now();if(s.snapshot_seq!==lastSeq&&s.snapshot_seq!=null){lastSeq=s.snapshot_seq;lastProgress=Date.now();}snapshot=s;try{await checkShutdownHistory();}catch{}render(s);}
 catch(e){/* local watchdog displays staleness independently */}
 finally{
  pollPending=false;
  if(manual&&retry){retry.disabled=false;retry.textContent='Coba lagi';}
  if(pollAgain){pollAgain=false;void poll(true);}
  else pollTimer=setTimeout(poll,500);
 }
}
function retrySnapshot(){
 if(pollTimer){clearTimeout(pollTimer);pollTimer=null;}
 if(pollPending){pollAgain=true;return;}
 void poll(true);
}
 $('retry').onclick=retrySnapshot;
async function keepAdminSessionAlive(){
 const now=Date.now();
 if(!authenticated||adminSessionRefreshPending||now-lastTouch>ADMIN_SESSION_ACTIVITY_MS||now-lastAdminSessionRefresh<ADMIN_SESSION_REFRESH_MS)return;
 adminSessionRefreshPending=true;lastAdminSessionRefresh=now;
 try{
  const session=await get('/api/v2/session');
  if(!session.authenticated){authenticated=false;csrf=null;render(snapshot);}
  else csrf=session.csrf;
 }catch{lastAdminSessionRefresh=Date.now()-ADMIN_SESSION_REFRESH_MS+5000;}
 finally{adminSessionRefreshPending=false;}
}
setInterval(keepAdminSessionAlive,ADMIN_SESSION_CHECK_MS);
setInterval(()=>{const stale=Date.now()-lastProgress>5000;$('stale').hidden=!stale;if(stale)$('stalereason').textContent=Date.now()-lastApi>5000?'API lokal tidak merespons.':'API hidup, snapshot tidak bergerak.';const sec=snapshot.config?.preferences?.blank_after_seconds||0;if(sec>0&&Date.now()-lastTouch>sec*1000&&!snapshot.active_alerts?.some(x=>x.severity==='error'))$('blank').hidden=false;},250);
for(const button of document.querySelectorAll('nav button'))button.addEventListener('click',()=>{for(const e of document.querySelectorAll('.page'))e.classList.toggle('active',e.id===button.dataset.tab);for(const e of document.querySelectorAll('nav button')){const active=e===button;e.classList.toggle('selected',active);if(active)e.setAttribute('aria-current','page');else e.removeAttribute('aria-current');}});
function segmented(id,onChange){
 const host=$(id);
 host.addEventListener('click',event=>{
  const button=event.target.closest('button[data-view]');if(!button||button.classList.contains('on'))return;
  for(const item of host.querySelectorAll('button[data-view]')){const on=item===button;item.classList.toggle('on',on);item.setAttribute('aria-pressed',String(on));}
  onChange(button.dataset.view==='1');
 });
}
segmented('linkpage',value=>{linkData=value;render(snapshot);});
segmented('syspage',value=>{hostPage=value;$('systitle').textContent=hostPage?'RASPBERRY / HOST':'RDF / DAQ';render(snapshot);});
function modal(title){if(mqttKeyboardDismiss){mqttKeyboardDismiss(false);mqttKeyboardDismiss=null;}const dialog=$('modal').querySelector('.dialog');dialog.querySelector('.dialoghead').append($('closemodal'));dialog.classList.remove('keyboard-open');$('modalbody').classList.remove('keyboard-open');modalReturnFocus=document.activeElement;pinKeyHandler=null;$('modaltitle').textContent=title;$('modalbody').replaceChildren();$('modalmsg').textContent='';$('modal').hidden=false;return $('modalbody');}
function dismissModal(){if(mqttKeyboardDismiss)mqttKeyboardDismiss(false);$('modal').hidden=true;pinKeyHandler=null;const target=modalReturnFocus;modalReturnFocus=null;if(target&&typeof target.focus==='function')target.focus();}
$('closemodal').onclick=dismissModal;
function modalKeydown(event){
 if($('modal').hidden)return;
 if(event.key==='Escape'){event.preventDefault();if(mqttKeyboardDismiss){mqttKeyboardDismiss(true);return;}dismissModal();return;}
 if(event.key==='Tab'){
  const items=Array.from(document.querySelectorAll('#modal .dialog button:not(:disabled),#modal .dialog select:not(:disabled),#modal .dialog input:not(:disabled)')).filter(e=>e.getClientRects().length);
  const first=items[0],last=items[items.length-1];
  if(!first){event.preventDefault();return;}
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  return;
 }
 if(pinKeyHandler)pinKeyHandler(event);
}
document.addEventListener('keydown',modalKeydown);
function text(parent,s){const p=document.createElement('p');p.textContent=s;parent.append(p);return p;}
function action(parent,label,fn,danger=false){let wrap=parent.querySelector('.actions');if(!wrap){wrap=document.createElement('div');wrap.className='actions';parent.append(wrap);}let b=document.createElement('button');b.textContent=label;if(danger)b.className='danger';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){if(e.message==='AUTHENTICATION_AND_CSRF_REQUIRED'){authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);login();}else $('modalmsg').textContent=e.message;}finally{b.disabled=false;}};wrap.append(b);return b;}
function needLogin(){if(authenticated)return false;login();return true;}
function login(){
 let pin='',pending=false;
 const box=modal('PIN admin lokal');text(box,'Masukkan 6 angka menggunakan keypad.');
 const layout=document.createElement('div');layout.className='pin-layout';
 const pad=document.createElement('div');pad.className='pin-pad';pad.setAttribute('role','group');pad.setAttribute('aria-label','Keypad PIN');
 const readout=document.createElement('div');readout.className='pin-readout';readout.setAttribute('role','img');
 const slots=Array.from({length:6},()=>{const slot=document.createElement('span');slot.className='pin-slot';slot.setAttribute('aria-hidden','true');readout.append(slot);return slot;});
 const status=document.createElement('span');status.className='sr-only';status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 const unlock=document.createElement('button');unlock.type='button';unlock.className='pin-unlock';unlock.textContent='Unlock';
 const keys=[];
 function addKey(label,aria,run,cls=''){
  const button=document.createElement('button');button.type='button';button.textContent=label;button.setAttribute('aria-label',aria);if(cls)button.className=cls;
  button.onclick=run;pad.append(button);keys.push(button);
 }
 function refresh(){
  slots.forEach((slot,index)=>slot.classList.toggle('filled',index<pin.length));
  const message=`PIN ${pin.length} dari 6 angka.`;readout.setAttribute('aria-label',message);status.textContent=message;
  unlock.disabled=pending||pin.length!==6;keys.forEach(button=>button.disabled=pending);
 }
 function addDigit(digit){if(!pending&&pin.length<6){pin+=digit;refresh();}}
 function removeDigit(){if(!pending&&pin.length){pin=pin.slice(0,-1);refresh();}}
 function clearPin(){if(!pending){pin='';refresh();}}
 for(let digit=1;digit<=9;digit++)addKey(String(digit),`Angka ${digit}`,()=>addDigit(String(digit)));
 addKey('Reset','Hapus seluruh PIN',clearPin,'pin-clear');addKey('0','Angka 0',()=>addDigit('0'));addKey('⌫','Hapus angka terakhir',removeDigit,'pin-backspace');
 layout.append(pad,readout,unlock);box.append(layout,status);
 async function submitPin(){
  if(pending||pin.length!==6)return;
  pending=true;refresh();
  try{const result=await post('/api/v2/login',{pin});csrf=result.csrf;authenticated=true;lastAdminSessionRefresh=Date.now();dismissModal();render(snapshot);}
  catch(e){$('modalmsg').textContent=e.message==='LOGIN_FAILED_OR_RATE_LIMITED'?'PIN salah atau percobaan dibatasi.':e.message;pin='';pending=false;refresh();pad.querySelector('button')?.focus();return;}
  pending=false;refresh();
 }
 unlock.onclick=submitPin;
 pinKeyHandler=event=>{
  if(/^[0-9]$/.test(event.key)){event.preventDefault();addDigit(event.key);}
  else if(event.key==='Backspace'){event.preventDefault();removeDigit();}
  else if(event.key==='Delete'){event.preventDefault();clearPin();}
 };
 refresh();pad.querySelector('button')?.focus();
}
$('admin').onclick=async()=>{
 if(!authenticated)return login();
 try{await post('/api/v2/logout',{});}
 catch(e){
  if(e.message!=='AUTHENTICATION_AND_CSRF_REQUIRED')throw e;
  authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);login();return;
 }
 authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);
};
function mqttField(grid,title,value,type='text',placeholder='',maxLength=253){
 const field=document.createElement('label');field.className='mqtt-settings-field';
 const caption=document.createElement('span');caption.textContent=title;
 const input=document.createElement('input');input.type=type;input.value=value??'';input.placeholder=placeholder;
 input.maxLength=maxLength;input.setAttribute('aria-label',title);field.append(caption,input);grid.append(field);return input;
}
async function openMqttSettings(){
 if(needLogin())return;
 const box=modal('Pengaturan MQTT');text(box,'Memuat pengaturan MQTT...');let settings;
 try{settings=await get('/api/v2/mqtt/settings');}
 catch(e){if(e.message==='HTTP 401'){authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);login();return;}box.replaceChildren();text(box,`Pengaturan tidak termuat: ${e.message}. Tutup lalu coba lagi.`);return;}
 box.replaceChildren();
 if(!settings.editable){text(box,'Pengaturan MQTT tidak tersedia pada mode demo.');return;}
 const grid=document.createElement('div');grid.className='mqtt-settings-fields';
 const statusField=document.createElement('label');statusField.className='mqtt-settings-field';
 const statusCaption=document.createElement('span');statusCaption.textContent='Status MQTT';
 const enabled=document.createElement('select');enabled.setAttribute('aria-label','Status MQTT');
 for(const [value,title] of [['off','Nonaktif'],['on','Aktif']]){const option=document.createElement('option');option.value=value;option.textContent=title;enabled.append(option);}
 enabled.value=settings.enabled?'on':'off';statusField.append(statusCaption,enabled);grid.append(statusField);
 const transportField=document.createElement('label');transportField.className='mqtt-settings-field';
 const transportCaption=document.createElement('span');transportCaption.textContent='Transport MQTT';
 const transport=document.createElement('select');transport.setAttribute('aria-label','Transport MQTT');
 for(const [value,title] of [['tcp','MQTT/TCP'],['websocket','WebSocket (WS)']]){
  const option=document.createElement('option');option.value=value;option.textContent=title;transport.append(option);
 }
 transport.value=settings.transport||'tcp';transportField.append(transportCaption,transport);grid.append(transportField);
 const tlsField=document.createElement('label');tlsField.className='mqtt-settings-field';
 const tlsCaption=document.createElement('span');tlsCaption.textContent='Enkripsi TLS';
 const tls=document.createElement('select');tls.setAttribute('aria-label','Enkripsi TLS');
 for(const [value,title] of [['on','Aktif'],['off','Nonaktif']]){
  const option=document.createElement('option');option.value=value;option.textContent=title;tls.append(option);
 }
 tls.value=settings.tls===false?'off':'on';tlsField.append(tlsCaption,tls);grid.append(tlsField);
 const host=mqttField(grid,'IP / host',settings.host,'text','10.90.0.1',253);
 const port=mqttField(grid,'Port',String(settings.port),'number','8883',5);port.min='1';port.max='65535';port.step='1';
 const clientId=mqttField(grid,'Client ID dasar',settings.client_id,'text','rdf-node',48);
 const websocketPath=mqttField(grid,'Path WebSocket',settings.websocket_path||'/mqtt','text','/mqtt',256);
 const websocketPathField=websocketPath.closest('label');
 const controlUser=mqttField(grid,'User CTRL','','text','',128);controlUser.autocomplete='off';
 const controlPassword=mqttField(grid,'Pass CTRL','','password','',512);controlPassword.autocomplete='new-password';
 const bulkUser=mqttField(grid,'User BULK','','text','',128);bulkUser.autocomplete='off';
 const bulkPassword=mqttField(grid,'Pass BULK','','password','',512);bulkPassword.autocomplete='new-password';
 box.append(grid);
 function updateTransportVisibility(){websocketPathField.hidden=transport.value!=='websocket';}
 transport.addEventListener('change',updateTransportVisibility);
 updateTransportVisibility();
 action(box,'Simpan',async()=>{
  const saved=await post('/api/v2/mqtt/settings',{
   enabled:enabled.value==='on',host:host.value,port:Number(port.value),client_id:clientId.value,
   transport:transport.value,tls:tls.value==='on',websocket_path:websocketPath.value,
   control:{username:controlUser.value,password:controlPassword.value},
   bulk:{username:bulkUser.value,password:bulkPassword.value}
  });
  controlUser.value='';controlPassword.value='';bulkUser.value='';bulkPassword.value='';
  $('modalmsg').textContent=saved.apply_pending?'Tersimpan; koneksi MQTT diperbarui. Tunggu telemetry dan source evidence yang baru.':'Tersimpan.';
  renderData(snapshot);
 });
 const entries=[
  {key:'host',name:'IP / host',input:host,field:host.closest('label')},
  {key:'port',name:'Port',input:port,field:port.closest('label')},
  {key:'client-id',name:'Client ID dasar',input:clientId,field:clientId.closest('label')},
  {key:'websocket-path',name:'Path WebSocket',input:websocketPath,field:websocketPathField},
  {key:'control-user',name:'User CTRL',input:controlUser,field:controlUser.closest('label')},
  {key:'control-password',name:'Pass CTRL',input:controlPassword,field:controlPassword.closest('label')},
  {key:'bulk-user',name:'User BULK',input:bulkUser,field:bulkUser.closest('label')},
  {key:'bulk-password',name:'Pass BULK',input:bulkPassword,field:bulkPassword.closest('label')}
 ];
 const keyboard=document.createElement('section');keyboard.id='mqtt-keyboard';keyboard.hidden=true;
 keyboard.setAttribute('role','group');keyboard.setAttribute('aria-label','Keyboard layar sentuh');
 const bar=document.createElement('div');bar.className='mqtt-keyboard-bar';
 const fieldPicker=document.createElement('select');fieldPicker.setAttribute('aria-label','Pilih kolom pengaturan');
 const keyboardAction=document.createElement('button');keyboardAction.type='button';keyboardAction.setAttribute('aria-label','Pilih semua teks');
 const done=document.createElement('button');done.type='button';done.textContent='Selesai';
 bar.append(fieldPicker,keyboardAction,done);
 const editor=document.createElement('div');editor.id='mqtt-keyboard-editor';
 const keyboardRows=document.createElement('div');keyboardRows.className='mqtt-keyboard-rows';
 const keyboardStatus=document.createElement('span');keyboardStatus.className='sr-only';keyboardStatus.setAttribute('role','status');keyboardStatus.setAttribute('aria-live','polite');
 keyboard.append(bar,editor,keyboardRows,keyboardStatus);box.append(keyboard);
 let active=null,mode='letters',shifted=false,pointerField=null,suppressKeyboardFocus=false;
 const dialog=box.closest('.dialog'),closeButton=$('closemodal'),dialogHead=dialog.querySelector('.dialoghead');
 function restoreField(entry){
  if(entry.field.parentNode===editor)grid.insertBefore(entry.field,entry.next&&entry.next.parentNode===grid?entry.next:null);
 }
 function updateKeyboardAction(){
  if(!active)return;
  const number=active.input.type==='number',symbols=mode==='symbols';
  keyboardAction.textContent=number?'Kosong':symbols?'ABC':'Semua';
  keyboardAction.setAttribute('aria-label',number?'Kosongkan angka':symbols?'Kembali ke huruf':'Pilih semua teks');
 }
 function updatePicker(){
  fieldPicker.replaceChildren();
  for(const entry of entries){
   if(entry.field.hidden)continue;
   const option=document.createElement('option');option.value=entry.key;option.textContent=entry.name;fieldPicker.append(option);
  }
  if(active){fieldPicker.value=active.key;updateKeyboardAction();}
 }
 function replaceInput(input,insert='',remove=false){
  const value=input.value;let start=value.length,end=start;
  if(input.type!=='number'){start=input.selectionStart??value.length;end=input.selectionEnd??start;}
  if(remove){if(start===end&&start>0)start--;insert='';}
  const max=input.maxLength>0?input.maxLength:Infinity;
  const next=(value.slice(0,start)+insert+value.slice(end)).slice(0,max);
  input.value=next;
  const caret=Math.min(start+insert.length,next.length);
  if(input.type!=='number')try{input.setSelectionRange(caret,caret);}catch(_){}
  input.dispatchEvent(new Event('input',{bubbles:true}));
 }
 function renderKeyboard(){
  if(!active)return;
  keyboardRows.replaceChildren();
  const key=(label,value=label,action='',klass='',aria=label)=>({label,value,action,klass,aria});
  const addRow=defs=>{
   const row=document.createElement('div');row.className='mqtt-keyboard-row';
   for(const def of defs){
    const button=document.createElement('button');button.type='button';button.className=`mqtt-key ${def.klass||''}`.trim();
    button.textContent=def.label;button.setAttribute('aria-label',def.aria||def.label);button.dataset.keyAction=def.action||'input';
    if(def.action==='shift')button.setAttribute('aria-pressed',String(shifted));
    button.addEventListener('click',event=>{
     const input=active?.input;if(!input)return;
     if(def.action==='shift'){
      shifted=!shifted;renderKeyboard();
      if(event.detail===0)keyboardRows.querySelector('[data-key-action="shift"]')?.focus();else input.focus({preventScroll:true});
      return;
     }
     if(def.action==='symbols'||def.action==='letters'){
      mode=def.action;shifted=false;keyboardStatus.textContent=mode==='symbols'?'Mode simbol aktif':'Mode huruf aktif';updateKeyboardAction();renderKeyboard();
      if(event.detail===0){if(mode==='symbols')keyboardAction.focus();else keyboardRows.querySelector('[data-key-action="symbols"]')?.focus();}else input.focus({preventScroll:true});
      return;
     }
     if(def.action==='backspace')replaceInput(input,'',true);
     else if(def.action==='clear'){input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));}
     else if(def.action==='space')replaceInput(input,' ');
     else replaceInput(input,def.value);
     if(event.detail>0)input.focus({preventScroll:true});
    });
    row.append(button);
   }
   keyboardRows.append(row);
  };
  if(active.input.type==='number'){
   keyboardRows.classList.add('mqtt-numeric-keyboard');
   addRow(['1','2','3'].map(value=>key(value)));
   addRow(['4','5','6'].map(value=>key(value)));
   addRow(['7','8','9'].map(value=>key(value)));
   addRow([key('⌫','', 'backspace','','Hapus angka terakhir'),key('0'),key('C','', 'clear','','Kosongkan kolom')]);
   return;
  }
  keyboardRows.classList.remove('mqtt-numeric-keyboard');
  const letters=['qwertyuiop','asdfghjkl'];
  if(mode==='letters'){
   for(const line of letters)addRow(Array.from(line,char=>{const shown=shifted?char.toUpperCase():char;return key(shown,shown,'','',`Huruf ${shown}`);}));
   addRow([key('⇧','', 'shift','mqtt-key-wide','Shift huruf besar'),...Array.from('zxcvbnm',char=>key(shifted?char.toUpperCase():char,shifted?char.toUpperCase():char,'','',`Huruf ${shifted?char.toUpperCase():char}`)),key('⌫','', 'backspace','mqtt-key-wide','Hapus karakter terakhir')]);
   addRow([key('?123','', 'symbols','mqtt-key-mode','Angka dan simbol'),key('@'),key('Spasi','', 'space','mqtt-key-space'),key('.'),key('/')]);
  }else{
   addRow(['1','2','3','4','5','6','7','8','9','0'].map(value=>key(value)));
   addRow(['@','#','$','%','&','-','+','(',')','='].map(value=>key(value)));
   addRow(['!','?','*',':',';','_',"'",',','.','/'].map(value=>key(value)));
   addRow(['[',']','{','}','<','>','^',String.fromCharCode(96),'|','"'].map(value=>key(value)));
   addRow([key('~'),key('Spasi','', 'space','mqtt-key-space'),key(String.fromCharCode(92)),key('⌫','', 'backspace','mqtt-key-wide','Hapus karakter terakhir')]);
  }
 }
 function activate(entry,refocus=true){
  if(active===entry)return;
  let selection=null;
  if(entry.input.type!=='number')selection=[entry.input.selectionStart,entry.input.selectionEnd];
  if(active)restoreField(active);
  entry.next=entry.field.nextSibling;editor.append(entry.field);active=entry;mqttKeyboardDismiss=hideKeyboard;bar.append(closeButton);
  keyboard.hidden=false;box.classList.add('keyboard-open');dialog.classList.add('keyboard-open');
  mode=entry.input.type==='number'?'numeric':'letters';shifted=false;updatePicker();renderKeyboard();
  keyboardStatus.textContent=`Keyboard layar sentuh untuk ${entry.name}`;
  box.scrollTop=0;
  if(refocus){entry.input.focus({preventScroll:true});if(selection&&selection[0]!==null)try{entry.input.setSelectionRange(...selection);}catch(_){}}
 }
 function hideKeyboard(refocus=true){
  if(!active)return;
  const entry=active;restoreField(entry);active=null;keyboard.hidden=true;dialogHead.append(closeButton);
  box.classList.remove('keyboard-open');dialog.classList.remove('keyboard-open');mqttKeyboardDismiss=null;
  if(refocus){suppressKeyboardFocus=true;try{entry.input.focus({preventScroll:true});}finally{suppressKeyboardFocus=false;}}
 }
 fieldPicker.addEventListener('change',()=>{const entry=entries.find(value=>value.key===fieldPicker.value);if(entry)activate(entry);});
 keyboardAction.addEventListener('click',event=>{
  if(!active)return;
  if(active.input.type!=='number'&&mode==='symbols'){
   mode='letters';shifted=false;keyboardStatus.textContent='Mode huruf aktif';updateKeyboardAction();renderKeyboard();
   if(event.detail>0)active.input.focus({preventScroll:true});
   return;
  }
  if(active.input.type==='number')active.input.value='';else active.input.select();
  active.input.dispatchEvent(new Event('input',{bubbles:true}));
  if(event.detail>0)active.input.focus({preventScroll:true});
 });
 done.addEventListener('click',()=>hideKeyboard(true));
 for(const entry of entries){
  entry.input.inputMode='none';
  entry.input.addEventListener('pointerdown',()=>{pointerField=entry.input;setTimeout(()=>{if(pointerField===entry.input)pointerField=null;},0);});
  entry.input.addEventListener('focus',event=>{if(event.isTrusted&&!suppressKeyboardFocus&&pointerField!==entry.input)activate(entry);});
  entry.input.addEventListener('click',event=>{if(event.isTrusted)activate(entry);});
 }
 enabled.focus({preventScroll:true});
}
$('mqttsettings').onclick=openMqttSettings;
async function command(op,extras={}){
 const r={v:2,id:`local-${Date.now()}-${crypto.randomUUID().slice(0,8)}`,sid:snapshot.sid,boot:snapshot.boot_id,issued_ms:Date.now(),expires_ms:Date.now()+15000,base_rev:snapshot.config?.sdr_revision??null,op,...extras};
 try{const result=await post('/api/v2/commands',r);if(op==='system.shutdown.execute')result.requestId=r.id;$('modalmsg').textContent=`${result.stage}: ${result.id||''}`;return result;}
 catch(error){if(op!=='system.shutdown.execute')throw error;const failure=new Error(error.message);failure.operationId=r.id;failure.status=error.status;throw failure;}
}
$('profilebtn').onclick=()=>{if(needLogin())return;const box=modal('Profil telemetry');text(box,'Grafik dipause saat command berlangsung, source invalid, atau jalur Bulk belum siap.');action(box,'CONTROL',()=>command('stream.set',{profile:'control'}));action(box,'BALANCED',()=>command('stream.set',{profile:'balanced'}));action(box,'GRAPH',()=>command('stream.set',{profile:'graph_u8'}));};
function preferences(){
 if(needLogin())return;
 const box=modal('Tema dan font');text(box,'Pilihan ini disimpan pada perangkat.');
 const current={theme:'dark',accent:'teal',font:'system',...(snapshot.config?.preferences||{})};
 const grid=document.createElement('div');grid.className='preference-grid';const selects={};
 const fields=[
  ['theme','Mode',[['dark','Gelap'],['light','Terang']]],
  ['accent','Warna aksen',[['teal','Teal'],['blue','Biru'],['amber','Amber']]],
  ['font','Jenis huruf',[['system','Sistem'],['serif','Serif'],['mono','Monospace']]]
 ];
 for(const [key,label,choices] of fields){
  const field=document.createElement('label');field.className='preference-field';
  const caption=document.createElement('span');caption.textContent=label;
  const select=document.createElement('select');select.setAttribute('aria-label',label);
  for(const [value,title] of choices){const option=document.createElement('option');option.value=value;option.textContent=title;select.append(option);}
  select.value=current[key];field.append(caption,select);grid.append(field);selects[key]=select;
 }
 box.append(grid);
 action(box,'Simpan tampilan',async()=>{
  const body={};for(const [key,select] of Object.entries(selects))body[key]=select.value;
  const saved=await post('/api/v2/display/preferences',body);
  snapshot.config={...(snapshot.config||{}),preferences:saved};render(snapshot);$('modalmsg').textContent='Tampilan disimpan pada perangkat.';
 });
 grid.querySelector('select')?.focus();
}
$('themebtn').onclick=preferences;
$('controlbtn').onclick=()=>{if(needLogin())return;const box=modal('Kontrol RDF');const caps=snapshot.capabilities||{};
 action(box,'Start SDR service',()=>command('processing.set',{desired:'RUNNING'})).disabled=!caps.processing;
 action(box,'Stop SDR service',()=>confirmOperation('STOP SDR SERVICE','processing.set',{desired:'STOPPED'}),true).disabled=!caps.processing;
 action(box,'Restart SDR service',()=>confirmOperation('RESTART SDR SERVICE','service.restart',{}),true).disabled=!caps.restart;
 const row=document.createElement('div');box.append(row);
 action(row,'Frekuensi',()=>frequency()).disabled=!caps.config_patch;
  const reboot=action(row,'Reboot Raspberry',()=>prepareReboot(),true);
  reboot.disabled=!caps.reboot;
  const rebootReason=!caps.helper_available?'Helper kontrol belum tersedia.':
   !caps.reboot?'Reboot lokal memerlukan mode controlled dan approval root (--reboot).':'';
  if(rebootReason){const note=text(row,rebootReason);note.className='hint';}
  const shutdown=action(row,'Shutdown Raspberry',()=>prepareShutdown(),true);
  shutdown.disabled=!caps.shutdown||!shutdownHistoryChecked;
  shutdown.title=!shutdownHistoryChecked?'Riwayat operasi belum tersedia.':shutdownUncertain?'Periksa Raspberry secara lokal sebelum mengulangi shutdown.':'';
};
function confirmOperation(label,op,extra){const box=modal(label);text(box,'Aksi ini mengganggu pemrosesan RDF. Bridge tetap hidup kecuali reboot OS. Pastikan kondisi operasi aman sebelum melanjutkan.');action(box,'Konfirmasi',()=>command(op,extra),true);}
function frequency(){const box=modal('Frekuensi center + VFO0');text(box,'MHz; range dan gain mengikuti policy perangkat. File tersimpan belum berarti runtime terverifikasi.');const input=document.createElement('input');input.type='number';input.step='0.001';input.value=snapshot.detection?.frequency_hz?snapshot.detection.frequency_hz/1e6:'';box.append(input);action(box,'Apply',()=>{const hz=Math.round(Number(input.value)*1e6);if(!Number.isFinite(hz)||hz<=0)throw new Error('Frekuensi tidak valid');return command('config.patch',{changes:{center_frequency_hz:hz,vfo0_frequency_hz:hz}});});}
async function prepareReboot(){const box=modal('Reboot Raspberry');text(box,'Node akan offline. Admin harus unlock; approval root --reboot disimpan satu kali. Konfirmasi akhir tetap diperlukan. Health berhenti selama boot.');action(box,'Prepare',async()=>{const r=await command('system.reboot.prepare');$('modalmsg').textContent='Menunggu challenge...';let op=null;for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,300));const list=await get('/api/v2/operations/latest');op=list.find(x=>x.id===r.id);if(op&&['FAILED','APPLIED','REJECTED'].includes(op.stage))break;}
 // Challenges are deliberately not returned by public history. A local reboot is
 // issued through authenticated command result access (private endpoint below).
 const result=await post('/api/v2/operation/result',{id:r.id});
 if(result.stage!=='APPLIED'||!result.result?.challenge)throw new Error(result.result?.error||'Challenge belum tersedia');
 const cbox=modal('Konfirmasi reboot OS');text(cbox,'Konfirmasi dalam 30 detik. Reboot mulai setelah hasil dijurnal.');action(cbox,'REBOOT SEKARANG',()=>command('system.reboot.execute',{prepare_id:r.id,challenge:result.result.challenge}),true);
 });}
async function prepareShutdown(){
 const box=modal('Shutdown Raspberry');
 text(box,'Raspberry akan dimatikan sepenuhnya; akses lokal diperlukan untuk menyalakannya lagi. Fitur memerlukan Admin PIN dan approval root --shutdown yang disimpan satu kali.');
 action(box,'Prepare',async()=>{
  if(readShutdownUncertainty())setShutdownUncertainty(true);
  try{await checkShutdownHistory(true);}catch{$('modalmsg').textContent='Status shutdown tidak tersedia; permintaan tidak dikirim.';return;}
  if(shutdownUncertain&&!confirm('Hasil shutdown sebelumnya belum pasti. Periksa status Raspberry lokal. Jika intent tidak aktif, jalankan sudo rdf-node controls shutdown-reconcile pada Pi; helper menolak bila unit masih aktif. Lanjutkan?'))return;
  setShutdownUncertainty(false);
  const prepared=await command('system.shutdown.prepare');
  if(!prepared?.id||prepared.stage==='REJECTED'){ $('modalmsg').textContent=prepared?.result?.error||'Prepare shutdown ditolak.';return; }
  $('modalmsg').textContent='Menunggu hasil prepare...';
  let result;
  try{result=await waitForShutdownPrepare(prepared.id);}catch(error){$('modalmsg').textContent=error.message;return;}
  if(result?.stage!=='APPLIED'||!result.result?.challenge){$('modalmsg').textContent=result?.result?.error||'Challenge shutdown belum tersedia setelah menunggu helper.';return;}
  try{await checkShutdownHistory(true);}catch{$('modalmsg').textContent='Status shutdown berubah/tidak tersedia; execute tidak dikirim.';return;}
  if(shutdownUncertain&&!confirm('Ada shutdown terdahulu yang belum pasti. Periksa Raspberry lokal dan rekonsiliasi intent hanya bila unit systemd tidak aktif. Lanjutkan?'))return;
  const cbox=modal('Konfirmasi shutdown OS');
  text(cbox,'Pi akan mati dalam 5 detik setelah jadwal diterima. Tidak ada bukti OS sudah berhenti; nyalakan kembali secara lokal.');
  action(cbox,'SHUTDOWN PI SEKARANG',async()=>{
   const executeId=`local-${Date.now()}-${crypto.randomUUID().slice(0,8)}`;
   setShutdownUncertainty(true);
   let sent;
   try{sent=await command('system.shutdown.execute',{id:executeId,prepare_id:prepared.id,challenge:result.result.challenge});}
   catch(error){await showShutdownOutcome(null,error.status>=400&&error.status<500?'REJECTED':'OUTCOME_UNKNOWN',error.message);return;}
   if(sent.id!==executeId){await showShutdownOutcome(null,'OUTCOME_UNKNOWN','ID operasi tidak cocok');return;}
   const outcome=await shutdownOutcome(executeId);
   await showShutdownOutcome(outcome,outcome?'OUTCOME_UNKNOWN':'ACK_TIMEOUT');
  },true);
 });
}
document.addEventListener('pointerdown',()=>{lastTouch=Date.now();$('blank').hidden=true;});document.addEventListener('keydown',()=>{lastTouch=Date.now();$('blank').hidden=true;});
get('/api/v2/session').then(s=>{if(authenticated)return;authenticated=s.authenticated;csrf=s.csrf;lastAdminSessionRefresh=Date.now();render(snapshot);}).catch(()=>{});poll();
