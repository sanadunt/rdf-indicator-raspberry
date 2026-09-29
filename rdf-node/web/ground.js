'use strict';
const $=id=>document.getElementById(id);let csrf=null,snapshot={};
async function get(p){const r=await fetch(p,{cache:'no-store',signal:AbortSignal.timeout(2000)});return r.json();}
async function post(p,j){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf||''},body:JSON.stringify(j)});const out=await r.json();if(!r.ok)throw Error(out.error||'Gagal');return out;}
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
$('tune').onclick=()=>{const f=Math.round(Number($('frequency').value)*1e6);if(f>0&&Number.isFinite(f))command('config.patch',{changes:{center_frequency_hz:f,vfo0_frequency_hz:f}});};
$('setprofile').onclick=()=>command('stream.set',{profile:$('profile').value});
function plot(a){const c=$('plot').getContext('2d');c.clearRect(0,0,480,480);const cx=240,cy=240,r=180;c.strokeStyle='#31455e';c.lineWidth=1;c.fillStyle='#a6b7cf';c.font='14px system-ui';for(const x of [60,120,180]){c.beginPath();c.arc(cx,cy,x,0,Math.PI*2);c.stroke();}c.beginPath();c.moveTo(50,cy);c.lineTo(430,cy);c.moveTo(cx,50);c.lineTo(cx,430);c.stroke();c.fillText('0',234,35);c.fillText('90',439,244);c.fillText('180',228,455);c.fillText('270',10,244);if(!a)return;const v=a.values;if(!Array.isArray(v)||v.length!==360)return;const min=Math.min(...v),max=Math.max(...v),range=max-min;c.strokeStyle=a.stale?'#778493':'#79dce4';c.lineWidth=2;c.beginPath();v.forEach((x,i)=>{const radius=range===0?r/2:10+(r-10)*(x-min)/range;const theta=i*Math.PI/180;const px=cx+radius*Math.sin(theta),py=cy-radius*Math.cos(theta);if(i===0)c.moveTo(px,py);else c.lineTo(px,py);});c.closePath();c.stroke();}
async function poll(){try{snapshot=await get('/api/v2/snapshot');const d=snapshot.detection||{};$('state').textContent=`${snapshot.mode} / ${snapshot.health_fresh?'HEALTH FRESH':'HEALTH STALE'} / MQTT ${snapshot.link?.mqtt_control?.state||'--'}`;$('angle').textContent=d.valid?`${d.relative_doa_deg.toFixed(1)}\u00b0`:'--';$('metadata').textContent=`VFO ${d.frequency_hz?(d.frequency_hz/1e6).toFixed(3):'--'} MHz; PAPR ${d.confidence_native_db??'--'} dB; data umur terima ${d.receipt_age_ms??'--'} ms`;$('health').textContent=JSON.stringify({health:snapshot.health,health_age_ms:snapshot.health_age_ms,config:snapshot.config,capabilities:snapshot.capabilities},null,2);if(snapshot.last_operation)$('operation').textContent=JSON.stringify(snapshot.last_operation,null,2);const a=await get('/api/v2/angular/latest');plot(a);$('curve').textContent=a?`${a.encoding.toUpperCase()} / 360 titik / age ${(a.source_age_ms/1000).toFixed(1)} s / ${a.stale?'STALE':'RECEIVED'} / axis native / peak ${a.peak_index} deg. Radius dinormalisasi untuk tampilan; nilai native tetap di API.`:'Belum ada kurva lengkap. Tunggu receipt + source gate + interval.';}catch(e){$('state').textContent='GROUND API UNAVAILABLE';}setTimeout(poll,1000);}
get('/api/v2/session').then(r=>{csrf=r.csrf;}).catch(()=>{});poll();
