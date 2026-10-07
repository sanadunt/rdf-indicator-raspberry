# Protokol dan integrasi Ground

## Namespace

Live: `sdr/v2/uav-01`. Node ID dapat diset melalui config. V1 tidak kompatibel secara diam-diam.
Demo CLI tidak membuka koneksi MQTT. Prefix demo tersedia hanya bagi integration test internal.

| Topic suffix | Arah | QoS | Retained |
|---|---|---:|---|
| telemetry/doa | UAV -> Ground | 0 | tidak |
| telemetry/diagnostic/doa | UAV -> Ground | 0 | tidak |
| telemetry/health | UAV -> Ground | 0 | tidak |
| telemetry/health/detail | UAV -> Ground | 0 | tidak |
| telemetry/angular | UAV -> Ground | 0 | tidak |
| telemetry/diagnostic/angular | UAV -> Ground | 0 | tidak |
| state, capabilities, availability | UAV -> Ground | 1 | ya, last-known |
| settings/reported | UAV -> Ground | 1 | tidak, expiry 30 s |
| cmd/config/get, cmd/config/patch | Ground -> UAV | 1 | tidak |
| cmd/processing/set, cmd/service/restart, cmd/service/ppp/restart | Ground -> UAV | 1 | tidak |
| cmd/system/reboot/prepare, cmd/system/reboot/execute | Ground -> UAV | 1 | tidak |
| cmd/system/shutdown/prepare, cmd/system/shutdown/execute | Ground -> UAV | 1 | tidak |
| cmd/operation/get, cmd/stream/set | Ground -> UAV | 1 | tidak |
| ack/config, ack/operation | UAV -> Ground | 1 | tidak |


Session expiry nol, clean start selalu. State/capabilities mengaitkan alias sid 8 hex dengan
boot UUID dan instance UUID. Alias yang belum dikenal atau bentrok tidak menjadi authority.
Jangan memperlakukan retained state sebagai heartbeat. Freshness menggunakan health/sumber.

## MQTT network transport

The client supports MQTT 5 over TCP or WebSocket. `tls: true` (the default) selects TCP/TLS or
WSS, requires TLS 1.2+, and verifies the certificate chain and hostname/IP. If `ca_file` is unset,
the system CA store is used; private/self-signed broker roots need an explicit CA file.
`tls: false` selects plain TCP or `ws://`; broker credentials and MQTT payloads are unencrypted.
Without configured MQTT credentials, the client uses anonymous CONNECT. Edge does not
authenticate publisher identity or distinguish a Ground principal in MQTT messages. Broker
ACL and network isolation limit which publishers can reach command topics; if anonymous
publishers are allowed there, any reachable publisher can use enabled remote operations.
Plaintext leaves credentials and payloads unencrypted. Use it only on an approved private,
trusted link. There is no TLS mode that disables verification.

WebSocket uses RFC 6455 and the `mqtt` subprotocol at `mqtt.websocket_path` (default `/mqtt`).
MQTT Control Packets travel only in WebSocket binary frames; the parser treats the payload as a
byte stream, not frame-aligned packets. Client frames are masked, server frames must be unmasked,
and fragmentation/control frames are handled.

## DoA compact

```json
{"v":2,"sid":"7a8b9c0d","q":1245,"t":1790668800123,"f":433920000,"a":137.4,"c":8.27,"p":-54.2,"rev":7,"ok":1}
```

`t`: timestamp native ms; `q`: sequence sampel adapter, bukan frame DAQ.
`a`: bila `ok=1`, sudut menurut konvensi config; bila `ok=0`, nilai raw source dan hanya
berarti RAW dengan `angle_reference="RAW"`. `c`: PAPR/native dB, bukan probabilitas;
`p`: power native signed; `f`: VFO Hz; `rev`: safe config revision-at-sample.
`ok=1` berarti kandidat LIVE terverifikasi. Varian unverified pada topic yang sama:

```json
{"v":2,"sid":"7a8b9c0d","q":1246,"t":1790668800123,"f":433920000,"a":10.0,"c":8.27,"p":-54.2,"rev":7,"ok":0,"trust":"UNVERIFIED","angle_reference":"RAW","validation_reasons":["SOURCE_UNVERIFIED","ANGLE_UNVERIFIED"]}
```

Edge hanya mengirim varian ini bila parsing, freshness/clock, DAQ, dan atribusi config lulus
serta satu-satunya gate yang gagal adalah authority dan/atau verifikasi konvensi sudut.
Ground menampilkannya di jalur diagnostic; tidak mengisi detection atau menjadi dasar kontrol.
Nilai contoh sintetis. Radio tidak menambahkan SNR yang belum terbukti sumbernya.

