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

## Reconnect dan keepalive

Kontrak wire tidak berubah; yang berubah hanya kapan client membuka sesi baru.

- Backoff reconnect mulai 1 s dan berlipat sampai `mqtt.reconnect_max` (default 30 s). Sesi yang
  bertahan minimal 20 s setelah CONNACK mengembalikan backoff ke 1 s; sesi yang putus lebih cepat
  atau gagal tersambung terus menaikkan backoff (tidak ada reconnect tiap detik).
- PINGREQ dikirim bila selama `mqtt.keepalive` detik tidak ada yang dikirim atau tidak ada yang
  diterima dari broker, termasuk saat telemetry QoS 0 terus terkirim. Tanpa PINGRESP dalam
  `keepalive` detik berikutnya, sesi direset (`PING_TIMEOUT`), kecuali TCP masih maju: data client
  yang belum di-ACK atau PINGREQ yang belum terkirim masih ada, dan peer mengirim ACK TCP dalam
  `keepalive` detik terakhir. Dalam keadaan itu (link lambat yang masih bekerja) client menunggu
  sampai 4 kali `keepalive`. Link mati tidak mengirim ACK TCP dan tetap direset seperti biasa.
- Edge memanggil `kick()` pada client Control dan Bulk utama saat interface PPP kembali `UP` atau
  ping peer kembali dibalas: client yang belum tersambung langsung reconnect dan backoff kembali ke
  awal.
- Setiap reconnect tetap Clean Start tanpa replay offline; consumer harus memakai `sid`, `boot`,
  `q`, dan freshness, bukan asumsi kontinuitas sesi.

