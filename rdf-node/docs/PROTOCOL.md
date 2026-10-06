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
| state, capabilities, config/reported, availability | UAV -> Ground | 1 | ya, last-known |
| ground/receipt | Ground -> UAV | 0 | tidak |
| cmd/config/get, cmd/config/patch | Ground -> UAV | 1 | tidak |
| cmd/processing/set, cmd/service/restart | Ground -> UAV | 1 | tidak |
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
Use plaintext only on an approved trusted link. There is no TLS mode that disables verification.

WebSocket uses RFC 6455 and the `mqtt` subprotocol at `mqtt.websocket_path` (default `/mqtt`).
MQTT Control Packets travel only in WebSocket binary frames; the parser treats the payload as a
byte stream, not frame-aligned packets. Client frames are masked, server frames must be unmasked,
and fragmentation/control frames are handled.

## DoA compact

```json
{"v":2,"sid":"7a8b9c0d","q":1245,"t":1790668800123,"f":433920000,"a":137.4,"c":8.27,"p":-54.2,"rev":7,"ok":1}
```

`t`: timestamp native ms; `q`: urutan sampel adapter, bukan frame DAQ.
`a`: konvensi angle config; `c`: PAPR/native dB, bukan probabilitas;
`p`: power native signed; `f`: VFO Hz; `rev`: safe config revision-at-sample.
Nilai di atas sintetis. Radio tidak menambahkan SNR yang belum terbukti sumbernya.

## Health

```json
{"v":2,"sid":"7a8b9c0d","q":86,"t":1790668800123,"run":1,"daq":1,"drop":12,"age":280,"temp":61.4,"clk":1,"rev":7}
```

Run: 0 stopped, 1 running, 2 starting, 3 stopping, 4 error, 255 unknown.
DAQ: 0 degraded, 1 healthy, 2 unknown. `age` ialah umur DoA pada source, bukan latency E2E.
Clock: 0 untrusted, 1 synced. Field unavailable dapat null. Health q tetap maju saat SDR stop.

## Grafik lengkap 360 titik

MQTT payload binary, bukan JSON/Base64. Decoder tersedia:

```python
from rdf_node.codec import Assembler
assembler = Assembler()
# payload berasal dari telemetry/angular, satu envelope chunk per MQTT message
frame = assembler.add(payload)
if frame is not None:
    samples = frame['values']  # exact 360 reconstructed numeric values
```

Sebelum diterima sebagai LIVE, Ground juga memeriksa sid/boot, revision, flags, source age,
health fresh, dan monotonic q. Contoh kode assembler saja bukan semua gate; gunakan
`Ground.receive` sebagai contoh integrasi lengkap.

Header little-endian `<4sBBHIIQIIBBHffHh`, 48 bytes:

| Field | Jenis |
|---|---|
| magic | 4 bytes RDF2 |
| version, encoding | u8, u8 (1 Q16, 2 U8) |
| flags | u16: parsed/fresh/DAQ/convention/config bits |
| sid, q | u32, u32 |
| timestamp | u64 ms |
| frequency, revision | u32, u32; revision ffffffff berarti unknown |
| vfo, convention, count | u8, u8, u16; count wajib360 |
| scale, offset | float32, float32 |
| raw CSV DoA cdeg, confidence centi | u16, i16 |

Q16: sample signed int16, scale0.01 offset0. Header48+720=768 bytes.
Range -327.67..327.67; -32768 reserved invalid; overflow ditolak, tidak clipped.
U8: sample unsigned byte, scale/offset per frame; header48+360=408 bytes.
Constant array scale0 menghasilkan semua offset. Keduanya kuantisasi, bukan lossless CSV.

Envelope chunk little-endian `<IIBBH`:
`sid(u32), q(u32), index(u8), count(u8), total-frame-length(u16)`.
Q16 default2 x (12+384)=792 payload bytes. U8 1 x (12+408)=420.
Assembler maximum2 incomplete frames, deadline3s, consistency and duplicate checks.
Kurva parsial tidak dirender. Konvensi1 mempertahankan index native; tidak otomatis true north.

## Receipt

```json
{"v":2,"sid":"7a8b9c0d","dq":1245,"hq":86,"aq":1238,"rev":7}
```

Dikirim setiap5s oleh satu receiver yang benar-benar mendecode. `aq` baru maju setelah
frame selesai dan lolos. Bukan PUBACK MQTT, bukan bukti operator melihat browser.
Unknown sid, q yang tidak pernah terkirim, regressi, dan retained receipt ditolak.
`telemetry/doa` dan `telemetry/angular` tidak dibatasi receipt atau keberadaan subscriber
Ground. Edge publish saat source/profile dan koneksi MQTT ke broker memenuhi syarat; receipt
adalah observasi aplikasi, bukan PUBACK. Koneksi Edge ke broker hanya membuktikan koneksi
Edge ke broker, bukan Ground terhubung atau menerima pesan QoS 0 non-retained. Clean Start
dan tidak adanya offline replay berarti telemetry dapat terlewat saat Ground offline; MQTT
Message Expiry yang dikonfigurasi tetap berlaku.
Validitas/freshness source, profile, backlog, budget, dan stabilisasi resume tetap menjadi gate lokal.

`telemetry/diagnostic/doa` dikirim tiap 3 s selama XML tersedia, terlepas dari validitas DoA
normal. `q` bertambah tiap publish; timestamp sumber/observasi tetap menunjuk pembacaan file
yang sama. Ground menyimpan sampel sebagai `UNVERIFIED`, tidak masuk ke `dq`, DoA valid,
angular, atau otorisasi command.