## Health

```json
{"v":2,"sid":"7a8b9c0d","q":86,"t":1790668800123,"run":1,"daq":1,"drop":12,"age":280,"temp":61.4,"clk":1,"rev":7}
```

Run: 0 stopped, 1 running, 2 starting, 3 stopping, 4 error, 255 unknown.
DAQ: 0 degraded, 1 healthy, 2 unknown. `age` ialah umur DoA pada source, bukan latency E2E.
Clock: 0 untrusted, 1 synced. Field unavailable dapat null. Health q tetap maju saat SDR stop.

## Grafik lengkap 360 titik

`telemetry/angular` dan `telemetry/diagnostic/angular` masing-masing membawa satu object JSON
compact per MQTT message. `values` berisi semua 360 sampel dalam urutan index native. Edge tidak
memecah frame menjadi chunk dan tidak mengkuantisasi sampel menjadi Q16 atau U8.

| Field | Bentuk |
|---|---|
| `v`, `encoding` | Wajib `2` dan `"json"`. |
| `sid`, `q` | Alias sesi 8 digit hex huruf kecil; sequence publish positif 32-bit. |
| `timestamp_ms`, `frequency_hz` | Timestamp source dalam ms dan frekuensi Hz positif. |
| `revision` | Revision konfigurasi 32-bit atau `null` bila tidak diketahui. |
| `vfo`, `convention` | VFO `0..15`; convention `0` atau `1`. Index native tetap dipakai, tidak otomatis menjadi true north. |
| `flags` | Bitmask bukti `0..63`, didefinisikan di bawah. |
| `raw_doa_deg`, `confidence_native_db` | Nilai metadata native; keduanya dapat `null`. |
| `values` | Tepat 360 angka JSON finite, tanpa kuantisasi, dalam batas decoder `-1e8..1e8`. |

Contoh decode:

```python
import json

frame = json.loads(payload)  # bytes dari satu PUBLISH Angular
assert frame['encoding'] == 'json'
samples = frame['values']
assert len(samples) == 360
```

Ground memeriksa session, source age, health fresh dengan `daq=1`, sequence, dan bukti flags.
Frame LIVE memerlukan `flags=63`, timestamp paling lama 10 s, dan sequence yang maju. `revision`
tetap metadata; Ground tidak membandingkannya dengan `state.cfg`. Bit config attribution tetap
wajib. Pesan yang kehilangan convention dan/atau authority masuk ke diagnostic `UNVERIFIED`;
pesan yang kehilangan salah satu bit integritas ditolak.

Flags: bit 0 parsed, bit 1 clock/fresh, bit 2 DAQ sehat, bit 3 convention verified, bit 4
config attribution, bit 5 source authority verified.

Ukuran JSON mengikuti digit metadata dan nilai source. Broker menerima satu payload berisi seluruh
array; consumer dapat langsung menjalankan `json.loads` dan memeriksa panjang `values`.

Timestamp convention 1 mempertahankan index native; Ground tidak mengonversi sudut ke true north.
Kurva hanya dinyatakan LIVE sesudah seluruh gate di atas lolos.

## Requested native settings

Ground mengirim `cmd/config/get` hanya melalui aksi eksplisit `Refresh config`. Edge tidak meminta
atau mengirim settings saat startup, reconnect, atau perubahan revision. Request memerlukan MQTT
Ground READY dan session `sid`/`boot` terkini, tetapi tidak memerlukan health fresh. Semua operasi
lain tetap mengikuti health, identity, clock, revision, capability, dan approval gates.

Edge merespons lewat `settings/reported` dengan QoS 1, non-retained, expiry 30 s:

```json
{"v":2,"sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","id":"g01-00001234","rev":7,"t":1790668800123,"settings_json":"{\"center_freq\":433.92,\"uniform_gain\":20.7}"}
```

Field wajib tepat `v,sid,boot,id,rev,t,settings_json`. `settings_json` menyimpan teks UTF-8
persis dari file native yang berhasil dibaca, bukan normalisasi atau safe-field serialization.
TLS wajib; Edge menolak TLS-off, source settings unavailable/stale, atau compact envelope di atas
8.192 byte dengan error spesifik. Edge tidak memotong atau memecah report. `ack/config` memuat
status/error command; settings datang pada PUBLISH terpisah.

