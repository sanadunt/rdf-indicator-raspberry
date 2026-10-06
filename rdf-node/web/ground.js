'use strict';
const $=id=>document.getElementById(id);let csrf=null,snapshot={},pollPending=false,pollAgain=false,pollTimer=null,apiSnapshotAvailable=false,shutdownPending=false,shutdownUncertain=false,shutdownHistoryChecked=false,shutdownHistoryAt=0,shutdownHistoryRequest=null,pppPending=false,pppRecords=[],pppHistoryAt=0,pppHistoryRequest=null,pppHistoryAvailable=false,pppRenderKey='',pppServerGate={required:false,freshAfter:0,message:''};
async function get(p){const r=await fetch(p,{cache:'no-store',signal:AbortSignal.timeout(2000)});if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json();}
function apiStatus(message){$('api-status').hidden=!message;$('api-message').textContent=message;}
async function post(p,j,timeout=6000){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf||''},body:JSON.stringify(j),signal:AbortSignal.timeout(timeout)});const out=await r.json();if(!r.ok){const e=Error(out.error||'Gagal');e.status=r.status;throw e;}return out;}
const shutdownFailureStages=new Set(['FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED']);
function readShutdownUncertainty(){try{return localStorage.getItem('rdf-node-ground-shutdown-uncertain')==='1';}catch{return false;}}
shutdownUncertain=readShutdownUncertainty();
function setShutdownUncertainty(value){shutdownUncertain=value;try{if(value)localStorage.setItem('rdf-node-ground-shutdown-uncertain','1');else localStorage.removeItem('rdf-node-ground-shutdown-uncertain');}catch{}}
function rememberShutdownState(op){if(op?.op==='system.shutdown.execute'&&!shutdownFailureStages.has(op.stage))setShutdownUncertainty(true);}
function updateShutdownButton(){$('shutdown').disabled=shutdownPending||!apiSnapshotAvailable||!shutdownHistoryChecked||!(snapshot.capabilities?.shutdown&&snapshot.capabilities?.remote_commands);}
const pppUnresolvedStages=new Set(['SUBMITTING','REQUESTED','ACCEPTED','APPLYING','PPP_RESTART_REQUESTED','OUTCOME_UNKNOWN']);
const pppIdPattern=/^[A-Za-z0-9._:-]{1,96}$/;
function readPppRecords(){
 try{
  const records=JSON.parse(localStorage.getItem('rdf-node-ground-ppp-restarts')||'[]');
  return Array.isArray(records)?records.filter(r=>r&&typeof r==='object'&&typeof r.id==='string'&&pppIdPattern.test(r.id)&&typeof r.stage==='string').map(r=>({...r,result:r.result&&typeof r.result==='object'?r.result:{}})):[];
 }catch{return[];}
}
function savePppRecords(){
 const latest=pppRecords.at(-1);
 pppRecords=pppRecords.filter(record=>pppIsUnresolved(record)||record.stage==='OUTCOME_UNKNOWN'||record===latest);
 const persistent=pppRecords.filter(record=>pppIsUnresolved(record)||record.stage==='OUTCOME_UNKNOWN');
 try{localStorage.setItem('rdf-node-ground-ppp-restarts',JSON.stringify(persistent));}catch{}
}
function readPppServerGate(){
 try{
  const gate=JSON.parse(localStorage.getItem('rdf-node-ground-ppp-gate')||'{}');
  return {required:gate.required===true,freshAfter:Number.isSafeInteger(gate.freshAfter)?gate.freshAfter:0,message:typeof gate.message==='string'?gate.message:''};
 }catch{return {required:false,freshAfter:0,message:''};}
}
function savePppServerGate(){try{localStorage.setItem('rdf-node-ground-ppp-gate',JSON.stringify(pppServerGate));}catch{}}
pppRecords=readPppRecords();
pppServerGate=readPppServerGate();
savePppRecords();
function pppIsUnresolved(record){return pppUnresolvedStages.has(record.stage)&&!record.confirmedBy&&!record.result?.confirmed_by;}
function rememberPppOperation(operation){
 if(operation?.op!=='ppp.restart'||typeof operation.id!=='string'||!pppIdPattern.test(operation.id))return;
 let record=pppRecords.find(item=>item.id===operation.id),changed=false;
 if(!record){record={id:operation.id,stage:operation.stage||'REQUESTED',created_ms:Date.now(),result:{}};pppRecords.push(record);changed=true;}
 if(typeof operation.stage==='string'&&record.stage!==operation.stage){record.stage=operation.stage;changed=true;}
 if(operation.result&&typeof operation.result==='object'&&JSON.stringify(record.result)!==JSON.stringify(operation.result)){record.result=operation.result;changed=true;}
 if(Number.isSafeInteger(record.result?.requested_ms)&&record.requested_ms!==record.result.requested_ms){record.requested_ms=record.result.requested_ms;changed=true;}
 if(record.result?.confirmed_by&&record.confirmedBy!==record.result.confirmed_by){record.confirmedBy=record.result.confirmed_by;changed=true;}
 if(Number.isSafeInteger(operation.updated_ms)&&operation.updated_ms>(record.updated_ms||0)){
  record.updated_ms=operation.updated_ms;
  pppRecords=pppRecords.filter(item=>item!==record);
  pppRecords.push(record);
  changed=true;
 }
 if(changed){savePppRecords();renderPppRecords();}
}
function mergePppResult(outcome){
 if(!outcome||typeof outcome.id!=='string'||!pppIdPattern.test(outcome.id))return;
 let record=pppRecords.find(item=>item.id===outcome.id),changed=false;
 if(!record){record={id:outcome.id,created_ms:Date.now(),result:{}};pppRecords.push(record);changed=true;}
 if(typeof outcome.stage==='string'&&record.stage!==outcome.stage){record.stage=outcome.stage;changed=true;}
 if(outcome.result&&typeof outcome.result==='object'&&JSON.stringify(record.result)!==JSON.stringify(outcome.result)){record.result=outcome.result;changed=true;}
 if(Number.isSafeInteger(record.result?.requested_ms)&&record.requested_ms!==record.result.requested_ms){record.requested_ms=record.result.requested_ms;changed=true;}
 if(record.result?.confirmed_by&&record.confirmedBy!==record.result.confirmed_by){record.confirmedBy=record.result.confirmed_by;changed=true;}
 if(changed)savePppRecords();
}
function pppRecordMessage(record){
 const messages={
  SUBMITTING:'Request Ground sedang dikirim. Browser tidak akan mengirim ulang otomatis.',
  REQUESTED:'Ground mengantrekan request. Edge belum melaporkan hasil systemd.',
  ACCEPTED:'Edge menerima command. Hasil systemd belum diterima.',
  APPLYING:'Edge sedang menjalankan preflight restart PPP.',
  PPP_RESTART_REQUESTED:'systemd menerima permintaan restart. Menunggu health node yang lebih baru; ini bukan bukti PPP/MQTT pulih.',
  APPLIED:'systemd menerima permintaan dan Ground menerima health node fresh yang lebih baru dalam sesi sama. Pemulihan unit PPP atau link belum diverifikasi terpisah.',
  OUTCOME_UNKNOWN:'Hasil belum pasti. Jangan kirim ulang. Tunggu health fresh lalu gunakan konfirmasi tambahan untuk ID baru.',
  FAILED:'Node melaporkan kegagalan. Periksa alasan sebelum tindakan berikutnya.',
  REJECTED:'Ground atau node menolak request. Periksa alasan.',
  EXPIRED:'Request kedaluwarsa sebelum dijalankan.',
  CONFLICT:'Request ditolak karena state node berubah.',
  CANCELLED:'Request dibatalkan.'
 };
 let message=messages[record.stage]||`Status ${record.stage}.`;
 if(record.result?.error)message+=` Alasan: ${String(record.result.error)}`;
 if(record.confirmedBy||record.result?.confirmed_by)message+=' Request baru dikirim setelah operator meninjau hasil lama; hasil lama tetap tidak diketahui.';
 return message;
}
function renderPppRecords(){
 const list=$('ppp-results');
 if(!list)return;
 const visible=pppRecords.filter((record,index)=>pppIsUnresolved(record)||record.stage==='OUTCOME_UNKNOWN'||index===pppRecords.length-1);
 const key=JSON.stringify({records:visible.map(record=>[record.id,record.stage,record.confirmedBy||record.result?.confirmed_by||'',record.result?.error||'']),gate:pppServerGate.required?pppServerGate.message:''});
 if(key===pppRenderKey)return;
 pppRenderKey=key;
 list.replaceChildren();
 for(const record of visible){
  const item=document.createElement('li'),title=document.createElement('strong'),message=document.createElement('span');
  title.textContent=`${record.stage} | ${record.id}`;
  message.textContent=pppRecordMessage(record);
  item.append(title,document.createElement('br'),message);
  list.append(item);
 }
 if(pppServerGate.required){
  const item=document.createElement('li');
  item.textContent=pppServerGate.message||'Ground melaporkan restart PPP sebelumnya yang belum terselesaikan.';
  list.append(item);
 }
}
function pppReadiness(){
 const unresolved=pppRecords.filter(pppIsUnresolved),healthMs=snapshot.health?.t;
 const freshAfter=unresolved.every(record=>{
  const required=Number.isSafeInteger(record.requested_ms)?record.requested_ms:Number.isSafeInteger(record.created_ms)?record.created_ms:Infinity;
  return Number.isSafeInteger(healthMs)&&healthMs>required;
 });
 const serverFreshAfter=!pppServerGate.required||(Number.isSafeInteger(healthMs)&&healthMs>pppServerGate.freshAfter);
 const authorized=snapshot.capabilities?.remote_commands===true&&snapshot.capabilities?.ppp_restart===true;
 const fresh=snapshot.health_fresh===true&&snapshot.link?.mqtt_control?.ready===true;
 const ready=!!csrf&&apiSnapshotAvailable&&pppHistoryAvailable&&authorized&&fresh&&freshAfter&&serverFreshAfter;
 let reason='Restart siap. Tindakan baru setelah hasil lama belum pasti memerlukan konfirmasi tambahan.';
 if(!csrf)reason='Login Ground diperlukan untuk meminta restart.';
 else if(!apiSnapshotAvailable)reason='Snapshot Ground tidak tersedia; request tidak dikirim.';
 else if(!pppHistoryAvailable)reason='Riwayat operasi Ground belum dapat diperiksa; request tidak dikirim.';
 else if(!authorized)reason='Izin remote control dan PPP restart belum aktif di node.';
 else if(!fresh)reason='Restart perlu health fresh dan MQTT Control tersambung.';
 else if(unresolved.length&&!freshAfter)reason='Hasil PPP sebelumnya belum terkonfirmasi. Tunggu health node yang lebih baru sebelum konfirmasi tambahan.';
 else if(pppServerGate.required&&!serverFreshAfter)reason='Ground meminta health fresh yang lebih baru sebelum konfirmasi tambahan.';
 return {ready,reason};
}
function pppOperationReady(){return pppReadiness().ready;}
function updatePppButton(){
 const button=$('ppp-restart');
 if(!button)return;
 const readiness=pppReadiness();
 button.disabled=pppPending||!readiness.ready;
 const reason=pppPending?'Mengirim request PPP. Jangan klik ulang.':readiness.reason;
 const status=$('ppp-availability');
 if(status.textContent!==reason)status.textContent=reason;
}
async function refreshPppHistory(force=false){
 if(!csrf)return;
 if(pppHistoryRequest)return pppHistoryRequest;
 if(!force&&Date.now()-pppHistoryAt<3000)return;
 pppHistoryAt=Date.now();
 pppHistoryRequest=(async()=>{
  try{
   const operations=await get('/api/v2/operations/latest');
   if(!Array.isArray(operations))throw Error('Invalid operation history');
   pppHistoryAvailable=true;
   const pppOperations=operations.filter(operation=>operation?.op==='ppp.restart');
   const latestPpp=pppOperations.reduce((latest,operation)=>!latest||operation.updated_ms>latest.updated_ms?operation:latest,null);
   for(const operation of pppOperations)if(pppUnresolvedStages.has(operation.stage))rememberPppOperation(operation);
   if(latestPpp)rememberPppOperation(latestPpp);
   const unresolved=pppRecords.filter(pppIsUnresolved);
   const results=await Promise.all(unresolved.map(record=>post('/api/v2/operation/result',{id:record.id},1500).catch(()=>null)));
   for(const result of results)if(result)mergePppResult(result);
  }catch{pppHistoryAvailable=false;}
  renderPppRecords();
  updatePppButton();
 })();
 try{await pppHistoryRequest;}finally{pppHistoryRequest=null;}
}
async function requestPppRestart(){
 const button=$('ppp-restart');
 if(pppPending||button.disabled)return;
 pppPending=true;
 updatePppButton();
 try{
  await refreshPppHistory(true);
  if(!pppOperationReady())return;
  const previous=pppRecords.filter(pppIsUnresolved),confirmPrevious=previous.length>0||pppServerGate.required;
  if(!confirm('Restart T900 PPP sekarang? PPP/MQTT dapat terputus. Tidak ada retry otomatis.'))return;
  if(confirmPrevious){
   const detail=previous.length?`ID: ${previous.map(record=>record.id).join(', ')}.`:'ID dan hasil lama tidak tersedia di riwayat browser.';
   if(!confirm(`Ground mencatat restart PPP sebelumnya yang belum terkonfirmasi. ${detail} Kirim request baru dengan ID berbeda? Hasil lama tidak akan diulang atau dihapus.`))return;
  }
  const id=`ground-${crypto.randomUUID().replace(/-/g,'').slice(0,20)}`,record={id,stage:'SUBMITTING',created_ms:Date.now(),result:{}};
  pppRecords.push(record);
  savePppRecords();
  renderPppRecords();
  const command={id,op:'ppp.restart',base_rev:snapshot.config?.sdr_revision??null};
  if(confirmPrevious)command.confirm_previous_unknown=true;
  try{
   const result=await post('/api/v2/commands',command);
   if(result.id!==id)throw Error('ID operasi PPP tidak cocok');
   record.stage=result.stage||'REQUESTED';
   if(Number.isSafeInteger(result.request?.issued_ms))record.requested_ms=result.request.issued_ms;
   record.result={};
   if(record.stage==='REQUESTED'){
    for(const old of previous)old.confirmedBy=id;
    pppServerGate={required:false,freshAfter:0,message:''};
    savePppServerGate();
   }
   savePppRecords();
   renderPppRecords();
   void refreshPppHistory(true);
  }catch(error){
   record.stage=error.status>=400&&error.status<500?'REJECTED':'OUTCOME_UNKNOWN';
   record.result={error:error.message};
   if(['PPP_RESTART_CONFIRMATION_REQUIRED','PPP_RESTART_HEALTH_NOT_NEWER'].includes(error.message)){
    pppServerGate={required:true,freshAfter:Date.now(),message:error.message==='PPP_RESTART_CONFIRMATION_REQUIRED'?'Ground melaporkan restart PPP sebelumnya yang belum terselesaikan, tetapi ID/hasilnya tidak ada di browser history.':'Ground memerlukan health node yang lebih baru dari request restart PPP sebelumnya.'};
    savePppServerGate();
   }
   savePppRecords();
   renderPppRecords();
  }
 }finally{
  pppPending=false;
  updatePppButton();
 }
}
renderPppRecords();
updatePppButton();
function diagnosticTimestamp(ms){return Number.isSafeInteger(ms)?`${new Date(ms).toISOString()} (${ms} ms)`:'--';}
function renderDiagnostic(d){
 if(!d?.available){$('diagnostic-state').textContent='Belum ada data diagnostik dari node.';$('diagnostic-angle').textContent='DoA raw: --';$('diagnostic-frequency').textContent='Frekuensi: --';$('diagnostic-source-time').textContent='TIME sumber: --';$('diagnostic-observed-time').textContent='Observasi Edge: --';$('diagnostic-age').textContent='Usia: --';$('diagnostic-reasons').textContent='Gate: --';return;}
 $('diagnostic-state').textContent=d.stale?`UNVERIFIED / STALE RECEIPT (${d.received_age_ms??'--'} ms)`:'UNVERIFIED / RECEIVED';
 $('diagnostic-angle').textContent=`DoA raw: ${Number.isFinite(d.raw_doa_deg)?d.raw_doa_deg.toFixed(1):'--'}°`;
 $('diagnostic-frequency').textContent=`Frekuensi: ${Number.isFinite(d.frequency_mhz)?d.frequency_mhz.toFixed(3):'--'} MHz`;
 $('diagnostic-source-time').textContent=`TIME sumber: ${diagnosticTimestamp(d.source_timestamp_ms)}`;
 $('diagnostic-observed-time').textContent=`Observasi Edge: ${diagnosticTimestamp(d.observed_timestamp_ms)}`;
 $('diagnostic-age').textContent=`Usia pesan Ground ${d.received_age_ms??'--'} ms`;
 $('diagnostic-reasons').textContent=`Alasan validasi: ${Array.isArray(d.validation_reasons)?d.validation_reasons.join(', '):'--'}`;
}
async function checkShutdownHistory(force=false,clearIfEmpty=false){
 if(!force&&shutdownHistoryChecked&&Date.now()-shutdownHistoryAt<5000)return shutdownUncertain;
 if(shutdownHistoryRequest)return shutdownHistoryRequest;
 shutdownHistoryRequest=(async()=>{
  const status=await get('/api/v2/operations/pending-shutdowns');
  if(typeof status?.pending!=='boolean')throw Error('Invalid shutdown status');
  shutdownHistoryAt=Date.now();shutdownHistoryChecked=true;
  if(status.pending)setShutdownUncertainty(true);
  else if(clearIfEmpty)setShutdownUncertainty(false);
  updateShutdownButton();return status.pending;
 })();
 try{return await shutdownHistoryRequest;}finally{shutdownHistoryRequest=null;}
}
async function shutdownOutcome(id){for(let i=0;i<12;i++){try{const outcome=await post('/api/v2/operation/result',{id},1500);if(['SHUTDOWN_SCHEDULED','FAILED','REJECTED','OUTCOME_UNKNOWN'].includes(outcome.stage))return outcome;}catch{return null;}await new Promise(r=>setTimeout(r,250));}return null;}
async function command(op,extra={}){try{$('operation').textContent=JSON.stringify(await post('/api/v2/commands',{op,base_rev:snapshot.config?.sdr_revision??null,...extra}),null,2);}catch(e){$('operation').textContent=e.message;}}
const pinInput=$('pin');const loginButton=$('login');loginButton.disabled=true;
pinInput.addEventListener('input',()=>{pinInput.value=pinInput.value.replace(/[^0-9]/g,'').slice(0,6);loginButton.disabled=pinInput.value.length!==6;});
loginButton.onclick=async()=>{try{const r=await post('/api/v2/login',{pin:pinInput.value});csrf=r.csrf;pinInput.value='';loginButton.disabled=true;$('authstate').textContent='ADMIN - operasi tetap memerlukan izin node.';updatePppButton();void refreshPppHistory(true);}catch(e){pinInput.value='';loginButton.disabled=true;$('authstate').textContent=e.message;}};
$('refresh').onclick=()=>command('config.get');$('start').onclick=()=>command('processing.set',{desired:'RUNNING'});$('stop').onclick=()=>{if(confirm('Hentikan stack RDF? Telemetry tetap hidup.'))command('processing.set',{desired:'STOPPED'});};
$('restart').onclick=()=>{if(confirm('Restart stack RDF yang sudah di-approve?'))command('service.restart');};
 $('reboot').onclick=async()=>{
  if(!confirm('Prepare reboot Raspberry? Memerlukan approval dan lease maintenance lokal.'))return;
  try{
   const prepared=await post('/api/v2/commands',{op:'system.reboot.prepare',base_rev:snapshot.config?.sdr_revision??null});
   if(!prepared.id)throw Error(prepared.error||'Prepare ditolak');
   let result=null;
   for(let i=0;i<30;i++){
    await new Promise(r=>setTimeout(r,500));
    result=await post('/api/v2/operation/result',{id:prepared.id});
    if(['APPLIED','FAILED','REJECTED','OUTCOME_UNKNOWN'].includes(result.stage))break;
   }
   if(result?.stage!=='APPLIED'||!result.result?.challenge)throw Error(result?.result?.error||'Challenge tidak tersedia');
   if(confirm('REBOOT Raspberry SEKARANG? Node akan offline selama boot.')){
    $('operation').textContent=JSON.stringify(await post('/api/v2/commands',{op:'system.reboot.execute',base_rev:snapshot.config?.sdr_revision??null,prepare_id:prepared.id,challenge:result.result.challenge}),null,2);
   }
  }catch(e){$('operation').textContent=e.message;}
 };
 $('shutdown').onclick=async()=>{
  if(shutdownPending||$('shutdown').disabled)return;
  if(readShutdownUncertainty())setShutdownUncertainty(true);
  try{await checkShutdownHistory(true);}catch(e){$('operation').textContent='Status shutdown tidak tersedia; permintaan tidak dikirim.';return;}
  if(shutdownUncertain&&!confirm('Hasil shutdown sebelumnya belum pasti. Periksa status Raspberry secara lokal. Jika perlu melepas intent yang tidak aktif, jalankan sudo rdf-node controls shutdown-reconcile pada Pi; helper menolak bila unit masih aktif. Lanjutkan?'))return;
  setShutdownUncertainty(false);
  if(!confirm('Prepare shutdown Raspberry? Memerlukan approval dan lease maintenance lokal.'))return;
  shutdownPending=true;updateShutdownButton();
  let executeAttempted=false,executeAccepted=false,executeId=null;
  try{
   const prepared=await post('/api/v2/commands',{op:'system.shutdown.prepare',base_rev:snapshot.config?.sdr_revision??null});
   if(!prepared.id)throw Error(prepared.error||'Prepare ditolak');
   let result=null;
   for(let i=0;i<30;i++){
    await new Promise(r=>setTimeout(r,500));
    result=await post('/api/v2/operation/result',{id:prepared.id});
    if(['APPLIED','FAILED','REJECTED','OUTCOME_UNKNOWN'].includes(result.stage))break;
   }
   if(result?.stage!=='APPLIED'||!result.result?.challenge)throw Error(result?.result?.error||'Challenge shutdown belum tersedia');
   await checkShutdownHistory(true);
   if(shutdownUncertain&&!confirm('Hasil shutdown sebelumnya belum pasti. Periksa status Raspberry secara lokal dan rekonsiliasi intent bila unit systemd sudah tidak aktif. Lanjutkan?'))return;
   if(!confirm('MATIKAN Raspberry SEKARANG? Sistem akan mati; untuk menyalakan lagi perlu akses lokal.'))return;
   executeId='ground-'+crypto.randomUUID().replace(/-/g,'').slice(0,20);
   setShutdownUncertainty(true);executeAttempted=true;
   const sent=await post('/api/v2/commands',{id:executeId,op:'system.shutdown.execute',base_rev:snapshot.config?.sdr_revision??null,prepare_id:prepared.id,challenge:result.result.challenge});
   executeAccepted=true;
   if(sent.id!==executeId)throw Error('ID operasi shutdown tidak cocok');
   const outcome=await shutdownOutcome(executeId);
   if(outcome&&shutdownFailureStages.has(outcome.stage)){try{await checkShutdownHistory(true,true);}catch{setShutdownUncertainty(true);}}
   else setShutdownUncertainty(true);
   $('operation').textContent=JSON.stringify(outcome||{stage:'OUTCOME_UNKNOWN',error:'Hasil shutdown belum diterima.'},null,2)+
    (shutdownUncertain?'\nJangan ulangi tanpa pemeriksaan lokal. Jika intent tidak aktif, jalankan sudo rdf-node controls shutdown-reconcile pada Pi.':'');
  }catch(e){
   const maybeAccepted=executeAccepted||(executeAttempted&&!(e.status>=400&&e.status<500));
   if(maybeAccepted){
    const outcome=executeId?await shutdownOutcome(executeId):null;
    if(outcome&&shutdownFailureStages.has(outcome.stage)){try{await checkShutdownHistory(true,true);}catch{setShutdownUncertainty(true);}}
    else setShutdownUncertainty(true);
    $('operation').textContent=JSON.stringify(outcome||{stage:'OUTCOME_UNKNOWN',error:e.message},null,2)+
     (shutdownUncertain?'\nJangan ulangi tanpa pemeriksaan lokal. Jika intent tidak aktif, jalankan sudo rdf-node controls shutdown-reconcile pada Pi.':'');
   }else if(executeAttempted){
    try{await checkShutdownHistory(true,true);}catch{setShutdownUncertainty(true);}
    $('operation').textContent=JSON.stringify({stage:'REJECTED',error:e.message},null,2);
   }else $('operation').textContent=e.message;
  }finally{shutdownPending=false;updateShutdownButton();}
 };
 $('tune').onclick=()=>{const f=Math.round(Number($('frequency').value)*1e6);if(f>0&&Number.isFinite(f))command('config.patch',{changes:{center_frequency_hz:f,vfo0_frequency_hz:f}});};
 $('setprofile').onclick=()=>command('stream.set',{profile:$('profile').value});
 $('ppp-restart').onclick=()=>{void requestPppRestart();};