Kedua topic diagnostic memakai Control token bucket bersama health/DoA normal (default
850 B/s), sehingga chunk QoS 0 dapat expired saat backlog. Broker ACL harus mengizinkan
Control Edge publish dan Control Ground subscribe ke kedua topic. Diagnostic angular tidak
memenuhi `aq`.

## Diagnostic angular candidate

Topic `telemetry/diagnostic/angular` membawa frame RDF2 terakhir dari 360 sampel `DOA_value.html`
yang berhasil diparse, hanya saat jalur angular Bulk normal tidak dapat mengirimnya. Jika gate
validitas source strict gagal, Edge menjadwalkan setiap 6 s; jika source valid tetapi Bulk
terblokir atau menunggu stabilisasi resume, setiap 30 s. Tidak ada duplikasi saat Bulk normal
tersedia; profile `control` menahan array. Topic memakai Control QoS 0, non-retained, expiry 3 s,
dan tidak menunggu receipt Ground. Timestamp sumber dipertahankan; `q` adalah sequence publish
diagnostic, bukan sequence file sumber.

Bits `flags` menyatakan bukti yang tersedia saat Edge mengirim:

| Bit | Bukti |
|---:|---|
| 0 | record berhasil diparse |
| 1 | clock dipercaya dan source masih fresh |
| 2 | DAQ sehat |
| 3 | konvensi sudut diverifikasi lokal |
| 4 | atribusi konfigurasi cocok |

Ground menyimpan frame lengkap terpisah dari `telemetry/angular`, `telemetry/doa`, dan receipt.
Nilainya selalu `trust=UNVERIFIED`; timestamp stale, health/processing, revision, dan flags
menjadi `validation_reasons`/`stale`, bukan alasan membuang sampel. Baca melalui
`GET /api/v2/angular/diagnostic/latest` (mengembalikan `null` sebelum frame lengkap diterima).
Frame diagnostic tidak memenuhi receipt `aq` atau detection LIVE.

Response memuat 360 `values`, `encoding`, `frequency_hz`, `raw_doa_deg`,
`source_timestamp_ms`, `source_age_ms`, `received_age_ms`, `flags`,
`validation_reasons`, dan `stale`.

Encoding mengikuti profile; bila Q16 tidak dapat merepresentasikan rentang tanpa clipping,
Edge memakai U8. Keduanya terkuantisasi, bukan salinan lossless CSV. Gate LIVE pada
`telemetry/angular` tetap mensyaratkan frame flags lengkap dan verifikasi Ground.

## Command envelope

```json
{"v":2,"id":"g01-00001234","sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","issued_ms":1790668800123,"expires_ms":1790668815123,"base_rev":7,"op":"config.patch","changes":{"center_frequency_hz":433920000,"vfo0_frequency_hz":433920000}}
```

Semua nilai contoh adalah fixture, jangan dikirim mentah. Ground membuat envelope dari
identity/revision terbaru. TTL default15s, maksimum30s. Unknown fields ditolak.
ID sama+payload sama mengembalikan hasil/progress tersimpan, bukan eksekusi ulang.
ID sama+payload berbeda ditolak. Topic harus sesuai `op` dan command tidak retained.

ACK berisi `v,sid,id,status,t,rev,result`. APPLIED memerlukan bukti sesuai aksi.
PERSISTED_UNVERIFIED dan OUTCOME_UNKNOWN bukan sinonim gagal atau berhasil.
Hasil tersedia lewat jurnal setelah reconnect; tidak bergantung broker offline history.

## API untuk dashboard lama

Receiver Ubuntu: `http://127.0.0.1:8791`.

- `GET /api/v2/snapshot`: node/health/metadata yang telah digate.
  `diagnostic_doa` berisi sampel XML terpisah dengan timestamp sumber/observasi, alasan validasi dan status stale; tidak dihitung sebagai receipt atau DoA valid.
- `GET /api/v2/angular/latest`: array360, metadata dan `stale`.
- `GET /api/v2/config`: safe report/proof.
- `GET /api/v2/operations/latest`: public results, challenge disensor.
- `GET /api/v2/operations/pending-shutdowns`: ringkasan publik `{"pending":true|false}` dari seluruh jurnal operasi shutdown yang belum gagal pasti; tidak dibatasi 20 record terbaru dan tidak memuat ID.
- `GET /api/v2/capabilities`: capability yang dilaporkan node.
- `POST /api/v2/login`: PIN lokal enam digit, field JSON `pin`, HttpOnly cookie+CSRF.
- `POST /api/v2/commands`: authenticated intent, tidak perlu membangun envelope di frontend. Ground API menerima `id` klien opsional agar dapat query operation yang sama bila respons execute hilang; bila tidak diberikan, server membuat ID.
- `POST /api/v2/operation/result`: private result, membutuhkan auth+CSRF.

Saat Ground mengubah sesi node, ia merekonsiliasi seluruh shutdown pending dari jurnal, bukan hanya window latest. Backend Ground hanya menyimpan/mengirim command dengan ID klien yang sudah tervalidasi; browser tidak memilih hasil operasi lain untuk menggantikan ID tersebut.

Gunakan backend Ground lama untuk mengakses API pendamping secara server-to-server lokal.
Browser frontend pada origin berbeda tidak diberi CORS wildcard. Jangan expose port8791 ke
seluruh LAN hanya agar browser lintas-origin dapat memanggilnya.