Ground menerima hanya PUBLISH non-retained yang cocok dengan pending `id`, session `sid`, `boot`,
dan timestamp fresh. Ground mem-parse teks sebagai JSON object, menyimpan teks mentah, lalu
menurunkan subset safe untuk form mutation. Safe settings hanya diberikan bila report `rev`
cocok dengan `state.cfg`; teks mentah tetap dapat ditampilkan.

## Telemetry delivery and evidence

Ground tidak mengirim konfirmasi aplikasi untuk telemetry. QoS 1 PUBACK hanya berarti broker
mengakui PUBLISH; QoS 0 `SOCKET_WRITE` hanya berarti Edge menulis frame ke socket lokal.
Telemetry dikirim berdasar source/profile, queue, budget, dan stabilisasi lokal, bukan keberadaan
Ground. Clean Start tanpa offline replay berarti telemetry dapat terlewat saat Ground offline.

LIVE DoA/Angular tetap memerlukan session, freshness, sequence, health/DAQ, parse, clock, source,
dan angle evidence yang berlaku. Ground tidak mengharuskan `rev` telemetry sama dengan `state.cfg`;
revision tetap metadata dan bit config attribution Angular tetap wajib. Jika hanya authority
atau angle approval yang hilang, Edge menandai frame `UNVERIFIED`; kegagalan integritas lain
tetap memblokir jalur normal.

Perubahan Angular wire format memerlukan Edge dan Ground package yang diperbarui bersama. Consumer
RDF2 lama yang mengharapkan binary chunk tidak dapat membaca JSON baru. Consumer DoA lama juga
dapat menolak `ok=0` atau salah menafsirkan `a` sebagai sudut relatif.

`telemetry/diagnostic/doa` dikirim tiap 3 s selama XML tersedia, terlepas dari validitas DoA
normal. `q` bertambah tiap publish; timestamp sumber/observasi tetap menunjuk pembacaan file
yang sama. Ground menyimpan sampel sebagai `UNVERIFIED`, terpisah dari DoA/Angular LIVE dan
otorisasi command.

Kedua topic diagnostic memakai Control token bucket bersama health/DoA normal (default 850 B/s).
Frame JSON multi-kilobyte dijadwalkan dengan headroom Control yang tersisa; QoS 0 tetap dapat
expired jika backlog atau trafik lain memakai budget. Broker ACL harus mengizinkan Control Edge
publish dan Control Ground subscribe.

## Diagnostic angular candidate

Topic `telemetry/diagnostic/angular` membawa satu object JSON berisi 360 sampel terakhir dari
`DOA_value.html`, hanya saat jalur angular Bulk normal tidak dapat mengirimnya. Interval 6 s saat
gate integritas source gagal dan 30 s saat source valid tetapi Bulk terblokir adalah minimum;
ukuran payload dan headroom Control dapat memperpanjangnya. Tidak ada duplikasi saat Bulk normal.
Profile `control` menahan array. Topic memakai Control QoS 0, non-retained, expiry 3 s.
Ground menyimpan candidate terpisah sebagai `UNVERIFIED`; ia tidak memenuhi detection LIVE.
`q` adalah sequence publish diagnostic, bukan sequence file sumber.

Bits `flags` menyatakan bukti yang tersedia saat Edge mengirim:

| Bit | Bukti |
|---:|---|
| 0 | record berhasil diparse |
| 1 | clock dipercaya dan source masih fresh |
| 2 | DAQ sehat |
| 3 | konvensi sudut diverifikasi lokal |
| 4 | atribusi konfigurasi cocok |
| 5 | authority source diverifikasi lokal |
Frame live memerlukan `flags=63`. Frame pada `telemetry/angular` yang memiliki bit parsed,
fresh, DAQ, dan config tetapi kehilangan bit convention dan/atau authority diterima sebagai
`trust=UNVERIFIED` di diagnostic; flags legacy `31` berarti authority tidak terbukti dan
diturunkan ke jalur itu. Ground menolak frame normal-topic yang juga kehilangan bit parsed,
fresh, DAQ, atau config.
Frame diagnostic tetap terpisah dari `telemetry/angular` dan `telemetry/doa`.
Timestamp stale, health/processing, revision, dan flags menjadi `validation_reasons`/`stale`,
bukan alasan membuang sampel. Baca melalui `GET /api/v2/angular/diagnostic/latest`
(`null` sebelum pesan JSON diterima).

Response memuat 360 `values`, `encoding`, `frequency_hz`, `raw_doa_deg`,
`source_timestamp_ms`, `source_age_ms`, `received_age_ms`, `flags`,
`validation_reasons`, dan `stale`.

