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
// Stroke icons on a 24-unit grid, drawn as single paths so they inherit currentColor.
const ICONS={
 lock:'M7 11V7a5 5 0 0 1 10 0v4M5 11h14v10H5z',unlock:'M7 11V7a5 5 0 0 1 9.6-2M5 11h14v10H5z',
 play:'M7 4v16l13-8z',stop:'M6 6h12v12H6z',restart:'M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5',power:'M12 3v8M6.3 7.5a8 8 0 1 0 11.4 0',
 wave:'M2 12c2.5-7 4.5-7 7 0s4.5 7 7 0 3-4.5 6-4.5',moon:'M20 14.5A8.5 8.5 0 1 1 9.5 4 6.5 6.5 0 0 0 20 14.5z',
 sun:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
 eye:'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',warn:'M12 3 2 20h20zM12 10v4M12 17v.5',
 plug:'M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5',chain:'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
 radar:'M12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2',
 cloud:'M7 19h10a4.5 4.5 0 0 0 .6-9A6 6 0 0 0 6 11.5 3.8 3.8 0 0 0 7 19z',updown:'M7 20V4M4 7l3-3 3 3M17 4v16M14 17l3 3 3-3',
 activity:'M3 12h4l3-7 4 14 3-7h4',pause:'M8 5v14M16 5v14',layers:'M12 3 2 8l10 5 10-5zM2 13l10 5 10-5',
 xcircle:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9 9l6 6M15 9l-6 6',cpu:'M6 6h12v12H6zM9.5 9.5h5v5h-5zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4',
 sync:'M4 12a8 8 0 0 1 13.7-5.7L20 8M20 4v4h-4M20 12a8 8 0 0 1-13.7 5.7L4 16M4 20v-4h4',film:'M4 5h16v14H4zM8 5v14M16 5v14',
 clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',thermo:'M14 14.8V5a2 2 0 0 0-4 0v9.8a4 4 0 1 0 4 0z',bolt:'M13 2 4 14h7l-1 8 9-12h-7z',
 gauge:'M4 18a8 8 0 1 1 16 0M12 18l4-5',pin:'M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zM12 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z',
 folder:'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',shield:'M12 3 4 6v6c0 5 3.4 8.4 8 9 4.6-.6 8-4 8-9V6z',
 hash:'M5 9h14M5 15h14M10 3 8 21M16 3l-2 18',screenoff:'M3 5h18v12H3zM8 21h8M12 17v4M2 2l20 20',type:'M4 7V4h16v3M9 20h6M12 4v16',
 drop:'M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z',check:'M5 12l5 5 9-10',sliders:'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M16 4v4M10 10v4M18 16v4',
 backspace:'M9 5h11v14H9l-6-7zM12 9l5 6M17 9l-5 6'
};
function icon(name,extra=''){
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
 svg.setAttribute('class',`ico${extra?` ${extra}`:''}`);const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',ICONS[name]||'');svg.append(path);return svg;
}
// Rewrites a button only when its label changes, so a periodic render never swaps the node a
// finger is currently pressing.
function setButtonLabel(button,label,iconName){
 if(!button||button.dataset.label===label)return;
 button.dataset.label=label;const span=document.createElement('span');span.textContent=label;
 button.replaceChildren(...(iconName?[icon(iconName)]:[]),span);
}
function row(label,value,code,iconName){const r=document.createElement('div');r.className='row';const l=document.createElement('label');if(iconName)l.append(icon(iconName));l.append(label);const v=document.createElement('b');v.textContent=String(value??'--');if(code)v.className=good.includes(code)?'good':bad.includes(code)?'bad':'warn';r.append(l,v);return r;}
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
// Policy discards (diagnostic not needed, Bulk paused by a gate) are reported by the Agent as
// ERROR but are deliberate non-sends; show them as skipped with the reason, not as failures.
function deliverySkipped(status){return status?.state==='ERROR'&&(status.error==='DIAGNOSTIC_NOT_NEEDED'||String(status.error||'').startsWith('BULK_PAUSED_'));}
function setTopicDelivery(entry,status){
 const skipped=deliverySkipped(status),state=skipped?'SKIPPED':status?.state||'NONE',label=entry.label,info=entry.info;
 entry.cell.className=`delivery-cell delivery-cell--${state.toLowerCase()}`;
 if(skipped){
  label.textContent='DILEWATI';info.textContent=status.error;
 }else if(state==='SENT'){
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
const TRAIL_LENGTH=8,TRAIL_MAX_AGE_MS=30000;
let needleTurn=null,bearingTrail=[],trailSid=null;
function createDial(host){
 if(!host)return null;
 const svg=document.createElementNS(SVG_NS,'svg');
 svg.setAttribute('viewBox','-72 -72 144 144');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
 const add=(tag,attrs,parent=svg)=>{const node=document.createElementNS(SVG_NS,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));parent.append(node);return node;};
 add('circle',{class:'dial-face',r:66});
 const spectrum=add('path',{class:'dial-spectrum',d:''});
 add('circle',{class:'dial-ring',r:64});add('circle',{class:'dial-inner',r:40});add('path',{class:'dial-cross',d:'M-64 0H64M0-64V64'});
 const ticks=add('g',{});
 for(let deg=0;deg<360;deg+=10){
  const major=deg%30===0,theta=deg*Math.PI/180,inner=major?55:59;
  add('line',{class:major?'dial-tick major':'dial-tick',x1:(inner*Math.sin(theta)).toFixed(2),y1:(-inner*Math.cos(theta)).toFixed(2),x2:(64*Math.sin(theta)).toFixed(2),y2:(-64*Math.cos(theta)).toFixed(2)},ticks);
 }
 const labels=add('g',{class:'dial-labels'});
 for(const [text,x,y] of [['0',0,-44],['90',46,0],['180',0,48],['270',-46,0]])add('text',{x,y},labels).textContent=text;
 const trail=add('g',{class:'dial-trail'});
 const trailDots=Array.from({length:TRAIL_LENGTH},()=>add('circle',{r:2.6,cx:0,cy:0,opacity:0},trail));
 const needle=add('g',{class:'needle'});
 add('path',{class:'needle-wedge',d:'M0 0L-9.1-63.3A64 64 0 0 1 9.1-63.3Z'},needle);add('path',{class:'needle-line',d:'M0 10V-52'},needle);add('path',{class:'needle-head',d:'M0-64L-6-51H6Z'},needle);
 add('circle',{class:'dial-hub',r:4});
 const note=add('text',{class:'dial-note',y:24});
 host.dataset.spectrum='off';host.append(svg);
 return {host,needle,spectrum,note,trailDots};
}
const dials=[createDial($('dial')),createDial($('focusdial'))].filter(Boolean);
// Only a gate-valid relative DoA moves the needle. Raw/UNVERIFIED angles use a different
// convention and stay numeric-only so the dial never implies a verified bearing.
function updateDial(angle,unverified,sample){
 const valid=typeof angle==='number'&&Number.isFinite(angle);
 const target=valid?((angle%360)+360)%360:null;
 if(valid)needleTurn=needleTurn===null?target:needleTurn+((target-needleTurn)%360+540)%360-180;
 // Recent gate-valid bearings only; any invalid sample clears the trail so old fixes never
 // read as current evidence.
 const now=Date.now();
 if(!valid)bearingTrail=[];
 else if(bearingTrail.at(-1)?.sample!==sample)bearingTrail.push({angle:target,sample,at:now});
 bearingTrail=bearingTrail.filter(point=>now-point.at<=TRAIL_MAX_AGE_MS).slice(-TRAIL_LENGTH);
 const history=bearingTrail.slice(0,-1);
 const description=valid?`Dial arah relatif ${fmt(target)} derajat, 0 di atas searah jarum jam`:unverified?'Dial arah relatif: sudut belum terverifikasi, jarum disembunyikan':'Dial arah relatif: belum ada pengukuran valid';
 for(const dial of dials){
  dial.host.dataset.state=valid?'valid':unverified?'unverified':'none';
  dial.note.textContent=valid?'':unverified?'UNVERIFIED':'TANPA DATA';
  if(valid)dial.needle.style.setProperty('--a',`${needleTurn.toFixed(2)}deg`);
  dial.trailDots.forEach((dot,index)=>{
   const point=history[history.length-1-index];
   if(!point){dot.setAttribute('opacity','0');return;}
   const theta=point.angle*Math.PI/180,fade=1-(now-point.at)/TRAIL_MAX_AGE_MS;
   dot.setAttribute('cx',(50*Math.sin(theta)).toFixed(1));dot.setAttribute('cy',(-50*Math.cos(theta)).toFixed(1));
   dot.setAttribute('opacity',(Math.max(.12,fade*(1-index/TRAIL_LENGTH))*.85).toFixed(2));
  });
  dial.host.setAttribute('aria-label',dial.host.id==='dial'?`${description}. Ketuk untuk mode baca jauh.`:description);
 }
}
// The 360-value Angular frame is indexed by theta, which equals the relative DoA axis only
// under theta_mirror. It is drawn (scaled per frame, no units) only for the same sample q as
// the displayed gate-valid detection; anything else hides the curve rather than guessing.
let spectrumQ=null,spectrumShownAt=0,spectrumPending=false;
function setSpectrum(values){
 let d='';
 if(values){
  const min=Math.min(...values),max=Math.max(...values),range=max-min;
  d=values.map((value,index)=>{const radius=range>0?14+48*(value-min)/range:38,theta=index*Math.PI/180;return `${index?'L':'M'}${(radius*Math.sin(theta)).toFixed(1)} ${(-radius*Math.cos(theta)).toFixed(1)}`;}).join('')+'Z';
 }
 for(const dial of dials){dial.spectrum.setAttribute('d',d);dial.host.dataset.spectrum=values?'on':'off';}
}
function spectrumVisible(){return $('blank').hidden&&($('home').classList.contains('active')||!$('focus').hidden);}
async function refreshSpectrum(s){
 const d=s.detection||{};
 if(!d.valid||d.angle_convention!=='theta_mirror'||typeof d.q!=='number'){if(spectrumQ!==null){spectrumQ=null;setSpectrum(null);}return;}
 if(spectrumQ!==null&&spectrumQ!==d.q&&Date.now()-spectrumShownAt>2000){spectrumQ=null;setSpectrum(null);}
 if(!spectrumVisible()||spectrumPending||d.q===spectrumQ)return;
 spectrumPending=true;
 try{
  const frame=await get('/api/v2/angular/latest'),current=snapshot.detection||{};
  if(frame&&frame.live===true&&current.valid&&frame.q===current.q&&Array.isArray(frame.values)&&frame.values.length===360&&frame.values.every(Number.isFinite)){
   spectrumQ=frame.q;spectrumShownAt=Date.now();setSpectrum(frame.values);
  }
 }catch{/* keep the last matching frame; the 2 s age check hides it if updates stop */}
 finally{spectrumPending=false;}
}
function openFocus(){$('focus').hidden=false;void refreshSpectrum(snapshot);}
function closeFocus(){if($('focus').hidden)return;$('focus').hidden=true;$('dial').focus({preventScroll:true});}
$('dial').addEventListener('click',openFocus);
$('dial').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openFocus();}});
$('focus').addEventListener('click',closeFocus);
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('modal').hidden)closeFocus();});
function renderData(s){
 const l=s.link||{};
 const control=l.mqtt_control||{},bulk=l.mqtt_bulk||{};
 label('mqttclients',`${control.state||'DISABLED'} / ${bulk.state||'DISABLED'}`,mqttPairState(l));
 setButtonLabel($('mqttsettings'),authenticated?'Atur':'Login',authenticated?'sliders':'lock');
 const node=s.node_id||'--',prefix=`${s.mode==='DEMO'?'sdr/demo/v2':'sdr/v2'}/${node}/`;
 if(prefix!==renderedTopicPrefix){renderedTopicPrefix=prefix;$('topicprefix').textContent=prefix;renderTopicList();}
 updateTopicStatuses(l.mqtt_topic_delivery||{});
}
const goodStages=new Set(['APPLIED','REBOOT_SCHEDULED','SHUTDOWN_SCHEDULED']);
const badStages=new Set(['FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED']);
const warnStages=new Set(['OUTCOME_UNKNOWN','PERSISTED_UNVERIFIED']);
function stageTone(stage){return goodStages.has(stage)?'good':badStages.has(stage)?'bad':warnStages.has(stage)?'warn':'progress';}
let seenOperation=undefined,toastTimer=null;
function showToast(message,tone){
 const toast=$('toast');toast.textContent=message;toast.className=`toast ${tone}`;toast.hidden=false;
 if(toastTimer)clearTimeout(toastTimer);
 toastTimer=setTimeout(()=>{toast.hidden=true;toastTimer=null;},4500);
}
// Stage codes are shown verbatim; colour only groups them. A finished stage is the journal
// outcome, not proof that runtime state changed (e.g. PERSISTED_UNVERIFIED stays amber).
function renderOperation(op){
 const chip=$('cmdstage');
 $('command').textContent=op?op.op:'Belum ada operasi';
 chip.hidden=!op;
 if(op){chip.textContent=op.stage;chip.className=`stagechip ${stageTone(op.stage)}`;}
 const key=op?`${op.id}:${op.stage}`:null;
 if(seenOperation===undefined){seenOperation=key;return;}
 if(key===seenOperation)return;
 seenOperation=key;
 if(op&&stageTone(op.stage)!=='progress')showToast(`${op.op}: ${op.stage}`,stageTone(op.stage));
}
// Nav badges point at the tab that explains a problem; they never mark anything healthy.
function tabAlerts(s){
 const l=s.link||{},h=s.host||{},q=s.daq||{},c=s.config||{},p=s.processing||{};
 const mqtt=[l.mqtt_control?.state,l.mqtt_bulk?.state];
 const link=mqtt.includes('ERROR')?'bad':(l.ppp!=='UP'||l.usb!=='PRESENT'||['NO_REPLY','ERROR'].includes(l.ppp_probe)||mqtt.includes('CONNECTING'))?'warn':'';
 const system=(q.state==='DEGRADED'||p.observed==='ERROR'||h.undervoltage===true)?'bad':(q.state!=='HEALTHY'||p.observed!=='RUNNING'||h.clock_trusted!==true)?'warn':'';
 const config=c.source_configured?'':'warn';
 const data=Object.values(l.mqtt_topic_delivery||{}).some(entry=>entry?.state==='ERROR'&&!deliverySkipped(entry))?'bad':'';
 return {link,system,config,data};
}
function renderTabAlerts(s){
 const alerts=tabAlerts(s);
 for(const button of document.querySelectorAll('nav button')){
  const level=alerts[button.dataset.tab]||'',name=button.querySelector('span').textContent;
  if(level)button.dataset.alert=level;else delete button.dataset.alert;
  button.setAttribute('aria-label',level?`${name}, ${level==='bad'?'ada masalah':'perlu perhatian'}`:name);
 }
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
 if(s.sid!==trailSid){trailSid=s.sid;bearingTrail=[];}
 updateDial(d.valid?d.relative_doa_deg:null,showDiagnostic,d.q);
 $('angle').className=showDiagnostic?'warn':d.valid?'good':'neutral';
 $('age').textContent=showDiagnostic?`doa.xml / umur ${age(diag.source_age_ms)}`:d.valid?`Umur ${age(d.source_age_ms)}`:`${d.state||'MENUNGGU'} / ${age(d.source_age_ms)}`;
 $('frequency-label').textContent=showDiagnostic?'FREKUENSI XML':'FREKUENSI VFO';
 $('freq').textContent=showDiagnostic?fmt(diag.frequency_mhz,3):d.frequency_hz?fmt(d.frequency_hz/1e6,3):'--';
 $('quality').textContent=showDiagnostic?`Gate: ${diagnosticReason}`:`PAPR ${fmt(d.confidence_native_db,2)} dB / P ${fmt(d.power_native_db)} dB`;
 label('daq',q.state==='HEALTHY'?'SINKRON':q.state||'UNKNOWN',q.state);
 label('sync',c.sdr_revision==null?'--':`r${c.sdr_revision}`,'');
 renderOperation(s.last_operation);
 const alerts=s.active_alerts||[];const a=alerts.find(x=>x.severity==='error')||alerts[0];
 $('alert').textContent=a?a.text:'Status lokal normal';$('alertbox').className=`alert ${a?(a.severity==='error'?'bad':'warn'):'good'}`;
 $('alerticon').textContent=a?'!':'\u2713';$('temp').textContent=`${fmt(h.temperature_c)}\u00b0C`;
 $('focus-angle-label').textContent=$('angle-label').textContent;$('focusangle').textContent=$('angle').textContent;$('focusangle').className=$('angle').className;
 $('focusage').textContent=$('age').textContent;$('focusfreq').textContent=$('freq').textContent;
 label('focusppp',`PPP ${l.ppp||'--'}`,l.ppp);label('focusmqtt',`MQTT ${l.mqtt_control?.state||'DISABLED'}`,l.mqtt_control?.state);label('focusdaq',`DAQ ${q.state==='HEALTHY'?'SINKRON':q.state||'UNKNOWN'}`,q.state);
 $('focusalert').textContent=a?a.text:'';$('focusalert').className=`focus-alert ${a?(a.severity==='error'?'bad':'warn'):''}`;
 if(!linkData){rows('linkrows',[["T900 USB",l.usb,l.usb,'plug'],["PPP / interface",`${l.ppp||'--'} / ${l.interface||'--'}`,l.ppp,'chain'],[`Ping ${l.ppp_peer||'peer'}`,pppProbeNames[l.ppp_probe]||'BELUM DICEK',l.ppp_probe,'radar'],["MQTT CTRL / BULK",`${l.mqtt_control?.state||'--'} / ${l.mqtt_bulk?.state||'--'}`,mqttPairState(l),'cloud'],["TX / RX (PPP/IP)",`${fmt(l.tx_kbit_s,2)} / ${fmt(l.rx_kbit_s,2)} kbit/s`,'','updown']]);}
 else rows('linkrows',[["Profil diminta",l.profile,'','activity'],["Grafik pause",l.bulk_pause||'STREAMING','','pause'],["Queue CTRL / BULK",`${l.mqtt_control?.depth||0} / ${l.mqtt_bulk?.depth||0}`,'','layers'],["Parse / angular drop",`${d.parse_errors||0} / ${l.angular_aborted||0}`,'','xcircle']]);
 const syn=q.sync||{},syncFlags=[syn.frame,syn.sample_delay,syn.iq];
 const syncCode=syncFlags.every(flag=>flag===true)?'SYNCED':syncFlags.includes(false)?'DEGRADED':'UNKNOWN';
 if(!hostPage) rows('sysrows',[["Engine / desired",`${p.observed||'--'} / ${p.desired||'BELUM DIAMBIL'}`,p.observed==='RUNNING'?'HEALTHY':p.observed,'cpu'],["Frame / Delay / IQ",`${syn.frame??'?'} / ${syn.sample_delay??'?'} / ${syn.iq??'?'}`,syncCode,'sync'],["Frame / progress",`${q.frame_index??'--'} / ${q.frame_progressing?'MAJU':'BELUM'}`,q.frame_progressing?'HEALTHY':'UNKNOWN','film'],["Drop total / delta",`${q.dropped_frames??'--'} / ${q.drop_delta??'--'}`,'','xcircle'],["DAQ umur / USB SDR",`${age(q.source_age_ms)} / ${h.usb_count??'--'} terdeteksi`,'','clock']]);
 else rows('sysrows',[["CPU / RAM",`${fmt(h.cpu_percent)}% / ${fmt(h.memory_percent)}%`,'','gauge'],["Suhu / disk kosong",`${fmt(h.temperature_c)} C / ${fmt(h.disk_free_percent)}%`,'','thermo'],["Throttle / under-voltage",`${h.throttled??'unknown'} / ${h.undervoltage??'unknown'}`,'','bolt'],["Uptime",h.uptime_s==null?'--':`${Math.floor(h.uptime_s/60)} menit`,'','power'],["Jam sistem",h.clock_state,h.clock_state,'clock']]);
 rows('configrows',[["Node / profil",`${s.node_id||'--'} / ${c.profile||'--'}`,'','pin'],["Source / MQTT",`${c.source_configured?'OK':'SETUP'} / ${c.mqtt_configured?'CONFIGURED':'OFF'}`,'','folder'],["RF revision / proof",`${c.sdr_revision??'--'} / ${c.proof||'--'}`,'','hash'],["Akses / helper",`${authenticated?'ADMIN':'READ ONLY'} / ${s.capabilities?.helper_available?'SIAP':'OFF'}`,'','shield']]);
 setButtonLabel($('admin'),authenticated?'Logout':'Login',authenticated?'unlock':'lock');$('adminchip').hidden=!authenticated;
 $('configreason').textContent='Kontrol lokal memerlukan approval root satu kali; aksi tetap meminta PIN Admin dan konfirmasi layar.';
 const prefs={theme:'dark',accent:'teal',font:'system',...(c.preferences||{})};
 document.body.classList.toggle('light',prefs.theme==='light');document.body.classList.toggle('night',prefs.theme==='night');
 document.body.dataset.accent=prefs.accent;document.body.dataset.font=prefs.font;
 renderData(s);
 renderTabAlerts(s);
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
 try{const s=await get('/api/v2/snapshot');lastApi=Date.now();if(s.snapshot_seq!==lastSeq&&s.snapshot_seq!=null){lastSeq=s.snapshot_seq;lastProgress=Date.now();}snapshot=s;try{await checkShutdownHistory();}catch{}render(s);void refreshSpectrum(s);}
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
// Black screen: an overlay only (the backlight stays under OS control). Any tap wakes it and
// that tap is swallowed; a new error alert that appears while black wakes it automatically.
let blankErrors=new Set();
function errorAlertCodes(){return new Set((snapshot.active_alerts||[]).filter(alert=>alert.severity==='error').map(alert=>alert.code||alert.text));}
function enterBlank(){blankErrors=errorAlertCodes();$('blank').hidden=false;}
function wakeScreen(){if($('blank').hidden)return false;$('blank').hidden=true;lastTouch=Date.now();void refreshSpectrum(snapshot);return true;}
setInterval(()=>{const stale=Date.now()-lastProgress>5000;$('stale').hidden=!stale;if(stale)$('stalereason').textContent=Date.now()-lastApi>5000?'API lokal tidak merespons.':'API hidup, snapshot tidak bergerak.';const sec=snapshot.config?.preferences?.blank_after_seconds||0,errors=errorAlertCodes();if($('blank').hidden){if(sec>0&&Date.now()-lastTouch>sec*1000&&!errors.size)enterBlank();}else if([...errors].some(code=>!blankErrors.has(code)))wakeScreen();},250);
const tabOrder=Array.from(document.querySelectorAll('nav button'),button=>button.dataset.tab);
function selectTab(tab,direction=0){
 for(const page of document.querySelectorAll('.page')){
  const active=page.id===tab;page.classList.toggle('active',active);page.classList.remove('enter-next','enter-prev');
  if(active&&direction)page.classList.add(direction>0?'enter-next':'enter-prev');
 }
 for(const e of document.querySelectorAll('nav button')){const active=e.dataset.tab===tab;e.classList.toggle('selected',active);if(active)e.setAttribute('aria-current','page');else e.removeAttribute('aria-current');}
 void refreshSpectrum(snapshot);
}
for(const button of document.querySelectorAll('nav button'))button.addEventListener('click',()=>selectTab(button.dataset.tab));
// Horizontal touch swipe on a page moves to the neighbouring tab; the tap that ends a swipe
// is swallowed so it cannot also press a button or open the far-reading view.
let swipe=null,swallowClick=false;
for(const page of document.querySelectorAll('.page')){
 page.addEventListener('pointerdown',event=>{swipe=event.pointerType==='mouse'?null:{x:event.clientX,y:event.clientY,at:Date.now(),id:event.pointerId};});
 page.addEventListener('pointercancel',()=>{swipe=null;});
 page.addEventListener('pointerup',event=>{
  if(!swipe||swipe.id!==event.pointerId)return;
  const dx=event.clientX-swipe.x,dy=event.clientY-swipe.y,quick=Date.now()-swipe.at<700;swipe=null;
  if(!quick||Math.abs(dx)<60||Math.abs(dx)<Math.abs(dy)*1.5)return;
  const index=tabOrder.indexOf(page.id),next=index+(dx<0?1:-1);
  if(next<0||next>=tabOrder.length)return;
  swallowClick=true;setTimeout(()=>{swallowClick=false;},350);
  selectTab(tabOrder[next],dx<0?1:-1);
 });
}
document.addEventListener('click',event=>{if(swallowClick){swallowClick=false;event.preventDefault();event.stopPropagation();}},true);
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
function modal(title,iconName='',tone=''){if(mqttKeyboardDismiss){mqttKeyboardDismiss(false);mqttKeyboardDismiss=null;}const dialog=$('modal').querySelector('.dialog');dialog.querySelector('.dialoghead').append($('closemodal'));dialog.classList.remove('keyboard-open');$('modalbody').classList.remove('keyboard-open');modalReturnFocus=document.activeElement;pinKeyHandler=null;$('modaltitle').replaceChildren(...(iconName?[icon(iconName,tone)]:[]),title);$('modalbody').replaceChildren();$('modalmsg').textContent='';$('modal').hidden=false;return $('modalbody');}
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
function action(parent,label,fn,danger=false,iconName=''){let wrap=parent.querySelector('.actions');if(!wrap){wrap=document.createElement('div');wrap.className='actions';parent.append(wrap);}let b=document.createElement('button');b.type='button';if(iconName)b.append(icon(iconName));const caption=document.createElement('span');caption.textContent=label;b.append(caption);if(danger)b.className='danger';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){if(e.message==='AUTHENTICATION_AND_CSRF_REQUIRED'){authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);login();}else $('modalmsg').textContent=e.message;}finally{b.disabled=false;}};wrap.append(b);return b;}
function cancelAction(parent){const button=action(parent,'Batal',()=>dismissModal(),false,'');button.classList.add('secondary');return button;}
function section(parent,title,iconName,klass=''){
 const wrap=document.createElement('section');wrap.className='dialog-section';
 const heading=document.createElement('h4');if(iconName)heading.append(icon(iconName));heading.append(title);
 const actions=document.createElement('div');actions.className=`actions ${klass}`.trim();
 wrap.append(heading,actions);parent.append(wrap);return wrap;
}
function needLogin(){if(authenticated)return false;login();return true;}
function login(){
 let pin='',pending=false;
 const box=modal('PIN admin lokal','lock');text(box,'Masukkan 6 angka menggunakan keypad.');
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
 const box=modal('Pengaturan MQTT','cloud');text(box,'Memuat pengaturan MQTT...');let settings;
 try{settings=await get('/api/v2/mqtt/settings');}
 catch(e){if(e.message==='HTTP 401'){authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);login();return;}box.replaceChildren();text(box,`Pengaturan tidak termuat: ${e.message}. Tutup lalu coba lagi.`);return;}
 box.replaceChildren();
 if(!settings.editable){text(box,'Pengaturan MQTT tidak tersedia pada mode demo.');return;}
 const grid=document.createElement('div');grid.className='mqtt-settings-fields';
 const groupHeading=(title,iconName)=>{const heading=document.createElement('h4');heading.className='mqtt-group';heading.append(icon(iconName),title);grid.append(heading);};
 groupHeading('Koneksi broker','cloud');
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
 groupHeading('Akun CTRL / BULK (kosongkan bila tidak diubah)','lock');
 const controlUser=mqttField(grid,'User CTRL','','text','',128);controlUser.autocomplete='off';
 const controlPassword=mqttField(grid,'Pass CTRL','','password','',512);controlPassword.autocomplete='new-password';
 const bulkUser=mqttField(grid,'User BULK','','text','',128);bulkUser.autocomplete='off';
 const bulkPassword=mqttField(grid,'Pass BULK','','password','',512);bulkPassword.autocomplete='new-password';
 box.append(grid);
 function updateTransportVisibility(){websocketPathField.hidden=transport.value!=='websocket';}
 transport.addEventListener('change',updateTransportVisibility);
 updateTransportVisibility();
 const saveButton=action(box,'Simpan',async()=>{
  const saved=await post('/api/v2/mqtt/settings',{
   enabled:enabled.value==='on',host:host.value,port:Number(port.value),client_id:clientId.value,
   transport:transport.value,tls:tls.value==='on',websocket_path:websocketPath.value,
   control:{username:controlUser.value,password:controlPassword.value},
   bulk:{username:bulkUser.value,password:bulkPassword.value}
  });
  controlUser.value='';controlPassword.value='';bulkUser.value='';bulkPassword.value='';
  $('modalmsg').textContent=saved.apply_pending?'Tersimpan; koneksi MQTT diperbarui. Tunggu telemetry dan source evidence yang baru.':'Tersimpan.';
  renderData(snapshot);
 },false,'check');
 saveButton.classList.add('primary');
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
const profileChoices=[
 ['control','CONTROL','pause','Grafik 360° ditahan; hanya DoA, health, dan kontrol. Paling hemat link.'],
 ['balanced','BALANCED','activity','Grafik 360° minimal tiap 4 dtk, diperpanjang sesuai budget link.'],
 ['graph_u8','GRAPH','wave','Grafik 360° minimal tiap 2 dtk; paling banyak memakai link.']
];
$('profilebtn').onclick=()=>{
 if(needLogin())return;
 const box=modal('Profil telemetry','activity'),current=snapshot.config?.profile;
 text(box,'Grafik tetap dipause saat command berlangsung, source invalid, atau jalur Bulk belum siap.').className='hint';
 const list=document.createElement('div');list.className='option-list';box.append(list);
 for(const [value,name,iconName,description] of profileChoices){
  const button=document.createElement('button');button.type='button';button.className=`option${value===current?' on':''}`;
  const head=document.createElement('span');head.className='option-head';head.append(icon(iconName),name);
  if(value===current){const badge=document.createElement('em');badge.textContent='AKTIF';head.append(badge);}
  const note=document.createElement('small');note.textContent=description;button.append(head,note);
  if(value===current)button.setAttribute('aria-current','true');
  button.onclick=async()=>{button.disabled=true;try{await command('stream.set',{profile:value});}catch(e){$('modalmsg').textContent=e.message;}finally{button.disabled=false;}};
  list.append(button);
 }
};
const preferenceGroups=[
 ['theme','Mode','sun',[['dark','Gelap','moon'],['light','Terang','sun'],['night','Malam','eye']]],
 ['accent','Warna aksen','drop',[['teal','Teal'],['blue','Biru'],['amber','Amber']]],
 ['font','Jenis huruf','type',[['system','Sistem'],['serif','Serif'],['mono','Mono']]],
 ['blank_after_seconds','Layar hitam otomatis · tidak saat alarm error','screenoff',[['0','Mati'],['60','1 mnt'],['300','5 mnt'],['900','15 mnt']]]
];
// Each choice saves immediately; the server still validates and returns the stored set.
function preferences(){
 if(needLogin())return;
 const box=modal('Tampilan','sun');
 const current={theme:'dark',accent:'teal',font:'system',blank_after_seconds:0,...(snapshot.config?.preferences||{})};
 for(const [key,label,iconName,choices] of preferenceGroups){
  const group=document.createElement('div');group.className='pref-group';group.setAttribute('role','group');group.setAttribute('aria-label',label);
  const caption=document.createElement('span');caption.className='pref-caption';caption.append(icon(iconName),label);
  const chips=document.createElement('div');chips.className='chips';
  const options=[...choices];const value=String(current[key]);
  if(!options.some(([choice])=>choice===value))options.push([value,`${value} dtk`]);
  for(const [choice,title,choiceIcon] of options){
   const chip=document.createElement('button');chip.type='button';chip.className='chip';chip.dataset.value=choice;
   if(choiceIcon)chip.append(icon(choiceIcon));
   if(key==='accent'){const swatch=document.createElement('i');swatch.className=`swatch swatch-${choice}`;chip.append(swatch);}
   const name=document.createElement('span');name.textContent=title;if(key==='font')name.className=`font-${choice}`;chip.append(name);
   const on=choice===value;chip.classList.toggle('on',on);chip.setAttribute('aria-pressed',String(on));
   chip.onclick=async()=>{
    if(chip.classList.contains('on'))return;
    for(const item of chips.children)item.disabled=true;
    try{
     const saved=await post('/api/v2/display/preferences',{[key]:key==='blank_after_seconds'?Number(choice):choice});
     snapshot.config={...(snapshot.config||{}),preferences:saved};render(snapshot);
     for(const item of chips.children){const active=item===chip;item.classList.toggle('on',active);item.setAttribute('aria-pressed',String(active));}
     $('modalmsg').textContent=`${label.split(' · ')[0]}: ${title} tersimpan.`;
    }catch(e){if(e.message==='AUTHENTICATION_AND_CSRF_REQUIRED'){authenticated=false;csrf=null;render(snapshot);login();return;}$('modalmsg').textContent=e.message;}
    finally{for(const item of chips.children)item.disabled=false;}
   };
   chips.append(chip);
  }
  group.append(caption,chips);box.append(group);
 }
 box.querySelector('.chip')?.focus();
}
$('themebtn').onclick=preferences;
$('controlbtn').onclick=()=>{if(needLogin())return;const box=modal('Kontrol RDF','power');const caps=snapshot.capabilities||{};
 const sdr=section(box,'Stack SDR','cpu','grid2');
 action(sdr,'Start',()=>command('processing.set',{desired:'RUNNING'}),false,'play').disabled=!caps.processing;
 action(sdr,'Stop',()=>confirmOperation('Stop SDR service','processing.set',{desired:'STOPPED'}),true,'stop').disabled=!caps.processing;
 action(sdr,'Restart',()=>confirmOperation('Restart SDR service','service.restart',{}),true,'restart').disabled=!caps.restart;
 action(sdr,'Frekuensi',()=>frequency(),false,'wave').disabled=!caps.config_patch;
 const host=section(box,'Raspberry','power','grid2');
  const reboot=action(host,'Reboot',()=>prepareReboot(),true,'sync');
  reboot.disabled=!caps.reboot;
  const shutdown=action(host,'Shutdown',()=>prepareShutdown(),true,'power');
  shutdown.disabled=!caps.shutdown||!shutdownHistoryChecked;
  shutdown.title=!shutdownHistoryChecked?'Riwayat operasi belum tersedia.':shutdownUncertain?'Periksa Raspberry secara lokal sebelum mengulangi shutdown.':'';
  const rebootReason=!caps.helper_available?'Helper kontrol belum tersedia.':
   !caps.reboot?'Reboot lokal memerlukan mode controlled dan approval root (--reboot).':'';
  if(rebootReason){const note=text(host,rebootReason);note.className='hint';}
};
function confirmOperation(label,op,extra){const box=modal(label,'warn','bad');text(box,'Aksi ini mengganggu pemrosesan RDF. Bridge tetap hidup kecuali reboot OS. Pastikan kondisi operasi aman sebelum melanjutkan.');cancelAction(box);action(box,'Konfirmasi',()=>command(op,extra),true,'check');}
// The kiosk has no physical keyboard, so frequency entry uses an on-screen keypad. Range and
// policy checks stay on the Edge; this only builds a well-formed MHz number.
function frequency(){
 const box=modal('Frekuensi center + VFO0','wave');
 const start=snapshot.detection?.frequency_hz?(snapshot.detection.frequency_hz/1e6).toFixed(3):'';
 let value=start,fresh=true;
 const layout=document.createElement('div');layout.className='freq-layout';
 const side=document.createElement('div');side.className='freq-side';
 const readout=document.createElement('div');readout.className='freq-readout';readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');
 const number=document.createElement('strong'),unit=document.createElement('span');unit.textContent='MHz';readout.append(number,unit);
 const note=document.createElement('p');note.className='hint';note.textContent='Center dan VFO0 diset sama. File tersimpan belum berarti runtime terverifikasi.';
 side.append(readout,note);
 const pad=document.createElement('div');pad.className='freq-pad';pad.setAttribute('role','group');pad.setAttribute('aria-label','Keypad frekuensi');
 function refresh(){number.textContent=value||'--';number.classList.toggle('dim',fresh);apply.disabled=!/^\d{1,4}(\.\d{1,6})?$/.test(value)||Number(value)<=0;}
 function press(key){
  if(fresh&&key!=='back'){value='';fresh=false;}
  if(key==='back'){fresh=false;value=value.slice(0,-1);}
  else if(key==='clear')value='';
  else if(key==='.'){if(!value.includes('.'))value=(value||'0')+'.';}
  else if(value.replace('.','').length<10&&!(value.includes('.')&&value.split('.')[1].length>=6))value=value==='0'?key:value+key;
  refresh();
 }
 for(const key of ['1','2','3','4','5','6','7','8','9','.','0','back']){
  const button=document.createElement('button');button.type='button';
  if(key==='back'){button.append(icon('backspace'));button.setAttribute('aria-label','Hapus angka terakhir');}else button.textContent=key;
  button.onclick=()=>press(key);pad.append(button);
 }
 layout.append(side,pad);box.append(layout);
 const clear=action(side,'Kosong',()=>press('clear'),false,'xcircle');clear.classList.add('secondary');
 const apply=action(side,'Terapkan',()=>{const hz=Math.round(Number(value)*1e6);if(!Number.isFinite(hz)||hz<=0)throw new Error('Frekuensi tidak valid');return command('config.patch',{changes:{center_frequency_hz:hz,vfo0_frequency_hz:hz}});},false,'check');
 apply.classList.add('primary');
 pinKeyHandler=event=>{
  if(/^[0-9.]$/.test(event.key)){event.preventDefault();press(event.key);}
  else if(event.key==='Backspace'){event.preventDefault();press('back');}
  else if(event.key==='Delete'){event.preventDefault();press('clear');}
  else if(event.key==='Enter'&&!apply.disabled){event.preventDefault();apply.click();}
 };
 refresh();pad.querySelector('button')?.focus();
}
async function prepareReboot(){const box=modal('Reboot Raspberry','sync','bad');text(box,'Node akan offline. Admin harus unlock; approval root --reboot disimpan satu kali. Konfirmasi akhir tetap diperlukan. Health berhenti selama boot.');cancelAction(box);action(box,'Siapkan reboot',async()=>{const r=await command('system.reboot.prepare');$('modalmsg').textContent='Menunggu challenge...';let op=null;for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,300));const list=await get('/api/v2/operations/latest');op=list.find(x=>x.id===r.id);if(op&&['FAILED','APPLIED','REJECTED'].includes(op.stage))break;}
 // Challenges are deliberately not returned by public history. A local reboot is
 // issued through authenticated command result access (private endpoint below).
 const result=await post('/api/v2/operation/result',{id:r.id});
 if(result.stage!=='APPLIED'||!result.result?.challenge)throw new Error(result.result?.error||'Challenge belum tersedia');
 const cbox=modal('Konfirmasi reboot OS','warn','bad');text(cbox,'Konfirmasi dalam 30 detik. Reboot mulai setelah hasil dijurnal.');cancelAction(cbox);action(cbox,'REBOOT SEKARANG',()=>command('system.reboot.execute',{prepare_id:r.id,challenge:result.result.challenge}),true,'sync');
 },true,'sync');}
