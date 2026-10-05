'use strict';
const $=id=>document.getElementById(id);let csrf=null,snapshot={},pollPending=false,pollAgain=false,pollTimer=null,apiSnapshotAvailable=false,shutdownPending=false,shutdownUncertain=false,shutdownHistoryChecked=false,shutdownHistoryAt=0,shutdownHistoryRequest=null;
async function get(p){const r=await fetch(p,{cache:'no-store',signal:AbortSignal.timeout(2000)});if(!r.ok)throw Error(`HTTP ${r.status}`);return r.json();}
function apiStatus(message){$('api-status').hidden=!message;$('api-message').textContent=message;}
async function post(p,j,timeout=6000){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf||''},body:JSON.stringify(j),signal:AbortSignal.timeout(timeout)});const out=await r.json();if(!r.ok){const e=Error(out.error||'Gagal');e.status=r.status;throw e;}return out;}
const shutdownFailureStages=new Set(['FAILED','REJECTED','EXPIRED','CONFLICT','CANCELLED']);
function readShutdownUncertainty(){try{return localStorage.getItem('rdf-node-ground-shutdown-uncertain')==='1';}catch{return false;}}
shutdownUncertain=readShutdownUncertainty();
function setShutdownUncertainty(value){shutdownUncertain=value;try{if(value)localStorage.setItem('rdf-node-ground-shutdown-uncertain','1');else localStorage.removeItem('rdf-node-ground-shutdown-uncertain');}catch{}}
function rememberShutdownState(op){if(op?.op==='system.shutdown.execute'&&!shutdownFailureStages.has(op.stage))setShutdownUncertainty(true);}
function updateShutdownButton(){$('shutdown').disabled=shutdownPending||!apiSnapshotAvailable||!shutdownHistoryChecked||!(snapshot.capabilities?.shutdown&&snapshot.capabilities?.remote_commands);}
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
loginButton.onclick=async()=>{try{const r=await post('/api/v2/login',{pin:pinInput.value});csrf=r.csrf;pinInput.value='';loginButton.disabled=true;$('authstate').textContent='ADMIN - operasi tetap memerlukan izin node.';}catch(e){pinInput.value='';loginButton.disabled=true;$('authstate').textContent=e.message;}};
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
function plot(a){const c=$('plot').getContext('2d');c.clearRect(0,0,480,480);const cx=240,cy=240,r=180;c.strokeStyle='#31455e';c.lineWidth=1;c.fillStyle='#a6b7cf';c.font='14px system-ui';for(const x of [60,120,180]){c.beginPath();c.arc(cx,cy,x,0,Math.PI*2);c.stroke();}c.beginPath();c.moveTo(50,cy);c.lineTo(430,cy);c.moveTo(cx,50);c.lineTo(cx,430);c.stroke();c.fillText('0',234,35);c.fillText('90',439,244);c.fillText('180',228,455);c.fillText('270',10,244);if(!a)return;const v=a.values;if(!Array.isArray(v)||v.length!==360)return;const min=Math.min(...v),max=Math.max(...v),range=max-min;c.strokeStyle=a.stale?'#778493':'#79dce4';c.lineWidth=2;c.beginPath();v.forEach((x,i)=>{const radius=range===0?r/2:10+(r-10)*(x-min)/range;const theta=i*Math.PI/180;const px=cx+radius*Math.sin(theta),py=cy-radius*Math.cos(theta);if(i===0)c.moveTo(px,py);else c.lineTo(px,py);});c.closePath();c.stroke();}
async function poll(manual=false){
 if(pollPending){if(manual)pollAgain=true;return;}
 pollPending=true;
 if(manual){$('retry').disabled=true;$('retry').textContent='Mencoba lagi...';apiStatus('Mencoba kembali ke Ground API.');}
 try{
  snapshot=await get('/api/v2/snapshot');
  apiSnapshotAvailable=true;
  if(snapshot.last_operation){$('operation').textContent=JSON.stringify(snapshot.last_operation,null,2);rememberShutdownState(snapshot.last_operation);}
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
get('/api/v2/session').then(r=>{csrf=r.csrf;}).catch(()=>{});poll();
