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
 backspace:'M9 5h11v14H9l-6-7zM12 9l5 6M17 9l-5 6',
 radio:'M4 10h16v10H4zM7.5 10 16 4.5M9 13.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6zM14 14h3M14 17h3',
 x:'M6 6l12 12M18 6 6 18',minus:'M6 12h12',history:'M3.5 12a8.5 8.5 0 1 0 2.5-6M3 4v4.5h4.5M12 7.5V12l3 2',
 question:'M9.2 9a2.9 2.9 0 1 1 4 2.7c-.8.4-1.2 1-1.2 1.8v.6M12 17.5v.5'
};
function icon(name,extra=''){
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');
 svg.setAttribute('class',`ico${extra?` ${extra}`:''}`);const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',ICONS[name]||'');svg.append(path);return svg;
}
// Rewrites a button only when its label or icon changes, so a periodic render never swaps the
// node a finger is currently pressing.
function setButtonLabel(button,label,iconName){
 const key=`${label}\n${iconName||''}`;
 if(!button||button.dataset.labelKey===key)return;
 button.dataset.labelKey=key;button.dataset.label=label;const span=document.createElement('span');span.textContent=label;
 button.replaceChildren(...(iconName?[icon(iconName)]:[]),span);
}
function row(label,value,code,iconName){const r=document.createElement('div');r.className='row';const l=document.createElement('label');if(iconName)l.append(icon(iconName));l.append(label);const v=document.createElement('b');v.textContent=String(value??'--');if(code)v.className=good.includes(code)?'good':bad.includes(code)?'bad':'warn';r.append(l,v);return r;}
function rows(id,values){const e=$(id);e.replaceChildren(...values.map(v=>row(...v)));}
const mqttTopics=[
 ['KELUAR / RASPBERRY -> GROUND (STATUS UTAMA)',[
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
   caption.textContent='Topik keluar dari Raspberry ke Ground. Status kirim berlaku untuk broker utama, bukan broker cadangan. QoS 0 berarti write socket lokal tanpa ACK; QoS 1 berarti PUBACK broker, bukan konfirmasi pemrosesan Ground.';
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
// The backup broker mirrors the primary one. A missing block (older Edge) or a backup that is
// switched off reads as DISABLED and stays neutral; an enabled backup shows its raw client state.
function mqttBackupState(l){
 const b=l?.mqtt_backup;
 const enabled=!!b&&typeof b==='object'&&(b.enabled===true||(b.enabled==null&&typeof b.state==='string'&&b.state!=='DISABLED'));
 const state=enabled?String(b.state||'UNKNOWN'):'DISABLED';
 return {enabled,state,code:enabled?state:'',error:enabled&&b.error?String(b.error):''};
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
 const backup=mqttBackupState(l);label('mqttbackup',backup.state,backup.code);
 const backupLabel=$('mqttbackup');if(backupLabel)backupLabel.title=backup.error;
 setButtonLabel($('mqttbackupsettings'),'Cadangan',authenticated?'layers':'lock');
 setButtonLabel($('mqttsettings'),authenticated?'Atur':'Login',authenticated?'sliders':'lock');
 const node=s.node_id||'--',prefix=`${s.mode==='DEMO'?'sdr/demo/v2':'sdr/v2'}/${node}/`;
 if(prefix!==renderedTopicPrefix){renderedTopicPrefix=prefix;$('topicprefix').textContent=prefix;renderTopicList();}
 updateTopicStatuses(l.mqtt_topic_delivery||{});
}
const goodStages=new Set(['APPLIED','REBOOT_SCHEDULED','SHUTDOWN_SCHEDULED']);
const badStages=new Set(['FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED']);
const warnStages=new Set(['OUTCOME_UNKNOWN','PERSISTED_UNVERIFIED']);
// PPP_RESTART_REQUESTED is only an intermediate stage when the Edge runs the PPP recovery observer
// (link.ppp_recovery present); on an older Edge it is the last stage and must not pulse forever.
function stageTone(stage){return goodStages.has(stage)?'good':badStages.has(stage)?'bad':warnStages.has(stage)?'warn':stage==='PPP_RESTART_REQUESTED'&&!(snapshot.link&&'ppp_recovery' in snapshot.link)?'warn':'progress';}
let seenOperation=undefined,toastTimer=null;
function showToast(message,tone){
 const toast=$('toast');toast.textContent=message;toast.className=`toast ${tone}`;toast.hidden=false;
 if(toastTimer)clearTimeout(toastTimer);
 toastTimer=setTimeout(()=>{toast.hidden=true;toastTimer=null;},4500);
}
// ---- Operation wording. origin, summary, reason, operation_seq, last_remote_operation and
// remote_rejects are additive snapshot fields; every one of them may be missing on an older Edge.
const READ_OPS=new Set(['config.get','operation.get']);
const OP_LABELS={'config.get':'Baca config','config.patch':'Ubah config RF','processing.set':'Start/Stop SDR','service.restart':'Restart SDR','ppp.restart':'Restart T900',
 'system.reboot.prepare':'Siapkan reboot','system.reboot.execute':'Reboot','system.shutdown.prepare':'Siapkan shutdown','system.shutdown.execute':'Shutdown',
 'operation.get':'Status operasi','stream.set':'Profil telemetry'};
const ORIGINS={local:{letter:'L',name:'Lokal',upper:'LOKAL'},ground:{letter:'G',name:'Ground',upper:'GROUND'},backup:{letter:'C',name:'Cadangan',upper:'CADANGAN'}};
const PROFILE_NAMES={control:'CONTROL',balanced:'BALANCED',graph_u8:'GRAPH'};
// [label, divisor, decimals, unit]; the order is the display order.
const CONFIG_FIELDS={vfo0_frequency_hz:['Frekuensi VFO',1e6,3,'MHz'],center_frequency_hz:['Frekuensi center',1e6,3,'MHz'],vfo0_bandwidth_hz:['Bandwidth VFO',1e3,1,'kHz'],gain_db:['Gain',1,1,'dB'],vfo0_squelch_db:['Squelch VFO',1,1,'dB']};
// Short Indonesian hints; the raw code stays available (title / Riwayat) for troubleshooting.
const REASONS={
 PPP_RECOVERY_TIMEOUT:'PPP/Ground belum pulih',PPP_CYCLE_NOT_OBSERVED:'PPP tidak terlihat restart',
 T900_USB_MISSING:'T900 USB tidak terdeteksi',MQTT_NOT_RECOVERED:'PPP & ping pulih, MQTT utama belum',
 PPP_SERVICE_NOT_LOADED:'unit t900-ppp tidak ada',PPP_SERVICE_STATUS_UNKNOWN:'status unit t900-ppp tidak diketahui',
 PPP_SERVICE_NOT_ACTIVE:'unit t900-ppp tidak aktif',PPP_RESTART_PENDING:'job systemd t900-ppp masih antre',
 PPP_RESTART_NOT_APPROVED:'belum ada approval root --ppp-restart',PPP_RESTART_IN_PROGRESS:'restart T900 sebelumnya masih dipantau',
 PPP_RESTART_TOO_SOON:'tunggu 15 dtk sejak restart terakhir',BUSY:'operasi lain sedang berjalan',CONTROL_IN_PROGRESS:'operasi lain sedang berjalan',
 CLOCK_UNTRUSTED:'jam sistem belum tepercaya',SESSION_MISMATCH:'sesi/boot Edge berbeda',CONFIG_REVISION_CONFLICT:'revisi config sudah berubah',
 REMOTE_COMMANDS_DISABLED:'perintah remote nonaktif',EXPIRED_OR_INVALID_DEADLINE:'perintah kedaluwarsa',CAPABILITY_DISABLED:'fitur dinonaktifkan',
 AGENT_RESTARTED_RECONCILE_NO_REPLAY:'agent restart; hasil tidak diketahui',SYSTEMD_ACTION_FAILED:'aksi systemd gagal',
 SYSTEMD_ACTION_OUTCOME_UNKNOWN:'hasil systemd tidak diketahui',RPC_TIMEOUT:'helper tidak menjawab (timeout)',TIMEOUTERROR:'helper tidak menjawab (timeout)',HELPER_CLOSED:'koneksi helper terputus',LIFECYCLE_VERIFICATION_TIMEOUT:'verifikasi SDR timeout',
 CONFIG_CHANGED_BEFORE_WRITE:'config berubah sebelum ditulis',NATIVE_RUNTIME_EVIDENCE_INCOMPLETE:'bukti runtime belum lengkap',
 DEMO_NO_HARDWARE_CONTROL:'mode demo, tanpa kontrol hardware',REQUEST_RATE_LIMIT:'permintaan terlalu cepat',
 HELPER_UNAVAILABLE:'helper kontrol tidak berjalan; tidak dijalankan',HELPER_ACCESS_DENIED:'tidak ada izin ke socket helper',
 HELPER_TIMEOUT:'helper tidak menjawab (timeout)',HELPER_CONNECTION_LOST:'koneksi helper terputus',HELPER_INTERNAL_ERROR:'error di helper kontrol',
 INTERNAL_ERROR:'error internal Edge',INVALID_COMMAND_FIELD:'field perintah tidak valid',JOURNAL_UNAVAILABLE:'journal perintah tidak bisa ditulis',
 COMMAND_TOO_LARGE:'perintah terlalu besar (>4 KB)',COMMAND_NOT_JSON:'perintah bukan JSON',COMMAND_QUEUE_FULL:'antrean perintah penuh'
};
const REASON_STAGES=new Set(['FAILED','REJECTED','OUTCOME_UNKNOWN','EXPIRED','CONFLICT','CANCELLED']);
const STAGE_TEXT={ACCEPTED:'diproses…',APPLYING:'diproses…',VERIFYING:'diproses…',PPP_RESTART_REQUESTED:'restart diminta, memantau link…',
 APPLIED:'diterapkan',PERSISTED_UNVERIFIED:'tersimpan, belum terbukti',FAILED:'gagal',REJECTED:'ditolak',OUTCOME_UNKNOWN:'hasil belum pasti',
 EXPIRED:'kedaluwarsa',CONFLICT:'konflik',CANCELLED:'dibatalkan',REBOOT_SCHEDULED:'reboot dijadwalkan',SHUTDOWN_SCHEDULED:'shutdown dijadwalkan'};
// An Edge's own `message` explains codes this panel does not know yet.
function reasonLabel(code,op){const message=op?.result?.message;return REASONS[code]||(typeof message==='string'&&message)||String(code||'');}
function opReason(op){const reason=op?.reason??op?.result?.error??op?.result?.reason;return typeof reason==='string'?reason:'';}
function opChanges(op){const changes=op?.summary?.changes;return changes&&typeof changes==='object'&&!Array.isArray(changes)?changes:{};}
function decimalId(value,divisor,decimals){return typeof value==='number'&&Number.isFinite(value)?(value/divisor).toFixed(decimals).replace('.',','):value==null?null:String(value);}
function changeText(name,change){
 const [title,divisor,decimals,unit]=CONFIG_FIELDS[name]||[name,1,null,''];
 const show=value=>decimals==null?(value==null?null:String(value)):decimalId(value,divisor,decimals);
 const from=show(change?.from),to=show(change?.to),suffix=unit?` ${unit}`:'';
 if(to==null)return title;
 return from==null?`${title} → ${to}${suffix}`:from===to?`${title} ${to}${suffix}`:`${title} ${from} → ${to}${suffix}`;
}
function processingLabel(op){const to=opChanges(op).desired?.to;return to==='RUNNING'?'Start SDR':to==='STOPPED'?'Stop SDR':'';}
function profileName(value){return value==null?null:PROFILE_NAMES[value]||String(value).toUpperCase();}
// Short label for the CMD row and toasts.
function opLabel(op){
 if(!op)return '';
 if(op.op==='processing.set'&&processingLabel(op))return processingLabel(op);
 if(op.op==='stream.set'){const to=profileName(opChanges(op).profile?.to);if(to)return `Profil ${to}`;}
 if(op.op==='config.patch'){
  const names=Object.keys(opChanges(op));
  if(names.some(name=>name.endsWith('frequency_hz')))return 'Frekuensi';
  if(names.length===1&&CONFIG_FIELDS[names[0]])return CONFIG_FIELDS[names[0]][0];
 }
 return OP_LABELS[op.op]||String(op.op||'?');
}
// Longer old -> new wording for the remote banner and Riwayat.
function opSummary(op){
 if(!op)return '';
 const changes=opChanges(op);
 if(op.op==='config.patch'){
  const vfo=changes.vfo0_frequency_hz,order=Object.keys(CONFIG_FIELDS);
  // The panel writes center and VFO0 together; one frequency line says it.
  const names=Object.keys(changes).filter(name=>!(name==='center_frequency_hz'&&vfo&&vfo.to===changes[name]?.to))
   .sort((a,b)=>(order.indexOf(a)+1||99)-(order.indexOf(b)+1||99));
  if(names.length)return names.map(name=>changeText(name,changes[name])).join('; ');
 }
 if(op.op==='stream.set'&&changes.profile){
  const from=profileName(changes.profile.from),to=profileName(changes.profile.to);
  if(to)return from&&from!==to?`Profil ${from} → ${to}`:`Profil ${to}`;
 }
 return opLabel(op);
}
function stageText(op){
 const stage=op?.stage;
 if(stage==='APPLIED'&&String(op.op||'').endsWith('.prepare'))return 'disiapkan';
 if(stage==='APPLIED'&&op.op==='ppp.restart')return 'tersambung lagi';
 return STAGE_TEXT[stage]||(stageTone(stage)==='progress'?'diproses…':String(stage||'?'));
}
function clockText(ms){if(typeof ms!=='number'||!Number.isFinite(ms))return '--:--:--';const t=new Date(ms),pad=v=>String(v).padStart(2,'0');return `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;}
function setOriginChip(el,origin){
 const o=ORIGINS[origin];if(!el)return el;
 el.hidden=!o;el.className=`originchip origin-${o?origin:'none'}`;el.textContent=o?o.letter:'';
 if(o){el.title=`Dari ${o.name}`;el.setAttribute('aria-label',`Asal ${o.name}`);}else{el.removeAttribute('title');el.removeAttribute('aria-label');}
 return el;
}
// Adds the Indonesian hint to a known rejection code, like the backup-broker errors; the login
// flow still sees AUTHENTICATION_AND_CSRF_REQUIRED unchanged.
function withReasonHint(error){if(error&&REASONS[error.message])error.message=`${error.message}: ${REASONS[error.message]}`;return error;}
function remoteFieldsSupported(s){return !!s&&Object.prototype.hasOwnProperty.call(s,'last_remote_operation');}
// Stage codes stay verbatim in the chip; colour only groups them. A finished stage is the journal
// outcome, not proof that runtime state changed (e.g. PERSISTED_UNVERIFIED stays amber).
function renderOperation(op,s){
 const chip=$('cmdstage'),reasonEl=$('cmdreason'),reason=op&&REASON_STAGES.has(op.stage)?opReason(op):'';
 setOriginChip($('cmdorigin'),op?.origin);
 $('command').textContent=op?opLabel(op):'Belum ada operasi';
 $('command').title=op?`${opSummary(op)} (${op.op})`:'';
 // A restart that recovered after the 120 s limit keeps its journal stage (chip stays verbatim);
 // while the Edge still shows that recovery, the hint says it recovered instead of the timeout text.
 const recoveryNow=currentRecovery(s),lateRecovery=!!op&&op.op==='ppp.restart'&&recoveryLate(recoveryNow,op.id);
 if(reasonEl){
  reasonEl.textContent=lateRecovery?`pulih setelah batas ${Number.isFinite(recoveryNow.timeout_s)?recoveryNow.timeout_s:120} dtk`:reason?reasonLabel(reason):'';reasonEl.title=reason;
  reasonEl.className=`cmdreason ${lateRecovery?'good':op?stageTone(op.stage):''}`.trim();
 }
 chip.hidden=!op;
 if(op){chip.textContent=op.stage;chip.className=`stagechip ${stageTone(op.stage)}`;}
 // Only a real snapshot sets the baseline, so a reload never toasts an old operation.
 if(s?.snapshot_seq==null)return;
 const key=op?`${op.id}:${op.stage}`:null;
 if(seenOperation===undefined){seenOperation=key;return;}
 if(key===seenOperation)return;
 seenOperation=key;
 if(!op||READ_OPS.has(op.op)||stageTone(op.stage)==='progress')return;
 // Ground/backup mutations are announced by the remote banner, which stays above dialogs.
 if(remoteFieldsSupported(s)&&(op.origin==='ground'||op.origin==='backup'))return;
 // The T900 recovery card already shows this restart's result.
 if(op.op==='ppp.restart'&&recoveryCardShows(s,op.id))return;
 showToast(`${opLabel(op)}: ${op.stage}${reason?` · ${reasonLabel(reason,op)}`:''}`,stageTone(op.stage));
}
// ---- Remote-command banner: Ground or the backup broker changed something on this Edge. ----
// The host is created if a stale index.html lacks it, so a mismatched page never stops loading.
const noticesHost=$('notices')||document.querySelector('.shell').appendChild(Object.assign(document.createElement('div'),{id:'notices'}));
// The toast lives in the same column (CSS order puts it last), so a banner or the recovery card
// pushes it down instead of covering it. While a dialog is open it stays hidden, as before.
noticesHost.append($('toast'));
let remoteSeen=undefined,remoteTimer=null,rejectSeen=undefined;
const remoteBanner=(()=>{
 const root=document.createElement('section');root.className='notice remote';root.hidden=true;root.setAttribute('role','status');root.setAttribute('aria-live','polite');
 const chip=document.createElement('span');
 const body=document.createElement('div');body.className='notice-text';
 const title=document.createElement('b');title.className='notice-title';
 const titleText=document.createElement('span'),time=document.createElement('time');title.append(titleText,time);
 // Only the summary is clamped (with an ellipsis); the outcome has its own line and is never cut.
 const summary=document.createElement('span');summary.className='notice-body';
 const stage=document.createElement('em');stage.className='notice-stage';
 body.append(title,summary,stage);
 const close=document.createElement('button');close.type='button';close.className='notice-close';close.textContent='Tutup';
 root.append(chip,body,close);noticesHost.append(root);
 return {root,chip,titleText,time,summary,stage,close};
})();
function hideRemoteBanner(){remoteBanner.root.hidden=true;if(remoteTimer){clearTimeout(remoteTimer);remoteTimer=null;}}
remoteBanner.close.onclick=hideRemoteBanner;
function showRemoteBanner(op){
 const b=remoteBanner,tone=stageTone(op.stage),origin=ORIGINS[op.origin],reason=REASON_STAGES.has(op.stage)?opReason(op):'';
 setOriginChip(b.chip,op.origin);
 b.root.dataset.tone=tone;
 b.titleText.textContent=`Perintah dari ${origin?origin.upper:'REMOTE'}`;
 b.time.textContent=clockText(op.updated_ms);
 const summary=opSummary(op);
 b.summary.textContent=summary;b.summary.title=summary;
 b.stage.textContent=`${stageText(op)}${reason?`: ${reasonLabel(reason,op)}`:''}`;b.stage.className=`notice-stage ${tone}`;
 b.stage.title=reason;
 b.root.hidden=false;
 if(remoteTimer){clearTimeout(remoteTimer);remoteTimer=null;}
 if(op.stage==='APPLIED')remoteTimer=setTimeout(hideRemoteBanner,10000);
 wakeScreen();
}
// Returns the operation it announced (or null) so a rejection in the same snapshot can compare times.
function renderRemoteBanner(s){
 if(s.snapshot_seq==null||!remoteFieldsSupported(s))return null;
 const raw=s.last_remote_operation,op=raw&&typeof raw==='object'&&raw.id?raw:null;
 const key=op?`${op.id}:${op.stage}`:null;
 // Baseline from the first real snapshot: a reload or a new panel never re-announces.
 if(remoteSeen===undefined){remoteSeen=key;return null;}
 if(key===remoteSeen)return null;
 remoteSeen=key;
 if(op&&!READ_OPS.has(op.op)){showRemoteBanner(op);return op;}
 return null;
}
// A rejected Ground/backup command is never journaled, so it only appears in remote_rejects.
// Entries are matched by identity, not by time, so a clock step cannot hide or replay one.
function rejectKey(entry){return `${entry.t}|${entry.origin}|${entry.op}|${entry.error}`;}
function renderRemoteRejects(s,announced){
 if(s.snapshot_seq==null||!Array.isArray(s.remote_rejects))return;
 const list=s.remote_rejects.filter(entry=>entry&&typeof entry==='object'),keys=new Set(list.map(rejectKey));
 if(rejectSeen===undefined){rejectSeen=keys;return;}
 const fresh=list.filter(entry=>!rejectSeen.has(rejectKey(entry))&&!READ_OPS.has(entry.op));
 rejectSeen=keys;
 if(!fresh.length)return;
 const time=entry=>typeof entry.t==='number'&&Number.isFinite(entry.t)?entry.t:-Infinity;
 const latest=fresh.reduce((a,b)=>time(b)>time(a)?b:a);
 if(announced&&typeof announced.updated_ms==='number'&&announced.updated_ms>time(latest))return;
 showRemoteBanner({id:`rej-${latest.t}`,op:String(latest.op||''),origin:latest.origin,stage:'REJECTED',reason:String(latest.error||''),updated_ms:latest.t,summary:null});
}
// ---- T900/PPP recovery progress (link.ppp_recovery). Inside the open dialog when there is
// one, otherwise a banner above the page. The Edge keeps it 90 s after it finishes. ----
const RECOVERY_STEPS=[['requested','Restart diminta'],['cycled','PPP turun/naik'],['ppp_up','PPP UP'],['peer_reply','Ping Ground'],['mqtt_ready','MQTT utama']];
const RECOVERY_END=new Set(['DONE','TIMEOUT','FAILED']);
const RECOVERY_HEAD={REQUESTED:'Menunggu PPP turun/naik…',RESTARTING:'PPP sedang restart…',PPP_UP:'PPP UP, ping Ground…',PEER_OK:'Ground membalas, tunggu MQTT…',DONE:'Tersambung ke Ground ✓',FAILED:'Restart T900 gagal'};
// A PPP restart cannot help while the radio itself is gone (pppd cannot even open the device).
// Only a missing alias means the radio is gone; IDENTITY_MISMATCH is usually a site VID/PID/port setting.
const USB_HINTS={MISSING:'T900 USB tidak terdeteksi — restart PPP tidak membantu; cek kabel/daya radio.'};
const PPP_APPROVAL_HINT='Perlu approval root: sudo rdf-node controls approve --ppp-restart',HELPER_MISSING_HINT='Helper kontrol belum tersedia.';
let recoveryDismissed=null,recoveryNote={id:null,text:'',at:0},recoveryWokeId=null;
const recovery=(()=>{
 // Only the headline is live: the elapsed counter changes every poll and must not be re-read aloud.
 const root=document.createElement('section');root.className='recovery';root.hidden=true;root.setAttribute('aria-label','Progres restart T900');
 const head=document.createElement('div');head.className='recovery-head';
 const title=document.createElement('b');title.append(icon('radio'),'Restart T900');
 const origin=document.createElement('span'),headline=document.createElement('span'),elapsed=document.createElement('span');
 headline.className='recovery-headline';headline.setAttribute('role','status');headline.setAttribute('aria-live','polite');elapsed.className='recovery-elapsed';
 const close=document.createElement('button');close.type='button';close.className='recovery-close';close.setAttribute('aria-label','Sembunyikan progres restart T900');close.append(icon('x'));
 head.append(title,origin,headline,elapsed,close);
 const list=document.createElement('ol');list.className='recovery-steps';
 const steps=RECOVERY_STEPS.map(([key,name])=>{
  const item=document.createElement('li'),dot=document.createElement('span'),caption=document.createElement('span'),sub=document.createElement('small');
  dot.className='rdot';caption.className='rlabel';caption.textContent=name;sub.className='rsub';sub.textContent='nonaktif';sub.hidden=true;
  item.append(dot,caption,sub);list.append(item);return {key,name,item,dot,sub,state:null};
 });
 // Reason, retry note and hints are separate lines that wrap; none of them is clamped.
 const foot=document.createElement('div');foot.className='recovery-foot';
 const reason=document.createElement('div');reason.className='recovery-reason';
 const retry=document.createElement('button');retry.type='button';retry.className='recovery-retry';
 const retryLabel=document.createElement('span');retryLabel.textContent='Coba lagi';retry.append(icon('sync'),retryLabel);
 foot.append(reason,retry);root.append(head,list,foot);noticesHost.append(root);
 return {root,origin,headline,elapsed,close,steps,foot,reason,retry,linesKey:''};
})();
// No wall-clock linger test here: the Pi has no RTC and NTP over the restored link can step the
// clock by hours. The Edge drops the record 90 s after it ends, on its monotonic clock.
function currentRecovery(s){
 const r=s?.link?.ppp_recovery;
 return r&&typeof r==='object'&&typeof r.phase==='string'?r:null;
}
function recoveryRunning(){const r=currentRecovery(snapshot);return !!r&&!RECOVERY_END.has(r.phase);}
// The helper timed out or its socket failed: systemd may still have restarted the unit.
function recoveryUncertain(r){return !!r&&r.phase==='FAILED'&&r.stage==='OUTCOME_UNKNOWN';}
// The link came back after the 120 s limit; the journal/ACK keep their timeout stage.
function recoveryLate(r,id){return !!r&&r.phase==='DONE'&&r.late===true&&(id===undefined||r.id===id);}
// Hiding a running card only lasts until it finishes, so the outcome is always seen.
function recoveryHidden(r){return !r||(recoveryDismissed?.id===r.id&&(recoveryDismissed.end||!RECOVERY_END.has(r.phase)));}
function recoveryCardShows(s,id){const r=currentRecovery(s);return !!r&&r.id===id&&!recoveryHidden(r);}
function usbHint(s){return USB_HINTS[s?.link?.usb]||'';}
// The primary link is fully back: PPP up, Ground answers ping and primary MQTT is ready (or off).
function linkHealthy(s){
 const l=s?.link||{},ctrl=l.mqtt_control||{};
 return l.ppp==='UP'&&l.ppp_probe==='REPLY'&&(ctrl.ready===true||(ctrl.state||'DISABLED')==='DISABLED');
}
function pppRestartBlockedHint(caps){return caps.ppp_restart?'':caps.helper_available?PPP_APPROVAL_HINT:HELPER_MISSING_HINT;}
function placeRecovery(){
 const slot=$('modalrecovery'),modalOpen=!!slot&&!$('modal').hidden,host=modalOpen?slot:noticesHost;
 if(recovery.root.parentNode!==host)host.append(recovery.root);
 recovery.root.classList.toggle('in-modal',modalOpen);
 recovery.root.classList.toggle('compact',modalOpen&&$('modal').dataset.recovery==='compact');
}
function setStepState(step,state){
 if(step.state===state)return;
 step.state=state;step.item.dataset.state=state;
 const glyph={done:'check',fail:'x',off:'minus',unknown:'question'}[state];
 step.dot.replaceChildren(...(glyph?[icon(glyph)]:[]));
 step.sub.hidden=state!=='off';
}
function setRecoveryLines(c,lines){
 const key=JSON.stringify(lines);if(key===c.linesKey)return;c.linesKey=key;
 c.reason.replaceChildren(...lines.map(([text,tone,title])=>{const line=document.createElement('span');line.className=`rline${tone?` ${tone}`:''}`;line.textContent=text;if(title)line.title=title;return line;}));
}
function renderRecovery(s){
 const c=recovery,r=currentRecovery(s),end=!!r&&RECOVERY_END.has(r.phase);
 const hidden=recoveryHidden(r);
 c.root.hidden=hidden;placeRecovery();
 if(hidden)return;
 const uncertain=recoveryUncertain(r),late=recoveryLate(r);
 const tone=r.phase==='DONE'?'good':r.phase==='TIMEOUT'||uncertain?'warn':r.phase==='FAILED'?'bad':'progress';
 c.root.dataset.tone=tone;c.root.dataset.phase=r.phase;
 setOriginChip(c.origin,r.origin);
 const limit=Number.isFinite(r.timeout_s)?r.timeout_s:120,seconds=Number.isFinite(r.elapsed_s)?Math.floor(r.elapsed_s):null;
 c.headline.textContent=r.phase==='TIMEOUT'?`Belum tersambung dalam ${limit} dtk`:uncertain?'Hasil restart T900 belum pasti':RECOVERY_HEAD[r.phase]||r.phase;
 c.elapsed.textContent=seconds==null?'':end?`${seconds} dtk`:`${seconds} / ${limit} dtk`;
 const steps=r.steps&&typeof r.steps==='object'?r.steps:{};let pointer=false;
 for(const step of c.steps){
  const value=steps[step.key];let state;
  if(step.key==='mqtt_ready'&&value===null&&r.phase!=='FAILED')state='off';
  else if(value===true)state='done';
  else if(!pointer){pointer=true;state=uncertain?'unknown':r.phase==='TIMEOUT'||r.phase==='FAILED'?'fail':end?'pending':'active';}
  else state='pending';
  setStepState(step,state);
 }
 const caps=s.capabilities||{},healthy=linkHealthy(s),usb=usbHint(s);
 const reason=typeof r.reason==='string'&&r.reason?r.reason:'';
 const note=recoveryNote.id===r.id&&Date.now()-recoveryNote.at<15000?recoveryNote.text:'';
 const lines=[];
 if(late)lines.push([`Tersambung lagi setelah batas ${limit} dtk; CMD tetap ${r.stage||'OUTCOME_UNKNOWN'}.`,'good',reason]);
 else if(reason&&!(reason==='T900_USB_MISSING'&&usb))lines.push([REASONS[reason]?`${REASONS[reason]} · ${reason}`:reason,tone==='progress'?'':tone,reason]);
 if(usb)lines.push([usb,'warn',s.link.usb]);
 // 'Coba lagi' re-sends ppp.restart. It is offered only when it can run and the link is still
 // down: restarting a link that just came back would drop it again.
 const retryPhase=r.phase==='TIMEOUT'||uncertain,busy=!!c.retry.dataset.busy;
 const canRetry=retryPhase&&!healthy&&!!caps.ppp_restart;
 if(retryPhase&&healthy&&!late)lines.push(['Link sudah sehat lagi (PPP, ping, MQTT) — tidak perlu restart.','good']);
 else if(retryPhase&&!caps.ppp_restart)lines.push([pppRestartBlockedHint(caps),'']);
 if(note)lines.push([note,'']);
 setRecoveryLines(c,lines);
 c.retry.hidden=!(canRetry||busy);
 if(!busy){c.retry.disabled=false;c.retry.removeAttribute('title');}
 c.foot.hidden=c.retry.hidden&&!lines.length;
}
recovery.close.onclick=()=>{const r=currentRecovery(snapshot);if(r)recoveryDismissed={id:r.id,end:RECOVERY_END.has(r.phase)};renderRecovery(snapshot);};
recovery.retry.onclick=async()=>{
 if(needLogin())return;
 const r=currentRecovery(snapshot),button=recovery.retry;
 if(!r||!snapshot.capabilities?.ppp_restart||linkHealthy(snapshot)||button.dataset.busy)return;
 // Without the radio on USB a fresh pppd cannot open the device; ask first, with the warning.
 if(usbHint(snapshot)){confirmPppRestart();return;}
 button.dataset.busy='1';button.disabled=true;recoveryNote={id:r.id,text:'Mengirim restart T900…',at:Date.now()};renderRecovery(snapshot);
 try{const result=await command('ppp.restart');recoveryNote={id:r.id,text:`Terkirim: ${result.stage}`,at:Date.now()};}
 catch(e){
  if(e.message==='AUTHENTICATION_AND_CSRF_REQUIRED'){authenticated=false;csrf=null;lastAdminSessionRefresh=0;recoveryNote={id:null,text:'',at:0};render(snapshot);login();}
  else recoveryNote={id:r.id,text:withReasonHint(e).message,at:Date.now()};
 }
 finally{delete button.dataset.busy;renderRecovery(snapshot);}
};
// ---- Riwayat: the last 10 mutations, refreshed while open when an operation stage changes. ----
let historyView=null;
function historyKey(s){return s.operation_seq!=null?`seq:${s.operation_seq}`:s.last_operation?`${s.last_operation.id}:${s.last_operation.stage}`:'';}
function historyRow(op){
 const item=document.createElement('div');item.className='history-row';item.setAttribute('role','listitem');
 const time=document.createElement('time');time.textContent=clockText(op.updated_ms);
 const chip=setOriginChip(document.createElement('span'),op.origin);
 if(chip.hidden){chip.hidden=false;chip.style.visibility='hidden';}
 const main=document.createElement('span');main.className='history-main';
 const title=document.createElement('b');title.textContent=opSummary(op);title.title=String(op.op||'');main.append(title);
 const reason=opReason(op);
 if(reason){const note=document.createElement('small');note.className=`history-reason ${stageTone(op.stage)}`;note.textContent=REASONS[reason]?`${REASONS[reason]} · ${reason}`:reason;main.append(note);}
 const stage=document.createElement('span');stage.className=`stagechip ${stageTone(op.stage)}`;stage.textContent=op.stage||'?';
 item.append(time,chip,main,stage);return item;
}
function renderHistoryRejects(s){
 const view=historyView;if(!view)return;
 const list=Array.isArray(s.remote_rejects)?s.remote_rejects.filter(entry=>entry&&typeof entry==='object'):[];
 view.rejects.hidden=!list.length;
 if(!list.length)return;
 const latest=list.reduce((a,b)=>typeof b.t==='number'&&b.t>(typeof a.t==='number'?a.t:-1)?b:a,list[0]);
 const origin=ORIGINS[latest.origin]?.upper||'REMOTE',error=String(latest.error||'?');
 view.rejects.replaceChildren(icon('warn'),`${list.length} perintah remote ditolak · terakhir ${clockText(latest.t)} ${origin} ${OP_LABELS[latest.op]||latest.op||''}: ${REASONS[error]?`${REASONS[error]} (${error})`:error}`);
}
async function loadHistory(view){
 if(view.loading){view.again=true;return;}
 view.loading=true;
 try{
  const ops=await get('/api/v2/operations/latest');
  if(historyView!==view)return;
  if(!Array.isArray(ops))throw new Error('format tidak valid');
  const items=ops.filter(op=>op&&typeof op==='object'&&!READ_OPS.has(op.op)).slice(0,10);
  view.list.replaceChildren(...items.map(historyRow));
  view.status.hidden=items.length>0;view.status.textContent=items.length?'':'Belum ada perintah yang mengubah sistem.';
 }catch(e){if(historyView===view){view.status.hidden=false;view.status.textContent=`Riwayat tidak termuat: ${e.message}. Tutup lalu coba lagi.`;}}
 finally{view.loading=false;if(view.again&&historyView===view){view.again=false;void loadHistory(view);}}
}
function openHistory(){
 const box=modal('Riwayat perintah','history');
 const rejects=document.createElement('p');rejects.className='history-rejects';rejects.hidden=true;
 const status=text(box,'Memuat riwayat…');status.className='hint';
 const list=document.createElement('div');list.className='history-list';list.setAttribute('role','list');
 box.prepend(rejects);box.append(list);
 const view={rejects,status,list,key:historyKey(snapshot),loading:false,again:false};historyView=view;
 renderHistoryRejects(snapshot);void loadHistory(view);
}
function refreshHistory(s){
 if(!historyView||$('modal').hidden)return;
 renderHistoryRejects(s);
 const key=historyKey(s);if(key!==historyView.key){historyView.key=key;void loadHistory(historyView);}
}
// Nav badges point at the tab that explains a problem; they never mark anything healthy.
function tabAlerts(s){
 const l=s.link||{},h=s.host||{},q=s.daq||{},c=s.config||{},p=s.processing||{};
 const mqtt=[l.mqtt_control?.state,l.mqtt_bulk?.state];
 // An enabled backup that is not connected only asks for attention; it never outranks a primary
 // ERROR, and an intentionally disabled backup adds nothing.
 const backup=mqttBackupState(l),backupDown=backup.enabled&&backup.state!=='CONNECTED';
 const link=mqtt.includes('ERROR')?'bad':(l.ppp!=='UP'||l.usb!=='PRESENT'||['NO_REPLY','ERROR'].includes(l.ppp_probe)||mqtt.includes('CONNECTING')||backupDown)?'warn':'';
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
 renderOperation(s.last_operation,s);
 const alerts=s.active_alerts||[];const a=alerts.find(x=>x.severity==='error')||alerts[0];
 $('alert').textContent=a?a.text:'Status lokal normal';$('alertbox').className=`alert ${a?(a.severity==='error'?'bad':'warn'):'good'}`;
 $('alerticon').textContent=a?'!':'\u2713';$('temp').textContent=`${fmt(h.temperature_c)}\u00b0C`;
 $('focus-angle-label').textContent=$('angle-label').textContent;$('focusangle').textContent=$('angle').textContent;$('focusangle').className=$('angle').className;
 $('focusage').textContent=$('age').textContent;$('focusfreq').textContent=$('freq').textContent;
 label('focusppp',`PPP ${l.ppp||'--'}`,l.ppp);label('focusmqtt',`MQTT ${l.mqtt_control?.state||'DISABLED'}`,l.mqtt_control?.state);label('focusdaq',`DAQ ${q.state==='HEALTHY'?'SINKRON':q.state||'UNKNOWN'}`,q.state);
 $('focusalert').textContent=a?a.text:'';$('focusalert').className=`focus-alert ${a?(a.severity==='error'?'bad':'warn'):''}`;
 const backup=mqttBackupState(l);
 if(!linkData){rows('linkrows',[["T900 USB",l.usb,l.usb,'plug'],["PPP / interface",`${l.ppp||'--'} / ${l.interface||'--'}`,l.ppp,'chain'],[`Ping ${l.ppp_peer||'peer'}`,pppProbeNames[l.ppp_probe]||'BELUM DICEK',l.ppp_probe,'radar'],["MQTT CTRL / BULK",`${l.mqtt_control?.state||'--'} / ${l.mqtt_bulk?.state||'--'}`,mqttPairState(l),'cloud'],["MQTT cadangan",backup.state,backup.code,'layers'],["TX / RX (PPP/IP)",`${fmt(l.tx_kbit_s,2)} / ${fmt(l.rx_kbit_s,2)} kbit/s`,'','updown']]);}
 else{
  // systemd state of t900-ppp.service; absent on an older Edge.
  const svc=l.ppp_service&&typeof l.ppp_service==='object'?l.ppp_service:null,svcState=typeof svc?.state==='string'?svc.state:'';
  const svcText=svcState?`${svcState}${svc.restarts!=null?` · restart ${svc.restarts}`:''}`:'--';
  const svcCode=svcState==='ACTIVE'?'UP':svcState==='FAILED'?'FAILED':svcState&&svcState!=='UNKNOWN'?svcState:'';
  rows('linkrows',[["Layanan PPP",svcText,svcCode,'radio'],["Profil diminta",l.profile,'','activity'],["Grafik pause",l.bulk_pause||'STREAMING','','pause'],["Queue CTRL / BULK",`${l.mqtt_control?.depth||0} / ${l.mqtt_bulk?.depth||0}`,'','layers'],["Parse / angular drop",`${d.parse_errors||0} / ${l.angular_aborted||0}`,'','xcircle'],["Error cadangan",backup.error||'--','','warn']]);
 }
 const syn=q.sync||{},syncFlags=[syn.frame,syn.sample_delay,syn.iq];
 const syncCode=syncFlags.every(flag=>flag===true)?'SYNCED':syncFlags.includes(false)?'DEGRADED':'UNKNOWN';
 if(!hostPage) rows('sysrows',[["Engine / desired",`${p.observed||'--'} / ${p.desired||'BELUM DIAMBIL'}`,p.observed==='RUNNING'?'HEALTHY':p.observed,'cpu'],["Frame / Delay / IQ",`${syn.frame??'?'} / ${syn.sample_delay??'?'} / ${syn.iq??'?'}`,syncCode,'sync'],["Frame / progress",`${q.frame_index??'--'} / ${q.frame_progressing?'MAJU':'BELUM'}`,q.frame_progressing?'HEALTHY':'UNKNOWN','film'],["Drop total / delta",`${q.dropped_frames??'--'} / ${q.drop_delta??'--'}`,'','xcircle'],["DAQ umur / USB SDR",`${age(q.source_age_ms)} / ${h.usb_count??'--'} terdeteksi`,'','clock']]);
 else rows('sysrows',[["CPU / RAM",`${fmt(h.cpu_percent)}% / ${fmt(h.memory_percent)}%`,'','gauge'],["Suhu / disk kosong",`${fmt(h.temperature_c)} C / ${fmt(h.disk_free_percent)}%`,'','thermo'],["Throttle / under-voltage",`${h.throttled??'unknown'} / ${h.undervoltage??'unknown'}`,'','bolt'],["Uptime",h.uptime_s==null?'--':`${Math.floor(h.uptime_s/60)} menit`,'','power'],["Jam sistem",h.clock_state,h.clock_state,'clock']]);
 rows('configrows',[["Node / profil",`${s.node_id||'--'} / ${c.profile||'--'}`,'','pin'],["Source / MQTT",`${c.source_configured?'OK':'SETUP'} / ${c.mqtt_configured?'CONFIGURED':'OFF'}`,'','folder'],["RF revision / proof",`${c.sdr_revision??'--'} / ${c.proof||'--'}`,'','hash'],["Akses / helper",`${authenticated?'ADMIN':'READ ONLY'} / ${s.capabilities?.helper_available?'SIAP':'OFF'}`,'','shield'],["Versi / kontrak",`${s.version||'--'} / ${s.contract||'--'}`,'','hash']]);
 setButtonLabel($('admin'),authenticated?'Logout':'Login',authenticated?'unlock':'lock');$('adminchip').hidden=!authenticated;
 const prefs={theme:'dark',accent:'teal',font:'system',...(c.preferences||{})};
 document.body.classList.toggle('light',prefs.theme==='light');document.body.classList.toggle('night',prefs.theme==='night');
 document.body.dataset.accent=prefs.accent;document.body.dataset.font=prefs.font;
 renderData(s);
 renderTabAlerts(s);
 renderRemoteRejects(s,renderRemoteBanner(s));
 renderRecovery(s);
 const usbWarn=$('pppusbwarn');if(usbWarn)setUsbWarning(usbWarn,s);
 refreshHistory(s);
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
// that tap is swallowed; a new error alert, a running T900 recovery or a new remote command
// (showRemoteBanner) wakes it automatically.
let blankErrors=new Set();
function errorAlertCodes(){return new Set((snapshot.active_alerts||[]).filter(alert=>alert.severity==='error').map(alert=>alert.code||alert.text));}
// The clock is the Pi system time; when the Edge reports it untrusted it is marked, not hidden.
function updateBlankClock(){
 const now=new Date(),pad=value=>String(value).padStart(2,'0');
 $('blanktime').textContent=`${pad(now.getHours())}:${pad(now.getMinutes())}`;
 let date='';try{date=now.toLocaleDateString('id-ID',{weekday:'long',day:'numeric',month:'long'});}catch{date=now.toDateString();}
 $('blankdate').textContent=date;
 const trusted=snapshot.host?.clock_trusted===true;
 $('blankclock').hidden=trusted;$('blank').classList.toggle('untrusted',!trusted);
}
function enterBlank(){blankErrors=errorAlertCodes();updateBlankClock();$('blank').hidden=false;}
function wakeScreen(){if($('blank').hidden)return false;$('blank').hidden=true;lastTouch=Date.now();void refreshSpectrum(snapshot);return true;}
// A running recovery wakes the screen once per restart id; a manual 'Layar hitam' afterwards
// sticks, while automatic blanking stays off until the recovery ends.
setInterval(()=>{
 const stale=Date.now()-lastProgress>5000;$('stale').hidden=!stale;
 if(stale)$('stalereason').textContent=Date.now()-lastApi>5000?'API lokal tidak merespons.':'API hidup, snapshot tidak bergerak.';
 const sec=snapshot.config?.preferences?.blank_after_seconds||0,errors=errorAlertCodes(),running=currentRecovery(snapshot),recovering=recoveryRunning();
 if(recovering&&running.id!==recoveryWokeId){recoveryWokeId=running.id;wakeScreen();}
 if($('blank').hidden){if(sec>0&&Date.now()-lastTouch>sec*1000&&!errors.size&&!recovering)enterBlank();}
 else if([...errors].some(code=>!blankErrors.has(code)))wakeScreen();else updateBlankClock();
},250);
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
function modal(title,iconName='',tone=''){if(mqttKeyboardDismiss){mqttKeyboardDismiss(false);mqttKeyboardDismiss=null;}const dialog=$('modal').querySelector('.dialog');dialog.querySelector('.dialoghead').append($('closemodal'));dialog.classList.remove('keyboard-open');$('modalbody').classList.remove('keyboard-open');modalReturnFocus=document.activeElement;pinKeyHandler=null;$('modaltitle').replaceChildren(...(iconName?[icon(iconName,tone)]:[]),title);$('modalbody').replaceChildren();$('modalbody').scrollTop=0;$('modalmsg').textContent='';historyView=null;$('modal').dataset.recovery='full';$('modal').hidden=false;placeRecovery();return $('modalbody');}
function dismissModal(){if(mqttKeyboardDismiss)mqttKeyboardDismiss(false);$('modal').hidden=true;historyView=null;placeRecovery();pinKeyHandler=null;const target=modalReturnFocus;modalReturnFocus=null;if(target&&typeof target.focus==='function')target.focus();}
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
 const box=modal('PIN admin lokal','lock');$('modal').dataset.recovery='compact';placeRecovery();
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
function mqttSelect(grid,title,choices,current){
 const field=document.createElement('label');field.className='mqtt-settings-field';
 const caption=document.createElement('span');caption.textContent=title;
 const select=document.createElement('select');select.setAttribute('aria-label',title);
 for(const [value,name] of choices){const option=document.createElement('option');option.value=value;option.textContent=name;select.append(option);}
 select.value=current;field.append(caption,select);grid.append(field);return select;
}
function mqttGroupHeading(grid,title,iconName){const heading=document.createElement('h4');heading.className='mqtt-group';heading.append(icon(iconName),title);grid.append(heading);return heading;}
// On-screen keyboard shared by the MQTT dialogs (the kiosk has no physical keyboard). Entries are
// {key,name,input,field}. The edited field is lifted into the keyboard editor and later put back
// into its own parent, so a dialog may use any number of grids. Hidden fields are left out of
// the picker. Closing with Selesai/Escape restores the dialog scroll and keeps the field visible.
function attachMqttKeyboard(box,entries){
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
 let active=null,mode='letters',shifted=false,pointerField=null,suppressKeyboardFocus=false,returnScroll=0;
 const dialog=box.closest('.dialog'),closeButton=$('closemodal'),dialogHead=dialog.querySelector('.dialoghead');
 function restoreField(entry){
  if(entry.field.parentNode===editor&&entry.parent)entry.parent.insertBefore(entry.field,entry.next&&entry.next.parentNode===entry.parent?entry.next:null);
 }
 function revealField(field){
  const view=box.getBoundingClientRect(),rect=field.getBoundingClientRect(),pad=4;
  if(rect.top-pad<view.top)box.scrollTop-=view.top-rect.top+pad;
  else if(rect.bottom+pad>view.bottom)box.scrollTop+=rect.bottom-view.bottom+pad;
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
  if(active)restoreField(active);else returnScroll=box.scrollTop;
  entry.parent=entry.field.parentNode;entry.next=entry.field.nextSibling;editor.append(entry.field);active=entry;mqttKeyboardDismiss=hideKeyboard;bar.append(closeButton);
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
  // refocus=false means the dialog is being closed or replaced; only a user close returns to
  // the scroll position the field was opened from.
  if(refocus){box.scrollTop=returnScroll;revealField(entry.field);}
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
 return {hide:hideKeyboard};
}
async function openMqttSettings(){
 if(needLogin())return;
 const box=modal('Pengaturan MQTT','cloud');text(box,'Memuat pengaturan MQTT...');let settings;
 try{settings=await get('/api/v2/mqtt/settings');}
 catch(e){if(e.message==='HTTP 401'){authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);login();return;}box.replaceChildren();text(box,`Pengaturan tidak termuat: ${e.message}. Tutup lalu coba lagi.`);return;}
 box.replaceChildren();
 if(!settings.editable){text(box,'Pengaturan MQTT tidak tersedia pada mode demo.');return;}
 const grid=document.createElement('div');grid.className='mqtt-settings-fields';
 mqttGroupHeading(grid,'Koneksi broker','cloud');
 const enabled=mqttSelect(grid,'Status MQTT',[['off','Nonaktif'],['on','Aktif']],settings.enabled?'on':'off');
 const transport=mqttSelect(grid,'Transport MQTT',[['tcp','MQTT/TCP'],['websocket','WebSocket (WS)']],settings.transport||'tcp');
 const tls=mqttSelect(grid,'Enkripsi TLS',[['on','Aktif'],['off','Nonaktif']],settings.tls===false?'off':'on');
 const host=mqttField(grid,'IP / host',settings.host,'text','10.90.0.1',253);
 const port=mqttField(grid,'Port',String(settings.port),'number','8883',5);port.min='1';port.max='65535';port.step='1';
 const clientId=mqttField(grid,'Client ID dasar',settings.client_id,'text','rdf-node',48);
 const websocketPath=mqttField(grid,'Path WebSocket',settings.websocket_path||'/mqtt','text','/mqtt',256);
 const websocketPathField=websocketPath.closest('label');
 mqttGroupHeading(grid,'Akun CTRL / BULK','lock');
 const controlUser=mqttField(grid,'User CTRL','','text','tidak diubah',128);controlUser.autocomplete='off';
 const controlPassword=mqttField(grid,'Pass CTRL','','password','tidak diubah',512);controlPassword.autocomplete='new-password';
 const bulkUser=mqttField(grid,'User BULK','','text','tidak diubah',128);bulkUser.autocomplete='off';
 const bulkPassword=mqttField(grid,'Pass BULK','','password','tidak diubah',512);bulkPassword.autocomplete='new-password';
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
 attachMqttKeyboard(box,[
  {key:'host',name:'IP / host',input:host,field:host.closest('label')},
  {key:'port',name:'Port',input:port,field:port.closest('label')},
  {key:'client-id',name:'Client ID dasar',input:clientId,field:clientId.closest('label')},
  {key:'websocket-path',name:'Path WebSocket',input:websocketPath,field:websocketPathField},
  {key:'control-user',name:'User CTRL',input:controlUser,field:controlUser.closest('label')},
  {key:'control-password',name:'Pass CTRL',input:controlPassword,field:controlPassword.closest('label')},
  {key:'bulk-user',name:'User BULK',input:bulkUser,field:bulkUser.closest('label')},
  {key:'bulk-password',name:'Pass BULK',input:bulkPassword,field:bulkPassword.closest('label')}
 ]);
 enabled.focus({preventScroll:true});
}
$('mqttsettings').onclick=openMqttSettings;
// Backup broker (mirror of the primary, LAN or online). It is saved through its own endpoint so
// that changing it never reconnects the PPP/T900 primary. Error codes stay raw, with a short hint.
const mqttBackupErrors={
 INVALID_MQTT_BACKUP_SETTINGS:'pengaturan cadangan ditolak.',
 INVALID_MQTT_HOST:'isi IP / host yang valid tanpa spasi.',
 INVALID_MQTT_PORT:'port harus 1-65535.',
 INVALID_MQTT_TRANSPORT:'pilih MQTT/TCP atau WebSocket.',
 INVALID_MQTT_WEBSOCKET_PATH:'path WebSocket harus diawali /.',
 INCOMPLETE_MQTT_CREDENTIALS:'isi user dan pass, atau kosongkan keduanya.',
 INVALID_MQTT_CREDENTIALS:'user atau pass tidak valid.',
 MQTT_BACKUP_CREDENTIALS_REQUIRED:'belum ada akun tersimpan; isi user dan pass.',
 MQTT_BACKUP_TLS_CA_UNAVAILABLE:'file CA khusus (ca_file) tidak ditemukan.'
};
async function openMqttBackupSettings(){
 if(needLogin())return;
 const box=modal('Broker cadangan','layers');text(box,'Memuat pengaturan broker cadangan...');let settings;
 try{settings=await get('/api/v2/mqtt/backup');}
 catch(e){
  if(e.message==='HTTP 401'){authenticated=false;csrf=null;lastAdminSessionRefresh=0;render(snapshot);login();return;}
  box.replaceChildren();
  text(box,e.message==='HTTP 404'?'Broker cadangan belum didukung oleh layanan Edge ini.':`Pengaturan tidak termuat: ${e.message}. Tutup lalu coba lagi.`);return;
 }
 if($('modal').hidden)return;
 box.replaceChildren();
 if(!settings||typeof settings!=='object'){text(box,'Pengaturan broker cadangan tidak valid. Tutup lalu coba lagi.');return;}
 if(!settings.editable){text(box,'Broker cadangan tidak tersedia pada mode demo.');return;}
 const grid=document.createElement('div');grid.className='mqtt-settings-fields';
 mqttGroupHeading(grid,'Koneksi cadangan','cloud');
 const enabled=mqttSelect(grid,'Status cadangan',[['off','Nonaktif'],['on','Aktif']],'off');
 const transport=mqttSelect(grid,'Transport MQTT',[['tcp','MQTT/TCP'],['websocket','WebSocket (WS)']],'tcp');
 const tls=mqttSelect(grid,'Enkripsi TLS',[['on','Aktif'],['off','Nonaktif']],'on');
 const host=mqttField(grid,'IP / host','','text','192.168.1.10',253);
 const port=mqttField(grid,'Port','','number','8883',5);port.min='1';port.max='65535';port.step='1';
 const websocketPath=mqttField(grid,'Path WebSocket','/mqtt','text','/mqtt',256);
 const websocketPathField=websocketPath.closest('label');
 const tlsHint=text(grid,'');const clientHint=text(grid,'');tlsHint.className=clientHint.className='hint';
 mqttGroupHeading(grid,'Perintah dan akun','lock');
 const commands=mqttSelect(grid,'Terima perintah',[['on','Aktif'],['off','Nonaktif']],'on');
 const settingsPlain=mqttSelect(grid,'Settings tanpa TLS',[['off','Tolak'],['on','Izinkan']],'off');
 const settingsPlainField=settingsPlain.closest('label');
 const auth=mqttSelect(grid,'Akun',[['none','Tanpa akun'],['account','Pakai akun']],'none');
 const user=mqttField(grid,'User','','text','',128);user.autocomplete='off';
 const password=mqttField(grid,'Pass','','password','',512);password.autocomplete='new-password';
 const commandHint=text(grid,'');commandHint.className='hint';
 const settingsHint=text(grid,'');settingsHint.className='hint';
 box.append(grid);
 function updateForm(){
  const tlsOn=tls.value==='on',trust=settings.trust_mode;
  websocketPathField.hidden=transport.value!=='websocket';
  user.closest('label').hidden=password.closest('label').hidden=auth.value!=='account';
  port.placeholder=tlsOn?'8883':'1883';
  tlsHint.textContent=!tlsOn?'TLS nonaktif: akun dan payload tidak terenkripsi; hanya untuk jaringan tepercaya.'
   :trust==='custom'?'TLS: sertifikat broker diverifikasi dengan CA khusus (ca_file).'
   :trust==='unavailable'?'TLS: file CA khusus (ca_file) tidak ditemukan; koneksi cadangan akan gagal.'
   :trust==='system'?'TLS: sertifikat diverifikasi dengan CA sistem. Broker LAN self-signed perlu ca_file di config.yaml atau TLS nonaktif.'
   :'TLS: sertifikat diverifikasi dengan CA sistem, atau ca_file bila diatur di config.yaml.';
  tlsHint.classList.toggle('warn',!tlsOn||trust==='unavailable');
  commandHint.textContent=commands.value==='on'
   ?'Edge subscribe cmd/* di broker ini; perintahnya diproses seperti dari Ground. Batasi lewat ACL broker.'
   :'Hanya publish; Edge tidak subscribe perintah di broker ini.';
  // Plaintext settings reports are only an option on a backup without TLS; TLS backups always answer config.get.
  settingsPlainField.hidden=tlsOn||commands.value!=='on';
  settingsHint.hidden=settingsPlainField.hidden;
  settingsHint.textContent=settingsPlain.value==='on'
   ?'cmd/config/get dijawab settings/reported tanpa enkripsi di broker ini. Izinkan hanya untuk broker tepercaya, bukan broker bersama/publik.'
   :'cmd/config/get di broker ini ditolak (TLS_REQUIRED_FOR_SETTINGS_REPORT) selama TLS nonaktif.';
  settingsHint.classList.toggle('warn',settingsPlain.value==='on');
 }
 function applySettings(s){
  enabled.value=s.enabled===true?'on':'off';transport.value=s.transport==='websocket'?'websocket':'tcp';tls.value=s.tls===false?'off':'on';
  host.value=s.host??'';port.value=s.port==null?'':String(s.port);websocketPath.value=s.websocket_path||'/mqtt';
  commands.value=s.commands===false?'off':'on';settingsPlain.value=s.settings_report_plaintext===true?'on':'off';auth.value=s.auth==='account'||s.auth==='config'?'account':'none';
  user.value='';password.value='';
  user.placeholder=password.placeholder=s.credentials_set===true?(s.credentials_source==='config'?'dari config':'tersimpan'):'wajib diisi';
  clientHint.textContent=s.client_id?`Client ID: ${s.client_id} (otomatis, berbeda dari broker utama).`:'Client ID dibuat otomatis, berbeda dari broker utama.';
  updateForm();
 }
 for(const select of [transport,tls,commands,settingsPlain,auth])select.addEventListener('change',updateForm);
 applySettings(settings);
 const saveButton=action(box,'Simpan',async()=>{
  const account=auth.value==='account';let saved;
  try{
   saved=await post('/api/v2/mqtt/backup',{
    enabled:enabled.value==='on',host:host.value.trim(),port:Number(port.value),transport:transport.value,
    websocket_path:websocketPath.value,tls:tls.value==='on',commands:commands.value==='on',
    settings_report_plaintext:tls.value!=='on'&&settingsPlain.value==='on',
    auth:auth.value,username:account?user.value:'',password:account?password.value:''
   });
  }catch(e){const hint=mqttBackupErrors[e.message];if(hint)e.message=`${e.message}: ${hint}`;throw e;}
  const valid=saved&&typeof saved==='object';
  if(valid){settings=saved;applySettings(saved);}else{user.value='';password.value='';}
  $('modalmsg').textContent=(valid?saved.enabled===true:enabled.value==='on')?'Tersimpan; koneksi cadangan diperbarui tanpa memutus broker utama.':'Tersimpan; broker cadangan nonaktif.';
  renderData(snapshot);
 },false,'check');
 saveButton.classList.add('primary');
 attachMqttKeyboard(box,[
  {key:'host',name:'IP / host',input:host,field:host.closest('label')},
  {key:'port',name:'Port',input:port,field:port.closest('label')},
  {key:'websocket-path',name:'Path WebSocket',input:websocketPath,field:websocketPathField},
  {key:'user',name:'User',input:user,field:user.closest('label')},
  {key:'password',name:'Pass',input:password,field:password.closest('label')}
 ]);
 enabled.focus({preventScroll:true});
}
$('mqttbackupsettings').onclick=openMqttBackupSettings;
async function command(op,extras={}){
 const r={v:2,id:`local-${Date.now()}-${crypto.randomUUID().slice(0,8)}`,sid:snapshot.sid,boot:snapshot.boot_id,issued_ms:Date.now(),expires_ms:Date.now()+15000,base_rev:snapshot.config?.sdr_revision??null,op,...extras};
 try{const result=await post('/api/v2/commands',r);if(op==='system.shutdown.execute')result.requestId=r.id;$('modalmsg').textContent=`${result.stage}: ${result.id||''}`;return result;}
 catch(error){if(op!=='system.shutdown.execute')throw error;const failure=new Error(error.message);failure.operationId=r.id;failure.status=error.status;throw failure;}
}
const profileChoices=[
 ['control','CONTROL','pause','Tanpa grafik 360°'],
 ['balanced','BALANCED','activity','Grafik 360° ≥ 4 dtk'],
 ['graph_u8','GRAPH','wave','Grafik 360° ≥ 2 dtk']
];
$('profilebtn').onclick=()=>{
 if(needLogin())return;
 const box=modal('Profil telemetry','activity'),current=snapshot.config?.profile;
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
 ['blank_after_seconds','Layar hitam otomatis','screenoff',[['0','Mati'],['60','1 mnt'],['300','5 mnt'],['900','15 mnt']]]
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
     $('modalmsg').textContent=`${label}: ${title} tersimpan.`;
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
  // Local T900/PPP restart: controlled mode + control.ppp_restart_enabled + helper approval.
  const ppp=action(host,'Restart T900',()=>confirmPppRestart(),false,'radio');
  ppp.disabled=!caps.ppp_restart;
  const rebootReason=!caps.helper_available?'Helper kontrol belum tersedia.':
   !caps.reboot?'Reboot lokal memerlukan mode controlled dan approval root (--reboot).':'';
  const pppReason=caps.ppp_restart?'':!caps.helper_available?'Helper kontrol belum tersedia.':'Perlu approval root: sudo rdf-node controls approve --ppp-restart';
  ppp.title=pppReason;
  for(const reason of new Set([rebootReason,pppReason].filter(Boolean))){const note=text(host,reason);note.className='hint';}
};
function confirmOperation(label,op,extra,message='Pemrosesan RDF akan terhenti.'){const box=modal(label,'warn','bad');text(box,message);cancelAction(box);action(box,'Konfirmasi',()=>command(op,extra).catch(error=>{throw withReasonHint(error);}),true,'check');}
// Live while the confirmation is open: replugging the radio clears the warning.
function setUsbWarning(el,s){
 const hint=usbHint(s);el.hidden=!hint;
 if(el.dataset.text!==hint){el.dataset.text=hint;el.replaceChildren(...(hint?[icon('warn'),hint]:[]));}
}
// PPP restart cuts only the primary (T900) MQTT path; progress then shows in this dialog.
function confirmPppRestart(){
 const box=modal('Restart T900','warn','bad');
 text(box,'Restart PPP T900? MQTT utama putus beberapa detik sampai PPP tersambung lagi. Broker cadangan (LAN/internet) tidak terpengaruh.');
 const warning=document.createElement('p');warning.id='pppusbwarn';warning.className='usb-warning';warning.setAttribute('role','alert');
 box.append(warning);setUsbWarning(warning,snapshot);
 cancelAction(box);
 action(box,'Konfirmasi',async()=>{
  let result;try{result=await command('ppp.restart');}catch(error){throw withReasonHint(error);}
  if(warning.isConnected&&$('modal').dataset.recovery==='compact'){$('modal').dataset.recovery='full';placeRecovery();}
  return result;
 },true,'check');
 // An earlier card shows only its headline here so the warning and buttons stay in view; once
 // this restart is accepted the dialog shows its full progress.
 if(currentRecovery(snapshot)){$('modal').dataset.recovery='compact';placeRecovery();}
}
// The kiosk has no physical keyboard, so frequency entry uses an on-screen keypad. Range and
// policy checks stay on the Edge; this only builds a well-formed MHz number.
function frequency(){
 const box=modal('Frekuensi center + VFO0','wave');$('modal').dataset.recovery='compact';placeRecovery();
 const start=snapshot.detection?.frequency_hz?(snapshot.detection.frequency_hz/1e6).toFixed(3):'';
 let value=start,fresh=true;
 const layout=document.createElement('div');layout.className='freq-layout';
 const side=document.createElement('div');side.className='freq-side';
 const readout=document.createElement('div');readout.className='freq-readout';readout.setAttribute('role','status');readout.setAttribute('aria-live','polite');
 const number=document.createElement('strong'),unit=document.createElement('span');unit.textContent='MHz';readout.append(number,unit);
 side.append(readout);
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
async function prepareReboot(){const box=modal('Reboot Raspberry','sync','bad');text(box,'Node offline selama boot.');cancelAction(box);action(box,'Siapkan reboot',async()=>{const r=await command('system.reboot.prepare');$('modalmsg').textContent='Menunggu challenge...';let op=null;for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,300));const list=await get('/api/v2/operations/latest');op=list.find(x=>x.id===r.id);if(op&&['FAILED','APPLIED','REJECTED'].includes(op.stage))break;}
 // Challenges are deliberately not returned by public history. A local reboot is
 // issued through authenticated command result access (private endpoint below).
 const result=await post('/api/v2/operation/result',{id:r.id});
 if(result.stage!=='APPLIED'||!result.result?.challenge)throw new Error(result.result?.error||'Challenge belum tersedia');
 const cbox=modal('Konfirmasi reboot OS','warn','bad');text(cbox,'Berlaku 30 detik.');cancelAction(cbox);action(cbox,'REBOOT SEKARANG',()=>command('system.reboot.execute',{prepare_id:r.id,challenge:result.result.challenge}),true,'sync');
 },true,'sync');}
async function prepareShutdown(){
 const box=modal('Shutdown Raspberry','power','bad');
 text(box,'Pi mati total; nyalakan ulang secara lokal.');
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
  text(cbox,'Pi mati 5 detik setelah dijadwalkan.');
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
if($('historybtn'))$('historybtn').onclick=openHistory;
get('/api/v2/session').then(s=>{if(authenticated)return;authenticated=s.authenticated;csrf=s.csrf;lastAdminSessionRefresh=Date.now();render(snapshot);}).catch(()=>{});poll();