async function prepareShutdown(){
 const box=modal('Shutdown Raspberry','power','bad');
 text(box,'Raspberry akan dimatikan sepenuhnya; akses lokal diperlukan untuk menyalakannya lagi. Fitur memerlukan Admin PIN dan approval root --shutdown yang disimpan satu kali.');
 cancelAction(box);
 action(box,'Siapkan shutdown',async()=>{
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
  const cbox=modal('Konfirmasi shutdown OS','warn','bad');
  text(cbox,'Pi akan mati dalam 5 detik setelah jadwal diterima. Tidak ada bukti OS sudah berhenti; nyalakan kembali secara lokal.');
  cancelAction(cbox);
  action(cbox,'SHUTDOWN PI SEKARANG',async()=>{
   const executeId=`local-${Date.now()}-${crypto.randomUUID().slice(0,8)}`;
   setShutdownUncertainty(true);
   let sent;
   try{sent=await command('system.shutdown.execute',{id:executeId,prepare_id:prepared.id,challenge:result.result.challenge});}
   catch(error){await showShutdownOutcome(null,error.status>=400&&error.status<500?'REJECTED':'OUTCOME_UNKNOWN',error.message);return;}
   if(sent.id!==executeId){await showShutdownOutcome(null,'OUTCOME_UNKNOWN','ID operasi tidak cocok');return;}
   const outcome=await shutdownOutcome(executeId);
   await showShutdownOutcome(outcome,outcome?'OUTCOME_UNKNOWN':'ACK_TIMEOUT');
  },true,'power');
 },true,'power');
}
document.addEventListener('pointerdown',()=>{lastTouch=Date.now();if(wakeScreen()){swallowClick=true;setTimeout(()=>{swallowClick=false;},400);}},true);
document.addEventListener('keydown',event=>{lastTouch=Date.now();if(wakeScreen())event.stopImmediatePropagation();},true);
$('blankbtn').onclick=()=>enterBlank();
get('/api/v2/session').then(s=>{if(authenticated)return;authenticated=s.authenticated;csrf=s.csrf;lastAdminSessionRefresh=Date.now();render(snapshot);}).catch(()=>{});poll();