Detail operasional ada di [MQTT_GROUND.md](MQTT_GROUND.md#reconnect-keepalive-dan-pemulihan-ppp).

## Broker cadangan

Edge dapat memakai satu broker cadangan opsional (`mqtt.backup`, panel `Data > Cadangan`) sebagai
mirror LAN/online yang independen dari PPP/T900. Kontrak wire tidak berubah: prefix, topic,
payload, QoS, retained, dan expiry identik dengan broker utama, dengan client ID
`{client_id}-backup`, transport/TLS sendiri, dan budget `mqtt.backup.budget_bytes_s` yang terpisah
dari budget T900. Data tetap dikirim ke cadangan saat broker utama tidak READY.

Aturan khusus per broker:

- `availability`/`capabilities` dikirim saat koneksi masing-masing broker siap; Last Will berlaku
  per koneksi.
- Angular normal ke cadangan dijadwalkan sendiri (interval profile dan budget cadangan) dan tidak
  dipause oleh alasan khusus T900 (`BULK_NOT_READY`, `CONTROL_BACKLOG`, `WAIT_STABLE`).
- Bila `mqtt.backup.commands: true`, Edge subscribe `cmd/*` yang sama di broker cadangan dan
  memproses command itu seperti command Ground (actor `ground-controller`). ACK command dari
  cadangan hanya dikirim ke cadangan; ACK command dari broker utama atau panel lokal dikirim ke semua
  broker READY.
- `config.get` dari broker utama dijawab dengan atau tanpa TLS. Dari broker cadangan, `config.get`
  memerlukan TLS atau `mqtt.backup.settings_report_plaintext: true`. `settings/reported` hanya
  dikirim ke broker asal request.

Detail per topic ada di [MQTT_TOPIC_SUMMARY.md](MQTT_TOPIC_SUMMARY.md#broker-cadangan); konfigurasi,
ACL, dan routing di [MQTT_GROUND.md](MQTT_GROUND.md#broker-cadangan-mirror-lanonline).

## DoA compact

```json
{"v":2,"sid":"7a8b9c0d","q":1245,"t":1790668800123,"f":433920000,"a":137.4,"c":8.27,"p":-54.2,"rev":7,"ok":1,"clk":1,"age":280,"reason":[]}
```

`t`: timestamp native ms; `q`: sequence sampel adapter, bukan frame DAQ.
`a`: bila `ok=1`, sudut menurut konvensi config; bila `ok=0`, nilai raw source dan hanya
berarti RAW dengan `angle_reference="RAW"`. `c`: PAPR/native dB, bukan probabilitas;
`p`: power native signed; `f`: VFO Hz; `rev`: safe config revision-at-sample, atau `null` bila
sampel belum teratribusi ke settings. `clk`: 1 bila jam Edge tersinkron, 0 bila tidak. `age`: umur
sampel di source (ms) saat dikirim. `reason`: daftar kode alasan sampel belum LIVE; kosong bila
`ok=1`.

Edge mengirim **setiap sampel baru** dari `DOA_value.html`, valid atau tidak, dengan label. Penerima
yang menentukan cara menampilkannya; label tidak boleh dipakai untuk membuang data diam-diam.
`ok=1` berarti kandidat LIVE terverifikasi. Sampel lain dikirim dengan `ok=0`:

```json
{"v":2,"sid":"7a8b9c0d","q":1246,"t":1790668800123,"f":433920000,"a":10.0,"c":8.27,"p":-54.2,"rev":7,"ok":0,"clk":0,"age":1840,"reason":["CLOCK_UNTRUSTED"],"trust":"UNVERIFIED","angle_reference":"RAW"}
```

Bila satu-satunya alasan adalah authority dan/atau konvensi sudut yang belum disetujui
(`SOURCE_UNVERIFIED`, `ANGLE_UNVERIFIED`), payload juga membawa `validation_reasons` dengan isi yang
sama. Itu nama field lama untuk kasus ini, dipertahankan supaya penerima yang ditulis sebelum
`reason` ada tetap bisa membacanya:

```json
{"v":2,"sid":"7a8b9c0d","q":1247,"t":1790668800123,"f":433920000,"a":10.0,"c":8.27,"p":-54.2,"rev":7,"ok":0,"clk":1,"age":280,"reason":["SOURCE_UNVERIFIED","ANGLE_UNVERIFIED"],"trust":"UNVERIFIED","angle_reference":"RAW","validation_reasons":["SOURCE_UNVERIFIED","ANGLE_UNVERIFIED"]}
```

Sampel yang sama tidak dikirim ulang: bila source tidak punya sampel baru (file kosong, basi, atau
tidak berubah), `telemetry/doa` diam dan alasannya ada di `health.why`. Nilai contoh sintetis. Radio
tidak menambahkan SNR yang belum terbukti sumbernya.

### Kode alasan DoA (`reason`, `health.why`)

| Kode | Arti |
|---|---|
| `SETUP_REQUIRED` | Folder `_share` KrakenSDR belum dipilih. |
| `EMPTY_CSV` | `DOA_value.html` terbaca tetapi tidak berisi baris VFO. |
| `DOA_FILE_NOT_UPDATING` | `DOA_value.html` tidak ditulis ulang lebih lama dari `freshness.doa_ms` (default 2,5 s), apa pun isinya. |
| `NO_RECORD` | Belum pernah ada sampel DoA yang terbaca sejak Edge start. |
| `NO_FRESH_DOA` | Sampel terakhir lebih tua dari `freshness.doa_ms`, atau timestamp-nya di masa depan. |
| `STATUS_STALE_OR_UNAVAILABLE` | `status.json` tidak berubah dalam `freshness.source_status_ms` atau tidak terbaca. |
| `DAQ_NOT_HEALTHY` | DAQ tidak sehat (`daq_ok`, salah satu sync, atau frame tidak maju). |
| `CLOCK_UNTRUSTED` | Jam Edge tidak tersinkron NTP. |
| `SOURCE_UNVERIFIED`, `ANGLE_UNVERIFIED` | Authority source dan/atau konvensi sudut belum disetujui (`sudo rdf-node setup --verify-source`). |
| `CONFIG_UNAVAILABLE` | `settings.json` tidak terbaca, bukan JSON object, atau tidak ada satu pun field aman. Satu field aman yang rusak tidak lagi memicu kode ini: field itu dikeluarkan dari subset aman dan namanya muncul di `config.invalid_settings` snapshot. |
| `CONFIG_ATTRIBUTION_UNVERIFIED` | Sampel tidak bisa dipastikan dibuat dengan settings yang sekarang (mis. sesaat setelah settings berubah). |
| `CONTROL_IN_PROGRESS` | Sebuah perintah SDR sedang berjalan. |
| kode parse lain | Kesalahan baca `DOA_value.html`, mis. `CSV_FIELD_COUNT`, `OUTPUT_VFO_MISMATCH`, `VFO_RECORD_AMBIGUOUS_OR_MISSING`, `FILE_TOO_LARGE_OR_NOT_REGULAR`, `FileNotFoundError:DOA_UNAVAILABLE`. |

Membedakan "tidak ada sinyal" dari "pengolahan macet" (status DAQ segar, tanpa
`STATUS_STALE_OR_UNAVAILABLE`):

| `health.why` berisi | Arti yang paling mungkin |
|---|---|
| `EMPTY_CSV` tanpa `DOA_FILE_NOT_UPDATING` | KrakenSDR masih menulis file, tetapi tidak ada VFO yang menghasilkan DoA, biasanya karena tidak ada sinyal di atas squelch. Pastikan dengan uji sinyal di frekuensi VFO. |
| `DOA_FILE_NOT_UPDATING` | DAQ berjalan, tetapi file hasil DoA berhenti ditulis: pengolahan DoA KrakenSDR macet atau dimatikan (`en_doa`). |
| `STATUS_STALE_OR_UNAVAILABLE` | KrakenSDR (DAQ) sendiri tidak berjalan atau tidak menulis status. |

Edge hanya melaporkan bukti; ia tidak menyimpulkan "tidak ada sinyal". Penerima menampilkan kode
dan arti di atas.

## Health

```json
{"v":2,"sid":"7a8b9c0d","q":86,"t":1790668800123,"run":1,"daq":1,"drop":12,"age":280,"temp":61.4,"clk":1,"rev":7,"why":[]}
```

Run: 0 stopped, 1 running, 2 starting, 3 stopping, 4 error, 255 unknown.
DAQ: 0 degraded, 1 healthy, 2 unknown. `age` ialah umur sampel DoA terakhir yang berhasil dibaca dari
source, bukan latency E2E. Selama `DOA_value.html` kosong atau rusak, sampel terakhir tetap dipakai,
jadi `age` terus naik. `why` menjelaskan sebabnya (kode di tabel atas; kosong bila DoA LIVE).
Clock: 0 untrusted, 1 synced. Field unavailable dapat null. Health q tetap maju saat SDR stop.

`telemetry/health/detail` (tiap 10 s) menambah field aditif status link PPP: `ppp` (`UP`/`DOWN`),
`peer` (hasil ping peer), `ppp_svc` (`ActiveState` unit `t900-ppp.service`), dan `ppp_restarts`
(`NRestarts` systemd), serta `perr`: daftar topic yang gagal disusun pada putaran publish terakhir
(kosong bila semua berhasil; satu topic yang gagal tidak menghentikan topic lain) dan `crej`: jumlah
command masuk yang dibuang sebelum diproses (lihat [Command envelope](#command-envelope)). Consumer harus
menerima field yang tidak ada dari Edge lama. Lihat
[MQTT_TOPIC_SUMMARY.md](MQTT_TOPIC_SUMMARY.md#telemetryhealthdetail).

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
Ground READY dan session `sid`/`boot` terkini, tetapi tidak memerlukan health fresh.

Mutation selain read-only `config.get` tetap melewati pemeriksaan session, boot, expiry, clock,
revision, health/evidence, dan target helper fixed bila relevan. Ground tidak memerlukan remote
grant atau approval root per aksi. Field `remote_*` melaporkan kesiapan target, bukan otorisasi
publisher; broker ACL dan credential membatasi siapa yang boleh publish command. Edge tidak
mengikat publisher MQTT ke sesi HTTP Ground.

Edge merespons lewat `settings/reported` dengan QoS 1, non-retained, expiry 30 s:

```json
{"v":2,"sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","id":"g01-00001234","rev":7,"t":1790668800123,"settings_json":"{\"center_freq\":433.92,\"uniform_gain\":20.7}"}
```

Field wajib tepat `v,sid,boot,id,rev,t,settings_json`. `settings_json` menyimpan teks UTF-8
persis dari file native yang berhasil dibaca, bukan normalisasi atau safe-field serialization.
Broker utama dijawab dengan atau tanpa TLS, karena `mqtt.tls: false` berarti operator memilih link
tepercaya yang juga membawa telemetry dan `config.patch`. Broker cadangan tanpa TLS ditolak
`TLS_REQUIRED_FOR_SETTINGS_REPORT` kecuali `mqtt.backup.settings_report_plaintext: true`. Edge
menolak source settings unavailable/stale atau compact envelope di atas 8.192 byte dengan error
spesifik. Report hanya dikirim ke broker yang
meminta (utama atau cadangan), tidak di-mirror. Edge tidak memotong atau memecah report. `ack/config` memuat
status/error command; settings datang pada PUBLISH terpisah.

Ground menerima hanya PUBLISH non-retained yang cocok dengan pending `id`, session `sid`, `boot`,
dan timestamp fresh. Ground mem-parse teks sebagai JSON object, menyimpan teks mentah, lalu
menurunkan subset safe untuk form mutation. Safe settings hanya diberikan bila report `rev`
cocok dengan `state.cfg`; teks mentah tetap dapat ditampilkan.

### Error di ACK

ACK `REJECTED`, `FAILED`, dan `OUTCOME_UNKNOWN` karena error membawa tiga field di `result`:

```json
{"v":2,"sid":"7a8b9c0d","id":"g01-00001235","status":"REJECTED","t":1790668800456,"rev":7,
 "result":{"error":"HELPER_UNAVAILABLE",
           "message":"Helper kontrol (rdf-control-helper) tidak berjalan atau socket-nya tidak ada; perintah tidak dijalankan.",
           "detail":"FileNotFoundError (No such file or directory): /run/rdf-node-control/control.sock"}}
```

| Field | Isi |
|---|---|
| `error` | Kode stabil `[A-Z][A-Z0-9_]*` untuk mesin. Field ini sudah ada sejak 1.0.0. |
| `message` | Penjelasan bahasa Indonesia untuk operator. Selalu ada bersama `error`; kode yang belum punya teks khusus mendapat teks umum dengan kodenya. |
| `detail` | Opsional, maks. 200 karakter: penyebab teknis (jenis exception dan pesannya, misalnya path socket). Tidak pernah memuat isi credential. |

Edge tidak lagi mengirim nama tipe exception mentah (`OSERROR`, `TIMEOUTERROR`,
`JOURNAL_OR_REQUEST_ERROR`). Penggantinya:

| Dulu | Sekarang | Stage | Arti |
|---|---|---|---|
| `OSERROR` saat socket helper tidak ada | `HELPER_UNAVAILABLE` | `REJECTED` sebelum jurnal, atau `FAILED` bila helper hilang setelah perintah diterima | Socket tidak bisa dibuka; tidak ada yang dikirim, jadi tidak ada yang dijalankan. |
| `PERMISSIONERROR` | `HELPER_ACCESS_DENIED` | `REJECTED` atau `FAILED` | Socket ada, tetapi user agent tidak boleh membukanya. |
| `TIMEOUTERROR` | `HELPER_TIMEOUT` | `OUTCOME_UNKNOWN` | Request sudah terkirim, jawaban tidak datang. |
| `OSERROR` setelah request terkirim | `HELPER_CONNECTION_LOST` | `OUTCOME_UNKNOWN` | Socket putus setelah request terkirim. |
| `OSERROR`, `PERMISSIONERROR`, dan sejenisnya dari dalam helper | `HELPER_INTERNAL_ERROR` | `FAILED` | Error tak terduga di helper; `detail` memuat penyebabnya. |
| `JOURNAL_OR_REQUEST_ERROR` | `INVALID_COMMAND_FIELD`, `JOURNAL_UNAVAILABLE`, atau `INTERNAL_ERROR` | `REJECTED` | Tipe/nilai field salah, journal SQLite gagal, atau error lain di agent. |

Mutation yang butuh helper (`config.patch`, `processing.set`, `service.restart`, `ppp.restart`,
`system.reboot.*`, `system.shutdown.*`) ditolak `REJECTED HELPER_UNAVAILABLE` sebelum masuk jurnal
bila helper tidak menjawab status. `stream.set`, `config.get`, dan `operation.get` tidak butuh
helper. Teks lengkap per kode ada di `docs/CONTROL.md#pesan-error-di-ack`.

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
terkini dan `state.cfg` untuk `base_rev`. TTL default 15 s, maksimum 30 s.

Field yang tidak dikenal Edge diabaikan: tidak disimpan di jurnal, tidak berpengaruh apa pun, dan
namanya (maks. 16, masing-masing maks. 64 karakter) dikembalikan di `result.ignored` pada ACK
pertama (`ACCEPTED`, `APPLIED` untuk read op, atau `REJECTED`). Sebelumnya command seperti itu ditolak
`UNKNOWN_COMMAND_FIELD`. Pengaman tidak ikut longgar: `sid`/`boot`, `issued_ms`/`expires_ms`,
`base_rev` untuk mutation, field wajib tiap op, dan batas nilai tetap wajib, jadi field wajib yang
salah eja tetap membuat command ditolak (misalnya `base_revision` diabaikan, lalu `base_rev` yang
hilang ditolak `CONFIG_REVISION_CONFLICT`). ID sama dengan payload sama, setelah field asing dibuang,
mengembalikan hasil atau progress yang tersimpan, bukan eksekusi ulang. ID sama dengan payload
berbeda ditolak. Topic harus sesuai `op`, dan command tidak boleh retained.

Command di atas 4.096 byte ditolak `COMMAND_TOO_LARGE`. Sampai 64 KiB, Edge masih mem-parse-nya
untuk membaca `id`, lalu mengirim ACK `REJECTED`; di atas itu, atau bila payload bukan JSON
(`COMMAND_NOT_JSON`), ID tidak terbaca sehingga tidak ada ACK. Antrean inbound yang penuh membuang
command dengan `COMMAND_QUEUE_FULL`. Semua penolakan tanpa ACK tetap terlihat: masuk `remote_rejects`
di snapshot panel, dihitung di `link.command_drops`, dan jumlahnya ada di `telemetry/health/detail`
sebagai `crej`.

ACK berisi `v,sid,id,status,t,rev,result`. APPLIED memerlukan bukti sesuai aksi.
PERSISTED_UNVERIFIED dan OUTCOME_UNKNOWN bukan sinonim gagal atau berhasil.
Hasil tersedia lewat jurnal setelah reconnect; tidak bergantung broker offline history.

Untuk `ppp.restart`, operasi yang diterima mula-mula mengirim `ACCEPTED`, lalu
`PPP_RESTART_REQUESTED` dengan `accepted_by_systemd: true`, timestamp request, dan nama unit tetap
`t900-ppp.service`. Edge kemudian memantau link sampai 120 s dan mengirim ACK akhir:
`APPLIED` dengan `proof: {ppp:"UP", peer:"REPLY", mqtt:"READY"|"DISABLED", cycled:true}` dan
`elapsed_ms` setelah PPP berganti siklus, peer membalas ping, dan MQTT utama tersambung lewat
link baru; atau `OUTCOME_UNKNOWN` dengan `reason` `T900_USB_MISSING`, `MQTT_NOT_RECOVERED`,
`PPP_RECOVERY_TIMEOUT`, atau `PPP_CYCLE_NOT_OBSERVED`. Bila link pulih setelah ACK timeout itu,
hanya snapshot lokal Edge yang berubah (`link.ppp_recovery.late: true`); tidak ada ACK kedua.
Restart PPP memakai jalur sendiri di Edge: tidak ditolak `BUSY` oleh mutasi SDR, tetapi ditolak
`PPP_RESTART_IN_PROGRESS` selama restart lain berjalan/dipantau dan `PPP_RESTART_TOO_SOON` dalam
15 s setelah permintaan sebelumnya. Helper menerima unit `active`, `failed`, dan
`activating/auto-restart` dari Ground; `inactive` hanya dari panel Edge lokal.

Receiver Ground bawaan tetap memakai rekonsiliasinya sendiri: ia melaporkan `APPLIED` setelah
health yang lebih baru dari request diterima pada sesi node yang sama, dan tidak mengganti record
dengan ACK akhir pemantau Edge. Status Ground ini bukan bukti PPP atau link MQTT pulih. Ground
tidak mengulang hasil yang tetap unknown. Request baru memerlukan health fresh yang lebih baru dari
setiap request unresolved serta konfirmasi operator; record lama tetap unknown. Konfirmasi itu
adalah intent lokal Ground, bukan field envelope MQTT. Request tidak dapat tiba setelah seluruh
jalur Control putus; restart lokal dari panel Edge tetap tersedia. Lihat
[CONTROL.md](CONTROL.md#restart-layanan-ppp-t900) dan [T900_PPP.md](T900_PPP.md).

Record operasi publik di hasil `operation.get` memuat field aditif `origin`
(`local`/`ground`/`backup`), `summary` (`{kind, changes}`), dan `reason`.

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
