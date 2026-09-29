'use strict';
const $=id=>document.getElementById(id);
let snapshot={}, csrf=null, authenticated=false, lastSeq=null, lastProgress=0, lastApi=0, linkData=false, hostPage=false, lastTouch=Date.now();
const good=['UP','CONNECTED','RECEIVING','HEALTHY','SYNCED','APPLIED'];
const bad=['ERROR','LOST','DEGRADED','FAILED','UNAVAILABLE'];
function label(id,text,code){const el=$(id);if(!el)return;el.textContent=text;el.classList.remove('good','warn','bad','neutral');el.classList.add(good.includes(code)?'good':bad.includes(code)?'bad':code?'warn':'neutral');}
function fmt(v,dec=1){return typeof v==='number'&&Number.isFinite(v)?v.toFixed(dec):'--';}
function age(ms){return typeof ms==='number'?`${fmt(ms/1000)} dtk`:'--';}
function row(label,value,code){const r=document.createElement('div');r.className='row';const l=document.createElement('label');l.textContent=label;const v=document.createElement('b');v.textContent=String(value??'--');if(code)v.className=good.includes(code)?'good':bad.includes(code)?'bad':'warn';r.append(l,v);return r;}
function rows(id,values){const e=$(id);e.replaceChildren(...values.map(v=>row(...v)));}
function render(s){
 const d=s.detection||{},l=s.link||{},h=s.host||{},q=s.daq||{},c=s.config||{},g=l.ground||{},p=s.processing||{};
 $('mode').textContent=s.mode==='DEMO'?'DEMO':'';
 const names={RUNNING:'RDF BERJALAN',STOPPED:'RDF BERHENTI',STARTING:'MEMULAI RDF',STOPPING:'MENGHENTIKAN',ERROR:'RDF ERROR',UNKNOWN:'RDF UNKNOWN'};
 label('run',names[p.observed]||'MENUNGGU',p.observed==='RUNNING'?'HEALTHY':p.observed);
 label('ppp',l.ppp||'--',l.ppp);label('mqtt',l.mqtt_control?.state||'DISABLED',l.mqtt_control?.state);
 label('ground',g.state==='RECEIVING'?age(g.age_ms):g.state==='UNCONFIRMED'?'BELUM TERBUKTI':g.state||'--',g.state);
 $('angle').textContent=d.valid?`${fmt(d.relative_doa_deg)}\u00b0`:'--';
 $('angle').className=d.valid?'good':'neutral';
 $('age').textContent=d.valid?`Umur ${age(d.source_age_ms)}`:`${d.state||'MENUNGGU'} / ${age(d.source_age_ms)}`;
 $('freq').textContent=d.frequency_hz?`${fmt(d.frequency_hz/1e6,3)} MHz`:'-- MHz';
 $('quality').textContent=`PAPR ${fmt(d.confidence_native_db,2)} dB / P ${fmt(d.power_native_db)} dB`;
 label('daq',q.state==='HEALTHY'?'SINKRON':q.state||'UNKNOWN',q.state);
 const syncnames={SYNCED:'SAMA',REPORTED_SAME:'REPORT SAMA',UNVERIFIED:'BELUM TERVERIFIKASI',PENDING:'BELUM SAMA',LAST_KNOWN:'TERAKHIR SAMA'};
 label('sync',`${syncnames[c.ground_sync]||'UNVERIFIED'}${c.sdr_revision?` r${c.sdr_revision}`:''}`,c.ground_sync);
 const op=s.last_operation;$('command').textContent=op?`${op.op} / ${op.stage}`:'Belum ada operasi';
 const alerts=s.active_alerts||[];const a=alerts.find(x=>x.severity==='error')||alerts[0];
 $('alert').textContent=a?a.text:'Status lokal normal';$('alertbox').className=`alert ${a?(a.severity==='error'?'bad':'warn'):'good'}`;
 $('alerticon').textContent=a?'!':'\u2713';$('temp').textContent=`${fmt(h.temperature_c)}\u00b0C`;
 if(!linkData){rows('linkrows',[["T900 USB",l.usb],["PPP / interface",`${l.ppp||'--'} / ${l.interface||'--'}`],["MQTT CTRL / BULK",`${l.mqtt_control?.state||'--'} / ${l.mqtt_bulk?.state||'--'}`],["Ground backend",`${g.state||'--'} / ${age(g.age_ms)}`,g.state],["TX / RX (PPP/IP)",`${fmt(l.tx_kbit_s,2)} / ${fmt(l.rx_kbit_s,2)} kbit/s`]]);}
 else rows('linkrows',[["Profil diminta",l.profile],["Grafik pause",l.bulk_pause||'STREAMING'],["Queue CTRL / BULK",`${l.mqtt_control?.depth||0} / ${l.mqtt_bulk?.depth||0}`],["Receipt hq / aq",`${g.last?.hq??'--'} / ${g.last?.aq??'--'}`],["Parse / angular drop",`${d.parse_errors||0} / ${l.angular_aborted||0}`]]);
 const syn=q.sync||{};
 if(!hostPage) rows('sysrows',[["Engine / desired",`${p.observed||'--'} / ${p.desired||'BELUM DIAMBIL'}`],["Frame / Delay / IQ",`${syn.frame??'?'} / ${syn.sample_delay??'?'} / ${syn.iq??'?'}`],["Frame / progress",`${q.frame_index??'--'} / ${q.frame_progressing?'MAJU':'BELUM'}`],["Drop total / delta",`${q.dropped_frames??'--'} / ${q.drop_delta??'--'}`],["DAQ umur / USB SDR",`${age(q.source_age_ms)} / ${h.usb_count??'--'} terdeteksi`]]);
 else rows('sysrows',[["CPU / RAM",`${fmt(h.cpu_percent)}% / ${fmt(h.memory_percent)}%`],["Suhu / disk kosong",`${fmt(h.temperature_c)} C / ${fmt(h.disk_free_percent)}%`],["Throttle / under-voltage",`${h.throttled??'unknown'} / ${h.undervoltage??'unknown'}`],["Uptime",h.uptime_s==null?'--':`${Math.floor(h.uptime_s/60)} menit`],["Jam sistem",h.clock_state,h.clock_state]]);
 rows('configrows',[["Node / profil",`${s.node_id||'--'} / ${c.profile||'--'}`],["Source / MQTT",`${c.source_configured?'OK':'SETUP'} / ${c.mqtt_configured?'CONFIGURED':'OFF'}`],["RF revision / proof",`${c.sdr_revision??'--'} / ${c.proof||'--'}`],["Akses / maintenance",`${authenticated?'ADMIN':'READ ONLY'} / ${s.capabilities?.maintenance?'AKTIF':'OFF'}`]]);
 $('admin').textContent=authenticated?'Logout':'Login';
 $('configreason').textContent='Kontrol perlu approval adapter dan maintenance lokal.';
 document.body.classList.toggle('light',c.preferences?.theme==='light');
}
async function get(path){const response=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(1200)});if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();}
async function post(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf||''},body:JSON.stringify(body),signal:AbortSignal.timeout(6000)});let j=await response.json();if(!response.ok)throw new Error(j.error||j.result?.error||j.stage||'Permintaan gagal');return j;}
async function poll(){try{const s=await get('/api/v2/snapshot');lastApi=Date.now();if(s.snapshot_seq!==lastSeq&&s.snapshot_seq!=null){lastSeq=s.snapshot_seq;lastProgress=Date.now();}snapshot=s;render(s);}catch(e){/* local watchdog displays staleness independently */}finally{setTimeout(poll,500);}}
setInterval(()=>{const stale=Date.now()-lastProgress>5000;$('stale').hidden=!stale;if(stale)$('stalereason').textContent=Date.now()-lastApi>5000?'API lokal tidak merespons.':'API hidup, snapshot tidak bergerak.';const sec=snapshot.config?.preferences?.blank_after_seconds||0;if(sec>0&&Date.now()-lastTouch>sec*1000&&!snapshot.active_alerts?.some(x=>x.severity==='error'))$('blank').hidden=false;},250);
for(const button of document.querySelectorAll('nav button'))button.addEventListener('click',()=>{for(const e of document.querySelectorAll('.page'))e.classList.toggle('active',e.id===button.dataset.tab);for(const e of document.querySelectorAll('nav button'))e.classList.toggle('selected',e===button);});
$('linkpage').onclick=()=>{linkData=!linkData;$('linkpage').textContent=linkData?'Link \u203a':'Data \u203a';render(snapshot);};
$('syspage').onclick=()=>{hostPage=!hostPage;$('syspage').textContent=hostPage?'DAQ \u203a':'Host \u203a';$('systitle').textContent=hostPage?'RASPBERRY / HOST':'RDF / DAQ';render(snapshot);};
function modal(title){$('modaltitle').textContent=title;$('modalbody').replaceChildren();$('modalmsg').textContent='';$('modal').hidden=false;return $('modalbody');}
$('closemodal').onclick=()=>$('modal').hidden=true;
function text(parent,s){const p=document.createElement('p');p.textContent=s;parent.append(p);return p;}
function action(parent,label,fn,danger=false){let wrap=parent.querySelector('.actions');if(!wrap){wrap=document.createElement('div');wrap.className='actions';parent.append(wrap);}let b=document.createElement('button');b.textContent=label;if(danger)b.className='danger';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){$('modalmsg').textContent=e.message;}finally{b.disabled=false;}};wrap.append(b);return b;}
function needLogin(){if(authenticated)return false;login();return true;}
function login(){const box=modal('Login admin lokal');text(box,'Password dibuat saat instalasi. Tidak ada password default.');const input=document.createElement('input');input.type='password';input.autocomplete='current-password';input.placeholder='Password admin';box.append(input);action(box,'Masuk',async()=>{const r=await post('/api/v2/login',{password:input.value});csrf=r.csrf;authenticated=true;$('modal').hidden=true;render(snapshot);});input.focus();}
$('admin').onclick=async()=>{if(!authenticated)return login();await post('/api/v2/logout',{});authenticated=false;csrf=null;render(snapshot);};
async function command(op,extras={}){const r={v:2,id:`local-${Date.now()}-${crypto.randomUUID().slice(0,8)}`,sid:snapshot.sid,boot:snapshot.boot_id,issued_ms:Date.now(),expires_ms:Date.now()+15000,base_rev:snapshot.config?.sdr_revision??null,op,...extras};const result=await post('/api/v2/commands',r);$('modalmsg').textContent=`${result.stage}: ${result.id||''}`;return result;}
$('profilebtn').onclick=()=>{if(needLogin())return;const box=modal('Profil telemetry');text(box,'Grafik otomatis dipause saat command, data invalid, atau receipt hilang.');action(box,'CONTROL',()=>command('stream.set',{profile:'control'}));action(box,'BALANCED',()=>command('stream.set',{profile:'balanced'}));action(box,'GRAPH U8',()=>command('stream.set',{profile:'graph_u8'}));};
$('themebtn').onclick=async()=>{if(needLogin())return;try{await post('/api/v2/display/preferences',{theme:document.body.classList.contains('light')?'dark':'light'});}catch(e){modal('Preferensi');$('modalmsg').textContent=e.message;}};
$('controlbtn').onclick=()=>{if(needLogin())return;const box=modal('Kontrol RDF - bukan bridge');const caps=snapshot.capabilities||{};text(box,`Maintenance: ${caps.maintenance?'AKTIF':'OFF'}; start/stop hanya stack yang di-approve.`);
 action(box,'Start',()=>command('processing.set',{desired:'RUNNING'})).disabled=!caps.processing;
 action(box,'Stop',()=>confirmOperation('STOP RDF','processing.set',{desired:'STOPPED'}),true).disabled=!caps.processing;
 action(box,'Restart RDF',()=>confirmOperation('RESTART RDF','service.restart',{}),true).disabled=!caps.restart;
 const row=document.createElement('div');box.append(row);
 action(row,'Frekuensi',()=>frequency()).disabled=!caps.config_patch;
 action(row,'Refresh config',()=>command('config.get'));
 action(row,'Reboot Pi',()=>prepareReboot(),true).disabled=!caps.reboot;
};
function confirmOperation(label,op,extra){const box=modal(label);text(box,'Aksi ini mengganggu pemrosesan RDF. Bridge tetap hidup kecuali reboot OS. Pastikan kondisi maintenance aman.');action(box,'Konfirmasi',()=>command(op,extra),true);}
function frequency(){const box=modal('Frekuensi center + VFO0');text(box,'MHz; range dan gain mengikuti policy perangkat. File tersimpan belum berarti runtime terverifikasi.');const input=document.createElement('input');input.type='number';input.step='0.001';input.value=snapshot.detection?.frequency_hz?snapshot.detection.frequency_hz/1e6:'';box.append(input);action(box,'Apply',()=>{const hz=Math.round(Number(input.value)*1e6);if(!Number.isFinite(hz)||hz<=0)throw new Error('Frekuensi tidak valid');return command('config.patch',{changes:{center_frequency_hz:hz,vfo0_frequency_hz:hz}});});}
async function prepareReboot(){const box=modal('Reboot Raspberry');text(box,'Node akan offline. Butuh maintenance lease lokal (sudo). Health berhenti selama boot.');action(box,'Prepare',async()=>{const r=await command('system.reboot.prepare');$('modalmsg').textContent='Menunggu challenge...';let op=null;for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,300));const list=await get('/api/v2/operations/latest');op=list.find(x=>x.id===r.id);if(op&&['FAILED','APPLIED','REJECTED'].includes(op.stage))break;}
 // Challenges are deliberately not returned by public history. A local reboot is
 // issued through authenticated command result access (private endpoint below).
 const result=await post('/api/v2/operation/result',{id:r.id});
 if(result.stage!=='APPLIED'||!result.result?.challenge)throw new Error(result.result?.error||'Challenge belum tersedia');
 const cbox=modal('Konfirmasi reboot OS');text(cbox,'Konfirmasi dalam 30 detik. Reboot mulai setelah hasil dijurnal.');action(cbox,'REBOOT SEKARANG',()=>command('system.reboot.execute',{prepare_id:r.id,challenge:result.result.challenge}),true);
 });}
document.addEventListener('pointerdown',()=>{lastTouch=Date.now();$('blank').hidden=true;});document.addEventListener('keydown',()=>{lastTouch=Date.now();$('blank').hidden=true;});
get('/api/v2/session').then(s=>{authenticated=s.authenticated;csrf=s.csrf;}).catch(()=>{});poll();