function plot(a){const c=$('plot').getContext('2d');c.clearRect(0,0,480,480);const cx=240,cy=240,r=180;c.strokeStyle='#31455e';c.lineWidth=1;c.fillStyle='#a6b7cf';c.font='14px system-ui';for(const x of [60,120,180]){c.beginPath();c.arc(cx,cy,x,0,Math.PI*2);c.stroke();}c.beginPath();c.moveTo(50,cy);c.lineTo(430,cy);c.moveTo(cx,50);c.lineTo(cx,430);c.stroke();c.fillText('0',234,35);c.fillText('90',439,244);c.fillText('180',228,455);c.fillText('270',10,244);if(!a)return;const v=a.values;if(!Array.isArray(v)||v.length!==360)return;const min=Math.min(...v),max=Math.max(...v),range=max-min;c.strokeStyle=a.stale?'#778493':'#79dce4';c.lineWidth=2;c.beginPath();v.forEach((x,i)=>{const radius=range===0?r/2:10+(r-10)*(x-min)/range;const theta=i*Math.PI/180;const px=cx+radius*Math.sin(theta),py=cy-radius*Math.cos(theta);if(i===0)c.moveTo(px,py);else c.lineTo(px,py);});c.closePath();c.stroke();}
async function poll(manual=false){
 if(pollPending){if(manual)pollAgain=true;return;}
 pollPending=true;
 if(manual){$('retry').disabled=true;$('retry').textContent='Mencoba lagi...';apiStatus('Mencoba kembali ke Ground API.');}
 try{
  snapshot=await get('/api/v2/snapshot');
  apiSnapshotAvailable=true;
  if(snapshot.last_operation){$('operation').textContent=JSON.stringify(snapshot.last_operation,null,2);rememberShutdownState(snapshot.last_operation);}
  if(snapshot.last_operation?.op==='ppp.restart')rememberPppOperation(snapshot.last_operation);
  renderPppRecords();
  updatePppButton();
  if(csrf)void refreshPppHistory();
  try{await checkShutdownHistory();}catch{}
  updateShutdownButton();
  document.querySelector('main').classList.remove('data-stale');
  const d=snapshot.detection||{};
  renderDiagnostic(snapshot.diagnostic_doa);
  $('state').textContent=`${snapshot.mode} / ${snapshot.health_fresh?'HEALTH FRESH':'HEALTH STALE'} / MQTT ${snapshot.link?.mqtt_control?.state||'--'}`;
  $('angle').textContent=d.valid?`${d.relative_doa_deg.toFixed(1)}\u00b0`:'--';
  $('metadata').textContent=`VFO ${d.frequency_hz?(d.frequency_hz/1e6).toFixed(3):'--'} MHz; PAPR ${d.confidence_native_db??'--'} dB; data umur terima ${d.receipt_age_ms??'--'} ms`;
  $('health').textContent=JSON.stringify({health:snapshot.health,health_age_ms:snapshot.health_age_ms,config:snapshot.config,capabilities:snapshot.capabilities},null,2);
  try{
   const a=await get('/api/v2/angular/latest');plot(a);$('plot').classList.remove('stale');
   $('curve').textContent=a?`${a.encoding.toUpperCase()} / 360 titik / age ${(a.source_age_ms/1000).toFixed(1)} s / ${a.stale?'STALE':'RECEIVED'} / axis native / peak ${a.peak_index} deg. Radius dinormalisasi untuk tampilan; nilai native tetap di API.`:'Belum ada kurva lengkap. Tunggu receipt + source gate + interval.';
   apiStatus('');
  }catch(e){
   $('plot').classList.add('stale');$('curve').textContent='Grafik terakhir tetap ditampilkan sebagai STALE; data angular terbaru gagal dimuat.';
   apiStatus(`Snapshot tersedia; API grafik angular gagal (${e.message}). Data grafik terakhir tetap STALE.`);
  }
 }catch(e){
  apiSnapshotAvailable=false;
  updateShutdownButton();
  updatePppButton();
  $('state').textContent='GROUND API UNAVAILABLE';document.querySelector('main').classList.add('data-stale');
  $('plot').classList.add('stale');
  apiStatus(`Ground API tidak merespons (${e.message}). Data terakhir tetap ditampilkan sebagai STALE.`);
 }
 finally{
  pollPending=false;
  if(manual){$('retry').disabled=false;$('retry').textContent='Coba lagi';}
  if(pollAgain){pollAgain=false;void poll(true);}
  else pollTimer=setTimeout(poll,1000);
 }
}
$('retry').onclick=()=>{
 if(pollTimer){clearTimeout(pollTimer);pollTimer=null;}
 if(pollPending){pollAgain=true;return;}
 void poll(true);
};
get('/api/v2/session').then(r=>{csrf=r.csrf;updatePppButton();if(csrf)void refreshPppHistory(true);}).catch(()=>{});poll();