Encoding selalu `"json"` untuk semua profile. Seluruh 360 nilai dikirim sebagai angka JSON tanpa
kuantisasi Q16/U8. Gate LIVE pada `telemetry/angular` mensyaratkan seluruh flags `63` dan
verifikasi Ground.

## Command envelope

```json
{"v":2,"id":"g01-00001234","sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","issued_ms":1790668800123,"expires_ms":1790668815123,"base_rev":7,"op":"config.patch","changes":{"center_frequency_hz":433920000,"vfo0_frequency_hz":433920000}}
```

PPP restart uses the ordinary v2 envelope on `cmd/service/ppp/restart`:

```json
{"v":2,"id":"g01-00001235","sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","issued_ms":1790668800123,"expires_ms":1790668815123,"base_rev":7,"op":"ppp.restart"}
```

Semua nilai contoh adalah fixture, jangan dikirim mentah. Ground membuat envelope dari identity
terkini dan `state.cfg` untuk `base_rev`. TTL default 15 s, maksimum 30 s. Unknown fields ditolak.
ID sama+payload sama mengembalikan hasil/progress tersimpan, bukan eksekusi ulang.
ID sama+payload berbeda ditolak. Topic harus sesuai `op` dan command tidak retained.

ACK berisi `v,sid,id,status,t,rev,result`. APPLIED memerlukan bukti sesuai aksi.
PERSISTED_UNVERIFIED dan OUTCOME_UNKNOWN bukan sinonim gagal atau berhasil.
Hasil tersedia lewat jurnal setelah reconnect; tidak bergantung broker offline history.

Untuk `ppp.restart`, operasi yang diterima mula-mula mengirim `PPP_RESTART_REQUESTED` dengan
`accepted_by_systemd: true`, timestamp request, dan nama unit tetap `t900-ppp.service`.
Ground hanya melaporkan `APPLIED` setelah health yang lebih baru dari request diterima pada
sesi node yang sama; status ini bukan bukti PPP atau link MQTT pulih. Ground tidak mengulang
hasil yang tetap unknown. Request baru memerlukan health fresh yang lebih baru dari setiap
request unresolved serta konfirmasi operator; record lama tetap unknown. Konfirmasi itu adalah
intent lokal Ground, bukan field envelope MQTT. Request tidak dapat tiba setelah seluruh jalur
Control putus.

## API untuk dashboard lama

Receiver Ubuntu: `http://127.0.0.1:8791`.

- `GET /api/v2/snapshot`: node/health/metadata yang telah digate.
  `diagnostic_doa` berisi sampel XML terpisah dengan timestamp sumber/observasi, alasan validasi dan status stale; sampel ini bukan DoA valid.
- `GET /api/v2/angular/latest`: array 360, metadata dan `stale`.
- `GET /api/v2/config`: `sdr_revision`, `settings_revision`, `proof`, `safe_settings`,
  `settings_json`, dan metadata `reported`. Teks mentah tersedia hanya setelah settings diminta;
  safe fields hanya saat report revision cocok dengan `state.cfg`.
- `GET /api/v2/operations/latest`: public results, challenge disensor.
- `GET /api/v2/operations/pending-shutdowns`: ringkasan publik `{"pending":true|false}` dari seluruh jurnal operasi shutdown yang belum gagal pasti; tidak dibatasi 20 record terbaru dan tidak memuat ID.
- `GET /api/v2/capabilities`: capability yang dilaporkan node.
- `POST /api/v2/login`: PIN lokal enam digit, field JSON `pin`, HttpOnly cookie+CSRF.
- `POST /api/v2/commands`: intent lokal terautentikasi; backend membuat envelope v2, frontend tidak. `config.get` memerlukan MQTT READY dan session terkini, tetapi bukan health fresh; semua operation lain tetap memerlukan health fresh. Klien boleh memberi `id` untuk query hasil setelah respons execute hilang. Untuk `ppp.restart`, `confirm_previous_unknown` adalah flag lokal yang tidak melintasi MQTT. Tanpa `id`, server membuat ID.
- `POST /api/v2/operation/result`: private result, membutuhkan auth+CSRF.

Saat Ground mengubah sesi node, ia merekonsiliasi seluruh shutdown pending dari jurnal, bukan hanya window latest. Backend Ground hanya menyimpan/mengirim command dengan ID klien yang sudah tervalidasi; browser tidak memilih hasil operasi lain untuk menggantikan ID tersebut.

Gunakan backend Ground lama untuk mengakses API pendamping secara server-to-server lokal.
Browser frontend pada origin berbeda tidak diberi CORS wildcard. Jangan expose port8791 ke
seluruh LAN hanya agar browser lintas-origin dapat memanggilnya.
