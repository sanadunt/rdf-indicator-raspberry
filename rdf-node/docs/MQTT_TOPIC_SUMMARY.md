# RDF Node MQTT topic guide

Panduan implementasi MQTT v2 Edge/Ground ini menjelaskan tujuan tiap topic, arah, kanal, QoS, retained, expiry, payload, dan contoh pemakaian. Payload sintetis; bukan capture Raspberry atau broker produksi.

## Namespace dan identitas

Prefix produksi adalah `sdr/v2/{node_id}`. Nilai `node_id` default di `config/example.yaml` adalah `uav-01`, sehingga contoh topic lengkapnya `sdr/v2/uav-01/...`. Prefix demo internal adalah `sdr/demo/v2/{node_id}`.

Semua topic di tabel berikut adalah suffix setelah prefix tersebut. Topic bersifat case-sensitive.

| Field | Makna |
|---|---|
| `v` | Versi protokol, saat ini `2`. |
| `sid` | Alias sesi proses, 8 digit hex; berubah ketika proses Agent dimulai ulang. |
| `boot` | ID boot OS. |
| `instance` | UUID instance Agent. Kombinasi `sid`, `boot`, dan `instance` mengaitkan data dengan satu sesi. |
| `t` | Unix timestamp dalam milidetik, kecuali disebut lain. |
| `q` | Urutan sampel untuk jenis datanya. Bukan satu counter global dan bukan nomor frame DAQ. |
| `rev` | Revision konfigurasi aman yang dikorelasikan dengan data; dapat `null` bila belum diketahui. |

Semua payload telemetry JSON, termasuk `telemetry/angular` dan `telemetry/diagnostic/angular`.
Setiap pesan Angular membawa metadata dan tepat 360 angka `values` dalam satu PUBLISH, tanpa
wrapper chunk atau Base64. MQTT tidak mengirim raw IQ, raw CSV, koordinat GPS, kredensial broker, atau PIN admin.

## Koneksi dan pemisahan kanal

Edge membuka dua MQTT client:

| Kanal | Client ID | Hak data |
|---|---|---|
| Control | `{client_id}-control` | Publish JSON telemetry (termasuk `telemetry/doa` UNVERIFIED dan topik diagnostik), state, capabilities, settings response, availability, ACK; subscribe command. |
| Bulk | `{client_id}-bulk` | Publish satu object JSON untuk `telemetry/angular` normal, termasuk varian UNVERIFIED dengan flags parsial. Tidak subscribe command dan tidak memiliki hak command. |
| Ground | `{node_id}-ground` | Subscribe data operasional/ACK dan settings response yang diminta; publish command. |

Kredensial Control dan Bulk terpisah bila digunakan; tanpa kredensial, client memakai anonymous
CONNECT. Edge tidak mengautentikasi identitas publisher atau mengikat payload ke principal
Ground. ACL broker dan isolasi jaringan membatasi publisher yang dapat menjangkau topic command.
Jika broker mengizinkan publish anonymous ke topic tersebut, publisher yang menjangkaunya dapat
memakai operasi remote yang aktif. Plaintext mengekspos credential dan payload; gunakan hanya
pada link privat terisolasi/tepercaya. MQTT memakai Clean Start dan Session Expiry 0; reconnect
membuat sesi baru tanpa replay offline. QoS 2 dan persistent broker session tidak didukung.

## Daftar lengkap topic

QoS dan expiry berikut adalah setting publish dari implementasi saat ini. `Retained` berarti broker menyimpan nilai terakhir untuk subscriber baru.

| Topic suffix | Arah | Kanal | QoS | Retained | Interval / expiry | Isi |
|---|---|---|---:|---|---|---|
| `telemetry/doa` | Edge -> Ground | Control | 0 | Tidak | Maks. 1/s; valid atau UNVERIFIED bila hanya approval lokal yang absen; expiry 3 s | DoA, frekuensi, confidence, power, revision; `ok=0` menandai sudut raw dan alasan. |
| `telemetry/diagnostic/doa` | Edge -> Ground | Control | 0 | Tidak | Setiap 3 s selama `doa.xml` tersedia; expiry 3 s | Sudut raw `doa.xml`, timestamp sumber/observasi, frekuensi MHz, status `UNVERIFIED` dan validation reasons. Diulang meski sampel tidak berubah, terlepas dari gate DoA normal. |
| `telemetry/diagnostic/angular` | Edge -> Ground | Control | 0 | Tidak | Minimum 6 s saat gate integritas source gagal; minimum 30 s jika source eligible tetapi Bulk terblokir/menunggu stabilisasi; ukuran JSON dan headroom Control dapat memperpanjang interval; tidak diduplikasi ketika Angular normal mengalir; profile `control` menahan array. Expiry 3 s | Satu object JSON dengan 360 values dan source timestamp; Ground menyimpan terpisah sebagai `UNVERIFIED`, tanpa mengubah telemetry normal. |
| `telemetry/health` | Edge -> Ground | Control | 0 | Tidak | Tiap 1 s; expiry 5 s | Status run, DAQ, clock, umur sumber, temperatur, dropped frames. |
| `telemetry/health/detail` | Edge -> Ground | Control | 0 | Tidak | Tiap 10 s; expiry 15 s | Detail host, USB, sync DAQ, trafik, parse dan abort counter. |
| `telemetry/angular` | Edge -> Ground | Bulk | 0 | Tidak | Profile-dependent; expiry 3 s | Frame 360 sampel; flags `63` untuk LIVE atau trust-only partial untuk UNVERIFIED. |
| `state` | Edge -> Ground | Control | 1 | Ya | Saat state berubah dan paling lambat tiap 60 s | Status run/DAQ, profile, clock dan identitas boot. Jangan dipakai sebagai heartbeat. |
| `capabilities` | Edge -> Ground | Control | 1 | Ya | Saat generation koneksi Control berubah | Versi, identitas, codec, profile dan capability yang diizinkan. |
| `settings/reported` | Edge -> Ground | Control | 1 | Tidak | Hanya setelah `cmd/config/get`; expiry 30 s | `v,sid,boot,id,rev,t,settings_json`; teks native UTF-8 persis; TLS wajib; compact envelope maksimum 8 KiB. |
| `availability` | Edge -> Ground | Control | 1 | Ya | Online saat koneksi siap; Last Will saat koneksi hilang | Ketersediaan Control, bukan bukti DAQ sehat. |
| `ack/config` | Edge -> Ground | Control | 1 | Tidak | Saat progress/hasil command `config.*`; expiry 30 s | ACK command konfigurasi. |
| `ack/operation` | Edge -> Ground | Control | 1 | Tidak | Saat progress/hasil command selain `config.*`; expiry 30 s | ACK command operation. |
| `cmd/config/get` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Meminta respons native settings; perlu MQTT READY dan session aktif, bukan health fresh. |
| `cmd/config/patch` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Safe-field patch memakai target settings fixed/single-writer; tanpa approval root per command. |
| `cmd/processing/set` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Intent `RUNNING` atau `STOPPED` untuk target SDR fixed yang sudah diaudit; tanpa approval root per command. |
| `cmd/service/restart` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Restart target SDR audited; tanpa approval root per command. Bukan restart PPP atau bridge. |
| `cmd/service/ppp/restart` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Restart fixed `t900-ppp.service`; tanpa grant/approval root, tetapi helper memeriksa loaded/active/no-job setiap request. |
| `cmd/system/reboot/prepare` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Meminta challenge reboot sekali pakai; tidak memerlukan remote grant/approval root. |
| `cmd/system/reboot/execute` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Eksekusi reboot dengan `prepare_id` dan `challenge` yang masih valid. |
| `cmd/system/shutdown/prepare` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Meminta challenge shutdown sekali pakai; tidak memerlukan approval root. |
| `cmd/system/shutdown/execute` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Menjadwalkan poweroff dengan `prepare_id` dan challenge yang masih valid. |
| `cmd/operation/get` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Query hasil operation berdasarkan ID. |
| `cmd/stream/set` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Ganti profile telemetry. |

### Peran setiap topic di Ground

| Topic | Yang dilakukan Ground |
|---|---|
| `telemetry/doa` | `ok=1`: validasi session, sequence, timestamp, nilai sampel dan health fresh sebelum detection; `rev` tetap metadata, bukan equality gate. `ok=0`: pertahankan gate raw diagnostic UNVERIFIED. |
| `telemetry/diagnostic/doa` | Tampilkan XML terpisah sebagai diagnostik UNVERIFIED dengan timestamp dan reasons; jangan gabungkan ke detection atau keputusan kontrol. |
| `telemetry/diagnostic/angular` | Simpan frame lengkap terbaru terpisah sebagai UNVERIFIED; expose lewat `GET /api/v2/angular/diagnostic/latest`; jangan gabungkan ke `telemetry/angular`, DoA, detection atau keputusan kontrol. |
| `telemetry/health` | Gunakan sebagai sumber utama freshness dan status DAQ; pisahkan MQTT tersambung dari `daq=1`. |
| `telemetry/health/detail` | Pakai untuk diagnosis host, USB, sync, trafik dan error; jangan jadikan pengganti gate health. |
| `telemetry/angular` | Parse satu object JSON per message, lalu pastikan ada tepat 360 values. `flags=63` memenuhi gate bukti LIVE; `revision` tetap metadata dan tidak dibandingkan dengan `state.cfg`. |
| `state` | Bootstrap identitas boot/sesi dan profile; saat `sid` berubah, buang cache telemetry sesi lama. Retained state bukan heartbeat. |
| `capabilities` | Tampilkan operasi sesuai capability node; tetap tegakkan ACL dan policy Ground. |
| `settings/reported` | Terima hanya respons non-retained untuk `config.get` yang cocok dengan pending `id`, session `sid`, `boot` dan timestamp fresh. Simpan teks JSON asli; turunkan safe settings hanya untuk form allowlist. |
| `availability` | Tampilkan status koneksi Control sebagai petunjuk saja; cek health untuk mengetahui kondisi DAQ. |
| `ack/config` | Cocokkan `id` dengan journal command konfigurasi; tampilkan progress dan hasil tanpa menganggap ACK broker sebagai hasil operasi. |
| `ack/operation` | Cocokkan `id` untuk lifecycle, stream, reboot dan shutdown; jangan anggap `SHUTDOWN_SCHEDULED` sebagai host telah mati. |
| `cmd/config/get` | Minta teks settings native; `ack/config` melaporkan status command, sedangkan respons terpisah tiba di `settings/reported`. |
| `cmd/config/patch` | Kirim hanya field allowlist dengan `base_rev` terkini; bedakan `APPLIED` dari `PERSISTED_UNVERIFIED`. |
| `cmd/processing/set` | Minta start/stop SDR stack hanya jika capability tersedia; verifikasi hasil dari ACK dan telemetry baru. |
| `cmd/service/restart` | Minta restart SDR stack yang di-approve; tunggu ACK final dengan proof operasi dan telemetry health fresh sebelum menyatakan pulih. |
| `cmd/service/ppp/restart` | Minta restart hanya dengan `ppp_restart` dan `remote_commands`; butuh health fresh. `PPP_RESTART_REQUESTED` hanya membuktikan systemd menerima request. Jangan retry otomatis; request baru perlu konfirmasi operator dan health yang lebih baru dari semua hasil belum pasti. |
| `cmd/system/reboot/prepare` | Minta challenge reboot setelah policy/approval terpenuhi; jangan log challenge. |
| `cmd/system/reboot/execute` | Kirim challenge satu kali sebelum kedaluwarsa; tunggu boot ID baru dan health fresh untuk rekonsiliasi. |
| `cmd/system/shutdown/prepare` | Minta challenge shutdown hanya saat capability tersedia; jangan log challenge. |
| `cmd/system/shutdown/execute` | Kirim challenge sekali pakai; `SHUTDOWN_SCHEDULED` bukan bukti poweroff dan hasil ambigu tetap `OUTCOME_UNKNOWN`. |
| `cmd/operation/get` | Query journal dengan `target_id` jika ACK hilang atau Ground reconnect. |
| `cmd/stream/set` | Ganti profile, lalu baca profile aktif dari `state` sebelum memperbarui ekspektasi rate Angular. |

Telemetri dan `settings/reported` tidak retained. `state`, `capabilities`, dan `availability` retained untuk bootstrap, tetapi nilainya tetap harus diuji freshness dan session-nya. Edge tidak meminta atau mengirim settings saat startup, reconnect, atau perubahan revision. ACK tidak retained dan punya expiry terbatas; Ground perlu menyimpan ID operation dan query ulang bila reconnect melewatkan ACK.

Subscription command memakai Retain Handling 2. Edge menolak command yang diterima dengan retain flag, termasuk retained command yang broker kirim sebagai publish live. Jangan publish command dengan `retain=true`.

## Estimasi bandwidth untuk 15 kbit/s

Asumsi di sini `15 Kbps` berarti 15.000 bit/s atau 1.875 byte/s, dan targetnya adalah trafik MQTT Edge rata-rata. Nilai default `config/example.yaml` memberi:

| Bucket | Budget default | Bit rate ekuivalen |
|---|---:|---:|
| Control | 850 byte/s | 6,8 kbit/s |
| Bulk | 350 byte/s | 2,8 kbit/s |
| Total dua bucket | 1.200 byte/s | 9,6 kbit/s |

Angka 9,6 kbit/s adalah budget biaya publish yang dihitung aplikasi, sekitar 64% dari 15 kbit/s dengan margin nominal 5,4 kbit/s. Ini mendukung trafik rata-rata pada konfigurasi default, bukan jaminan batas fisik.

Setiap client punya bucket terpisah dengan kredit awal 1.400 cost-byte untuk Control dan 650 cost-byte untuk Bulk. Satu publish bootstrap yang biayanya melebihi kapasitas tetap dapat dikirim saat bucket penuh, lalu membuat token berutang sampai terisi lagi. Karena itu burst singkat dapat melewati 15 kbit/s walaupun rata-ratanya dibatasi.

Untuk setiap outgoing PUBLISH, kode menghitung `len(MQTT packet) + 142`, lalu menambah 144 lagi untuk QoS 1. Nilai tetap itu adalah perkiraan overhead, bukan pengukuran byte aktual di T900. Kedua bucket juga tidak membatasi satu aggregate link: packet MQTT kontrol lain, trafik dari Ground, TLS/TCP/PPP, retransmission, dan proses lain dapat menambah trafik interface.

Kesimpulan: bila Raspberry memakai budget default dan tidak ada trafik PPP lain yang berarti, rata-rata MQTT Edge diperkirakan muat di bawah 15 kbit/s. Jangan klaim hard cap 15 kbit/s dari limiter ini saja. Untuk batas fisik yang tegas diperlukan shaper bersama pada interface/link dan pengukuran aktual. `telemetry/health/detail.tx` dan `.rx` menunjukkan rate interface dalam kbit/s untuk acceptance, tetapi belum ada pengukuran T900 nyata pada penilaian ini.

Jika perangkat memakai nilai MQTT budget berbeda dari `config/example.yaml`, hitung ulang dari konfigurasi aktual. Untuk limit agregat yang ketat, sisakan margin untuk overhead dan trafik non-MQTT.


## Ukuran payload dan laju aktual

Angka berikut diukur dari serializer JSON compact (`util.compact`) dan paket MQTT v5 yang dibuat
`publish_packet`, memakai prefix contoh `sdr/v2/uav-01`. `Payload` adalah byte JSON UTF-8.
`PUBLISH` mencakup topic, framing, dan expiry 3 s; tidak mencakup TCP/TLS/WebSocket/IP/PPP.
Ukuran fixture bukan ukuran maksimum; digit metadata dan nilai source dapat mengubahnya.

### Telemetri berkala Edge → Ground

| Topic / profile | Interval saat syarat lolos | Payload contoh | MQTT PUBLISH |
|---|---|---:|---:|
| `telemetry/doa` | Maks. 1/s; verified atau UNVERIFIED untuk trust-only blockers | 109 B verified | 147 B verified |
| `telemetry/diagnostic/doa` | Setiap 3 s selama XML tersedia; heartbeat mengulang sampel yang sama | 303 B | 352 B |
| `telemetry/health` | 1/s | 113 B | 154 B |
| `telemetry/health/detail` | 1/10 s | 179 B | 227 B |
| `telemetry/angular` (`balanced`) | Minimum 4 s; contoh JSON dipace sekitar 8.96 s pada Bulk 350 B/s | 2,946 B | 2,988 B |
| `telemetry/angular` (`graph_u8`) | Minimum 2 s; format JSON dan pace sama dengan `balanced` | 2,946 B | 2,988 B |
| `telemetry/angular` (`control`) | Tidak dipublish | — | — |
| `telemetry/diagnostic/angular` (`balanced`, `graph_u8`) | Minimum 6 s saat source invalid, 30 s saat valid-source Bulk blocked; contoh dipace sekitar 9.26 s atau 62.96 s pada Control 850 B/s | 2,946 B | 2,999 B |
| `telemetry/diagnostic/angular` (`control`) | Tidak dipublish | — | — |

Frame Angular JSON dikirim utuh dalam satu PUBLISH. Interval normal memakai
`max(interval_profile, (payload_bytes + topic_bytes + 160) / bulk_budget_bytes_s)`.
Diagnostic memakai headroom Control setelah reserve 510 B/s untuk source invalid atau 800 B/s
untuk source valid/trust-only. Dengan fixture ini, paket normal memerlukan sekitar 349.2
cost-B/s Bulk. Diagnostic memerlukan sekitar 339.2 cost-B/s saat source invalid dan 49.9
cost-B/s saat source valid tetapi Bulk terblokir. Angka biaya adalah `PUBLISH + 142 B`; QoS 1
menambah 144 B. Reserve membantu mencegah frame besar memakan budget telemetri Control, tetapi
limiter bukan shaper link dan trafik lain tetap dapat membuat QoS 0 expired.

### Topic event-driven dan command

Ukuran event memakai contoh JSON yang dicantumkan di bawah. State dapat dipublish saat berubah dan paling lambat tiap 60 s; capabilities/config/availability dikirim saat startup atau koneksi/event terkait, bukan tiap detik. ACK muncul saat progress/hasil command, dan satu command dapat memicu beberapa ACK.

| Topic / fixture | Kapan dikirim | Payload contoh | MQTT PUBLISH |
|---|---|---:|---:|
| `state` | Perubahan / maks. 60 s | 211 B | 238 B |
| `capabilities` | Generation koneksi Control berubah | 434 B | 468 B |
| `availability` online | Control siap | 56 B | 89 B |
| Last Will `availability` offline | Broker saat koneksi hilang; payload disertakan di CONNECT | 66 B | Bukan PUBLISH Edge |
| `ack/config` | Progress/hasil `config.*` | 226 B* | 263 B* |
| `ack/operation` | Progress/hasil operation lain | 226 B* | 266 B* |

`*` Kedua baris ACK memakai JSON fixture `ack/config` di bawah hanya untuk mengukur ukuran paket; payload ACK aktual berubah mengikuti status dan `result` operation.
`settings/reported` bersifat request-only dan tidak masuk rate periodik. Payload compact JSON maksimum 8.192 byte; ukuran paket PUBLISH mencakup overhead MQTT tambahan.

Ukuran command adalah fixture dengan envelope dan nilai umum yang sama seperti contoh `config.patch`; command execute reboot/shutdown memakai placeholder challenge 32 karakter. Nilai aktual bergantung ID, timestamp, operation field, dan isi `changes`.

| Topic command (Ground → Edge) | Payload fixture | MQTT PUBLISH |
|---|---:|---:|
| `cmd/config/get` | 174 B | 215 B |
| `cmd/config/patch` | 250 B | 293 B |
| `cmd/processing/set` | 198 B | 243 B |
| `cmd/service/restart` | 179 B | 225 B |
| `cmd/service/ppp/restart` | 175 B | 225 B |
| `cmd/system/reboot/prepare` | 185 B | 237 B |
| `cmd/system/reboot/execute` | 260 B | 312 B |
| `cmd/system/shutdown/prepare` | 187 B | 241 B |
| `cmd/system/shutdown/execute` | 262 B | 316 B |
| `cmd/operation/get` | 204 B | 248 B |
| `cmd/stream/set` | 195 B | 236 B |

### Rata-rata periodik per profile

Asumsi steady-state: source valid, normal Bulk Angular tersedia, DoA baru tiap detik, detail tiap
10 s, diagnostic DoA tiap 3 s, state tiap 60 s, semua gate lolos. Fixture Angular JSON berukuran
2,946 B; angka steady-state lain mengikuti fixture di atas. Belum termasuk event retained di luar
state periodik, request/response settings, command/ACK, MQTT control packets, atau overhead transport.

| Profile | Payload JSON | MQTT PUBLISH | Biaya limiter |
|---|---:|---:|---:|
| `control` | 344.4 B/s | 445.0 B/s (3.56 kbit/s) | 795.3 cost-B/s (6.36 kbit/s) |
| `balanced` | 673.1 B/s | 778.4 B/s (6.23 kbit/s) | 1,144.5 cost-B/s (9.16 kbit/s) |
| `graph_u8` | 673.1 B/s | 778.4 B/s (6.23 kbit/s) | 1,144.5 cost-B/s (9.16 kbit/s) |

State periodik tiap 60 s sudah masuk tabel. Pada `balanced` dan `graph_u8`, Bulk menggunakan
sekitar 349.2 cost-B/s dari budget 350 B/s. Control steady-state menggunakan sekitar 795.3
cost-B/s dari budget 850 B/s. Diagnostic Angular tidak berjalan saat normal Bulk mengirim.
Saat source valid tetapi Bulk terblokir, scheduler menyisihkan sekitar 800 B/s untuk telemetri
Control lain dan memace fixture diagnostic sekitar 62.96 s (49.9 cost-B/s). Saat source invalid,
DoA normal berhenti; reserve 510 B/s menyisakan sekitar 340 B/s dan fixture diagnostic dipace
sekitar 9.26 s (339.2 cost-B/s). Angka ini estimasi aplikasi, bukan hard cap link.

Health dan DoA normal dijadwalkan 1/s; DoA dapat memakai `ok=0` bila hanya trust approval belum
ada. Detail 10 s, state 60 s, diagnostic DoA 3 s, capabilities saat koneksi berubah,
`settings/reported` hanya setelah `config.get`, command/ACK saat diminta atau ada progress.
Angular normal tetap mengikuti eligibility source, client, backlog, token budget, dan stabilisasi;
keberadaan Ground bukan gate publish. Bila hanya authority/angle approval yang hilang, Angular
normal memakai flags partial dan tetap `UNVERIFIED`. Jika gate integritas lain memblokir Bulk,
Edge mengirim kandidat diagnostik dengan interval minimum 6 s (source invalid) atau 30 s
(source eligible, Bulk blocked); ukuran payload dan headroom Control dapat memperpanjang interval.


## Payload yang dipublish Edge

### `telemetry/doa`

```json
{"v":2,"sid":"7a8b9c0d","q":1245,"t":1790668800123,"f":433920000,"a":137.4,"c":8.27,"p":-54.2,"rev":7,"ok":1}
```

Varian unverified pada topic yang sama:

```json
{"v":2,"sid":"7a8b9c0d","q":1246,"t":1790668800123,"f":433920000,"a":10.0,"c":8.27,"p":-54.2,"rev":7,"ok":0,"trust":"UNVERIFIED","angle_reference":"RAW","validation_reasons":["SOURCE_UNVERIFIED","ANGLE_UNVERIFIED"]}
```

Hanya dikirim bila parsing, freshness/clock, DAQ, dan atribusi config lulus, sementara satu-satunya gate yang gagal adalah authority dan/atau verifikasi sudut. Untuk `ok=0`, `a` adalah raw source degree, bukan sudut relatif.

| Field | Makna |
|---|---|
| `q` | Urutan record DoA adapter. |
| `t` | Timestamp native source, bukan waktu publish MQTT. |
| `f` | Frekuensi VFO output dalam Hz. |
| `a` | `ok=1`: sudut relatif menurut konfigurasi angle, bukan otomatis true north. `ok=0`: raw source degree; tafsirkan RAW hanya dengan `angle_reference="RAW"`. |
| `c` | Confidence/PAPR native dB, bukan probabilitas atau persen. |
| `p` | Power native bertanda, bukan level absolut terkalibrasi. |
| `rev` | Revision konfigurasi aman saat sampel dibaca. |
| `ok` | `1` menandakan DoA lolos gate source Edge; `0` menandakan varian `trust=UNVERIFIED`. |
| `trust`, `angle_reference`, `validation_reasons` | Wajib pada varian `ok=0`; metadata trust-only; reasons hanya `SOURCE_UNVERIFIED` dan/atau `ANGLE_UNVERIFIED`. |

Ground menerima `ok=1` sebagai detection hanya setelah session, sequence, timestamp, sample fields, dan health DAQ fresh lolos; `rev` tetap metadata dan tidak harus sama dengan `state.cfg`. `ok=0` dirutekan ke diagnostic DoA view dengan `trust=UNVERIFIED`; Ground tetap memeriksa revision, timestamp, health dan reasons yang diizinkan.

### `telemetry/diagnostic/doa`

```json
{"v":2,"sid":"7a8b9c0d","q":19,"source":"doa.xml","source_timestamp_ms":1790668800123,"observed_timestamp_ms":1790668800444,"raw_doa_deg":200.0,"frequency_mhz":137.0,"trust":"UNVERIFIED","validation_reasons":["DIAGNOSTIC_UNVERIFIED","EMPTY_CSV","DAQ_NOT_HEALTHY","SOURCE_UNVERIFIED","ANGLE_UNVERIFIED"]}
```

`source_timestamp_ms` berasal dari field `TIME` XML sebagai Unix milliseconds; `observed_timestamp_ms` adalah waktu Edge membaca sampel; `frequency_mhz` mempertahankan unit MHz yang ditulis XML; `raw_doa_deg` tidak dikonversi menjadi sudut otoritatif. `validation_reasons` memuat gate `detection` saat publish dan selalu menyertakan `DIAGNOSTIC_UNVERIFIED`. Edge mengirim pesan QoS 0 ini setiap 3 s selama XML tersedia, terlepas dari validitas DoA normal dan perubahan sampel; expiry 3 s, tidak retained. Ground menampilkan topik ini terpisah; data tidak diterima sebagai DoA valid atau dasar command. Ukuran contoh payload 303 B / PUBLISH 352 B memakai fixture lima reasons; payload aktual mengikuti panjang reasons.

### `telemetry/health`

```json
{"v":2,"sid":"7a8b9c0d","q":86,"t":1790668800123,"run":1,"daq":1,"drop":12,"age":280,"temp":61.4,"clk":1,"rev":7}
```

| Field | Makna |
|---|---|
| `q` | Counter health Edge yang maju tiap publish interval. |
| `run` | `0` stopped, `1` running, `2` starting, `3` stopping, `4` error, `255` unknown. |
| `daq` | `0` degraded, `1` healthy, `2` unknown. |
| `drop` | Dropped-frame count dari status DAQ; dapat `null` bila tidak tersedia. |
| `age` | Umur DoA pada source dalam ms, bukan end-to-end latency; dapat `null`. |
| `temp` | Temperatur host dalam derajat C; dapat `null`. |
| `clk` | `0` clock tidak dipercaya, `1` tersinkronisasi. |
| `rev` | Revision konfigurasi aman; dapat `null`. |

Ground memperlakukan health fresh sampai 8 s dan memerlukan `q` yang maju. Health tetap dipublish saat SDR berhenti; service aktif atau MQTT tersambung bukan bukti `daq=1`.

### `telemetry/health/detail`

```json
{"v":2,"sid":"7a8b9c0d","t":1790668800000,"usb":2,"sync":[true,true,true],"cpu":22.1,"mem":41.7,"disk_free":83.2,"throt":false,"uv":false,"tx":12.34,"rx":8.21,"adrop":0,"parse":0}
```

- `usb`: jumlah device USB yang terhitung, dapat `null`.
- `sync`: nilai DAQ untuk urutan `[frame, sample_delay, iq]`; tiap nilai dapat boolean atau `null`.
- `cpu`, `mem`, `disk_free`: persentase.
- `throt`, `uv`: flag throttling dan undervoltage, dapat `null` bila tidak terbaca.
- `tx`, `rx`: trafik interface dalam kbit/s, dapat `null`.
- `adrop`: jumlah frame angular yang dibatalkan Edge.
- `parse`: jumlah error parse source.

Detail tidak menggantikan gate health utama. Ground saat ini menyimpan detail terpisah dari validasi DoA/Angular.

### `state`

```json
{"v":2,"sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","instance":"8a5bb269-73fd-4cbb-9c6b-a3327f679221","t":1790668800123,"run":"RUNNING","daq":true,"cfg":7,"profile":"balanced","clock":"SYNCED"}
```

`run` adalah state processing berupa string; `daq` adalah boolean ringkas; `cfg` revision konfigurasi; `profile` profile saat ini; `clock` state clock. `state.daq=false` tidak membedakan degraded dan unknown. Gunakan `telemetry/health.daq` untuk enum health dan freshness. Ground memakai `sid` bersama `boot` dan `instance` untuk memeriksa identitas sesi.

### `capabilities`

```json
{"v":2,"sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","instance":"8a5bb269-73fd-4cbb-9c6b-a3327f679221","t":1790668800123,"version":"1.0.0","mode":"read_only","codecs":["json"],"angle":"theta_mirror","native_axis":1,"count":360,"profiles":["control","balanced","graph_u8"],"scope":"SDR_STACK","helper_available":true,"maintenance":false,"remote_commands":true,"config_patch":false,"processing":false,"restart":false,"reboot":false,"shutdown":false,"ppp_restart":false,"remote_config_patch":false,"remote_processing":false,"remote_restart":false,"remote_reboot":true,"remote_shutdown":true,"remote_ppp_restart":true}
```

Boolean tanpa prefix (`config_patch`, `processing`, `restart`, `reboot`, `shutdown`,
`ppp_restart`) melaporkan gate panel Edge lokal. `remote_*` melaporkan jalur/target Ground:
settings memerlukan path fixed dan single-writer confirmation; processing/restart memerlukan
unit SDR audited; reboot/shutdown/PPP memerlukan helper. `remote_ppp_restart` tidak membuktikan
unit siap—helper melakukan preflight setiap request. `remote_commands` berarti jalur Ground
tersedia di luar DEMO, bukan otorisasi publisher. `mode=read_only` berlaku pada aksi lokal Edge.
Tidak ada capability yang menggantikan session/health checks atau ACL broker.
`codecs` saat ini hanya `json`. Profile yang didukung:

| Profile | DoA | Angular |
|---|---|---|
| `control` | sekitar 1 s | tidak dipublish |
| `balanced` | sekitar 1 s | JSON, minimum 4 s |
| `graph_u8` | sekitar 1 s | JSON, minimum 2 s; nama profile historis |

Interval Angular dapat memanjang untuk menyesuaikan ukuran payload, budget byte, readiness,
stabilisasi, dan gate scheduler. Koneksi atau penerimaan Ground bukan gate publish.

### `settings/reported`

Edge mengirim payload ini hanya setelah `cmd/config/get` diterima dan seluruh gate settings lolos:

```json
{"v":2,"sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","id":"g01-00001234","rev":7,"t":1790668800123,"settings_json":"{\"center_freq\":433.92,\"uniform_gain\":20.7}"}
```

`settings_json` adalah teks UTF-8 persis dari file settings native yang berhasil dibaca. Envelope
harus memiliki field di atas saja, dipublish QoS 1, non-retained, expiry 30 s. Ukuran seluruh
compact envelope tidak boleh melebihi 8.192 byte; Edge tidak memotong atau memecahnya.

`config.get` hanya dapat dikirim saat MQTT Ground READY dan `state.sid`/`state.boot` tersedia;
fresh health tidak diperlukan untuk read-only request ini. Edge menolak settings TLS-off,
unavailable/stale, atau oversized dengan error `TLS_REQUIRED_FOR_SETTINGS_REPORT`,
`SETTINGS_UNAVAILABLE`, `SETTINGS_STALE`, atau `SETTINGS_REPORT_TOO_LARGE`. Tidak ada report
otomatis saat startup, reconnect, atau perubahan revision. `ack/config` membawa status/error
command; respons settings datang terpisah di topic ini.

Ground menerima respons hanya jika `id`, `sid`, dan `boot` cocok dengan request/session aktif,
timestamp masih fresh, PUBLISH tidak retained, dan isi `settings_json` merupakan JSON object.
`GET /api/v2/config` mengembalikan `settings_json` dan metadata `reported` bersama
`sdr_revision`, `settings_revision`, `proof`, dan `safe_settings`. Teks asli tetap tampil jika
revision berbeda; safe fields hanya diberikan saat `rev` cocok dengan `state.cfg`, dan form
mutation tetap memakai subset safe yang diizinkan.


### `availability`

Saat Control siap, retained publish:

```json
{"v":2,"sid":"7a8b9c0d","online":true,"t":1790668800123}
```

Last Will saat koneksi MQTT hilang:

```json
{"v":2,"sid":"7a8b9c0d","online":false,"reason":"CONNECTION_LOST"}
```

Availability adalah nilai terakhir, bukan heartbeat dan bukan bukti DAQ sehat. Cek `state`,
`health`, dan timestamp. PUBACK hanya menunjukkan broker mengakui PUBLISH QoS 1, bukan aplikasi
Ground memprosesnya.

### `telemetry/angular` (JSON)

Setiap PUBLISH berisi satu object JSON dengan metadata dan seluruh 360 sampel. Tidak ada
envelope chunk; `values` memakai index source dan tidak diquantize.

| Field | Format dan batas |
|---|---|
| `v`, `encoding` | Wajib literal `2` dan `"json"`. |
| `sid`, `q` | `sid`: 8 digit lowercase hex; `q`: integer `1..0xffffffff`. |
| `timestamp_ms`, `frequency_hz` | Timestamp source: integer `1..0x7fffffffffffffff` ms; frekuensi: integer `1..0xffffffff` Hz. |
| `revision`, `vfo`, `convention` | `revision`: `null` atau integer `0..0xfffffffe`; `vfo`: `0..15`; `convention`: `0` atau `1`. |
| `flags` | Integer `0..63`; bitmask bukti parse, freshness/clock, DAQ, convention, config, authority. |
| `raw_doa_deg`, `confidence_native_db` | Keduanya dapat `null`; bila ada, finite dengan batas `0..360` dan `-327.67..327.67` dB. |
| `values` | JSON array tepat 360 angka finite dalam rentang `-1e8..1e8`, urutan index native. |

Ground menerima satu payload bytes dan langsung memvalidasinya:

```python
import json

frame = json.loads(payload)  # bytes dari telemetry/angular
assert frame['encoding'] == 'json'
assert len(frame['values']) == 360
print({key: frame[key] for key in (
    'sid', 'q', 'timestamp_ms', 'frequency_hz', 'revision', 'flags',
    'raw_doa_deg', 'confidence_native_db',
)})
```

Contoh pembuat payload lengkap untuk kedua topic Angular. Array berisi tepat 360 angka; semua
nilainya sintetis, bukan capture radio. Ganti metadata agar sesuai dengan sesi dan revision yang
sedang diuji. Jangan kirim fixture ini ke Ground produksi.

```python
import json
import time

frame = {
    "v": 2,
    "encoding": "json",
    "sid": "7a8b9c0d",
    "q": 1245,
    "timestamp_ms": int(time.time() * 1000),
    "frequency_hz": 433920000,
    "revision": 7,
    "vfo": 0,
    "convention": 1,
    "flags": 63,
    "raw_doa_deg": 137.4,
    "confidence_native_db": 8.27,
    "values": [0.0] * 359 + [1.0],
}
payload = json.dumps(
    frame, separators=(",", ":"), allow_nan=False
).encode("utf-8")
topic = "sdr/v2/uav-01/telemetry/angular"
```

Untuk candidate diagnostik, schema dan bentuk payload sama; suffix topic menjadi
`telemetry/diagnostic/angular`. Frame diagnostik tetap `UNVERIFIED`, sekalipun fixture di atas
memakai `flags=63`.

Ground memeriksa `sid`, source age maksimum 10 s, health fresh dengan `daq=1`, flags, dan
sequence yang maju untuk LIVE. LIVE memerlukan `flags=63`; `revision` tetap metadata dan tidak
dibandingkan dengan `state.cfg`. Pesan yang kehilangan convention dan/atau authority masuk ke
diagnostic sebagai `UNVERIFIED`; pesan yang kehilangan bukti integritas ditolak.

### `telemetry/diagnostic/angular` (JSON)

Topic ini memakai schema JSON yang sama dan membawa 360 sampel terakhir `DOA_value.html`.
Edge mengirim candidate hanya saat normal Bulk tidak dapat mengirimnya: minimum 6 s saat gate
integritas source gagal, minimum 30 s saat source valid tetapi Bulk terblokir/menunggu stabilisasi.
Ukuran JSON dan headroom Control dapat memperpanjang interval. Tidak ada duplikasi saat Bulk normal
tersedia; QoS 0, non-retained, expiry 3 s. Profile `control` tidak mengirim array; diagnostic
tidak mengubah DoA/Angular LIVE.

Flags: bit 0 parsed, bit 1 clock/fresh, bit 2 DAQ, bit 3 angle convention, bit 4 config
attribution, bit 5 source authority. Ground menyimpan diagnostic sebagai `UNVERIFIED`; status
timestamp, health, revision, dan flags menentukan `stale`/`validation_reasons`. Candidate tidak
mengubah DoA/Angular LIVE. `GET /api/v2/angular/diagnostic/latest` mengembalikan `values`,
`encoding`, `source_timestamp_ms`, `source_age_ms`, `received_age_ms`, `flags`, dan alasan validasi.

## Request-only settings exchange

Tombol `Refresh config` mengirim `cmd/config/get`; tidak ada request/report otomatis saat
startup, reconnect MQTT, atau perubahan settings. Ground perlu MQTT READY dan `sid`/`boot`
terkini dari `state`. Request read-only ini tidak memerlukan health fresh. Semua operasi lain
tetap mengikuti gate health fresh, identity, dan approval yang berlaku.

Edge membaca teks file native dari hasil read yang tersedia dan fresh. TLS wajib untuk export.
Edge menolak report jika settings tidak tersedia/stale atau compact JSON envelope melebihi
8.192 byte. Edge tidak memotong atau memecah response. `settings/reported` memakai QoS 1,
non-retained, expiry 30 s, dan membawa `v,sid,boot,id,rev,t,settings_json`. Ground mencocokkan
session, boot, request ID dan timestamp, lalu menyajikan teks asli lewat API loopback. Form
mutation tetap memakai field safe allowlist.

## Payload Ground -> Edge

### Command envelope

Semua command memakai envelope v2 berikut. Ground mengisi identitas sesi terkini dan `base_rev`
dari `state.cfg`; contoh ini fixture dan jangan dikirim mentah.

```json
{"v":2,"id":"g01-00001234","sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","issued_ms":1790668800123,"expires_ms":1790668815123,"base_rev":7,"op":"config.get"}
```

| Field | Makna |
|---|---|
| `id` | ID unik, dipakai ulang tanpa perubahan payload untuk retry intent yang sama. |
| `sid`, `boot` | Identitas sesi dan boot yang terakhir diterima dari `state`. |
| `issued_ms`, `expires_ms` | Deadline epoch ms. TTL harus positif dan maksimum 30 s; Ground default 15 s. |
| `base_rev` | Revision safe config yang diamati Ground; wajib cocok untuk mutation. |
| `op` | Nama operation. Topic command wajib cocok dengan mapping di bawah. |
| operation field | Salah satu `changes`, `desired`, `profile`, `target_id`, `prepare_id`, `challenge`, sesuai operation. Unknown field ditolak. |
Ground HTTP API juga menerima `id` klien opsional dan meneruskannya tanpa perubahan ke envelope MQTT; Ground UI memakainya untuk memulihkan hasil shutdown berdasarkan ID yang sama bila respons HTTP execute hilang.
Login dan CSRF pada Ground HTTP API melindungi request browser, bukan publisher MQTT. Edge tidak
mengautentikasi publisher atau membuktikan command berasal dari sesi UI Ground; publisher lain
yang diizinkan ACL topic dapat mengirim command langsung.


| Topic suffix | `op` | Field tambahan (contoh) |
|---|---|---|
| `cmd/config/get` | `config.get` | Tidak ada. Read-only; meminta teks native settings melalui `settings/reported`. |
| `cmd/config/patch` | `config.patch` | `{"changes":{"center_frequency_hz":433920000,"vfo0_frequency_hz":433920000}}`; subset field safe dan tidak kosong. |
| `cmd/processing/set` | `processing.set` | `{"desired":"STOPPED"}` atau `{"desired":"RUNNING"}`. Hanya target SDR audited; tanpa approval root per command. |
| `cmd/service/restart` | `service.restart` | Tidak ada. Hanya target SDR audited; tanpa approval root per command. |
| `cmd/service/ppp/restart` | `ppp.restart` | Tidak ada. Ground tanpa root grant/approval; helper memeriksa fixed unit loaded/active/no-job pada setiap request. Konfirmasi ulang diperlukan setelah hasil unknown. |
| `cmd/system/reboot/prepare` | `system.reboot.prepare` | Tidak ada. Ground tidak memerlukan remote grant atau approval reboot root. ACK mengembalikan `prepare_id`, `challenge`, `valid_seconds` (30). |
| `cmd/system/reboot/execute` | `system.reboot.execute` | `{"prepare_id":"<prepare-id>","challenge":"<one-time-challenge>"}` dari ACK prepare. Challenge sekali pakai, jangan log atau simpan sebagai credential permanen. |
| `cmd/system/shutdown/prepare` | `system.shutdown.prepare` | Tidak ada. Ground tidak memerlukan approval root; ACK mengembalikan `prepare_id`, `challenge`, `valid_seconds` (30). |
| `cmd/system/shutdown/execute` | `system.shutdown.execute` | `{"prepare_id":"<prepare-id>","challenge":"<one-time-challenge>"}` dari ACK prepare. Ground tidak memerlukan `allow_shutdown`, `shutdown_enabled`, remote grant, atau lease; Edge lokal tetap mengikuti gate dan lease. |
| `cmd/operation/get` | `operation.get` | `{"target_id":"<operation-id>"}`; query jurnal, hasil dapat `null` bila ID tidak ditemukan. |
| `cmd/stream/set` | `stream.set` | `{"profile":"balanced"}`; profile valid: `control`, `balanced`, atau `graph_u8`. |

### Contoh command melalui Ground

Gunakan intent HTTP Ground yang terautentikasi (session dan CSRF), bukan membangun envelope MQTT secara manual. Contoh body `POST /api/v2/commands` untuk query read-only:

```json
{"op":"config.get"}
```

Ground memeriksa MQTT READY dan session `sid`/`boot`, lalu mengisi `id`, deadline serta `base_rev`
dari `state.cfg` sebelum publish QoS 1 ke `cmd/config/get`. Fresh health tidak diperlukan untuk
`config.get`; semua operation lain tetap memerlukannya. Respons HTTP `202 REQUESTED` berarti
intent masuk antrean lokal, bukan hasil command. `ack/config` membawa status/error; teks settings
datang terpisah melalui `settings/reported`. Untuk mencari operation tertentu, body-nya
`{"op":"operation.get","target_id":"<operation-id>"}` dan hasilnya melalui `ack/operation`.

Gunakan API/UI Ground untuk semua mutation agar policy, identity, revision, journal, dan challenge tetap dikelola backend. Contoh field mutation pada tabel di atas bukan pesan mandiri dan tidak boleh dipublish tanpa common envelope serta policy yang valid.

Hanya `config.get` yang tidak memerlukan health fresh di Ground; operasi lain tetap mengikuti
gate health, identity, deadline, clock, revision, capability dan policy lokal. Perintah mutation
memerlukan approval yang sesuai. Konfigurasi default bersifat read-only. Retained command ditolak.
Jika `id` sama dan payload sama, Edge mengembalikan progress/hasil jurnal tanpa menjalankan ulang;
`id` sama dengan payload berbeda adalah conflict.
`config.patch` hanya menerima field safe pada tabel. Saat mengganti `center_frequency_hz`, sertakan `vfo0_frequency_hz` dengan target yang sama; batas frekuensi, bandwidth dan gain berasal dari policy helper lokal.

## ACK command dari Edge

`config.*` dibalas di `ack/config`; operation lain di `ack/operation`. ACK QoS 1, tidak retained, expiry 30 s.

```json
{"v":2,"sid":"7a8b9c0d","id":"g01-00001234","status":"APPLIED","t":1790668804123,"rev":8,"result":{"revision":8,"proof":{"center_frequency_hz":"FRESH_DAQ_RF_CENTER","vfo0_frequency_hz":"FRESH_DOA_FREQUENCY"},"persisted":true}}
```

`result` bergantung pada operation. `config.get` mengembalikan `revision` dan `proof` di
`ack/config`, lalu memicu respons terpisah di `settings/reported`. `operation.get` mengembalikan
`operation` berupa record operation publik atau `null`. Error biasanya berisi `{"error":"ERROR_CODE"}`.
Status yang dikenal jurnal mencakup `ACCEPTED`, `APPLYING`, `VERIFYING`, `REBOOT_SCHEDULED`, `SHUTDOWN_SCHEDULED`, `APPLIED`, `FAILED`, `REJECTED`, `EXPIRED`, `CONFLICT`, `PERSISTED_UNVERIFIED`, `OUTCOME_UNKNOWN`, dan `CANCELLED`.

`PERSISTED_UNVERIFIED` berarti perubahan tersimpan tetapi bukti runtime belum lengkap. `OUTCOME_UNKNOWN` berarti hasil side effect belum diketahui, bukan bukti gagal atau berhasil. Ground tidak boleh mengubah dua status ini menjadi sukses/gagal pasti atau mengulang mutation dengan ID baru tanpa rekonsiliasi.

## ACL minimum Ground

| Credential / role | Hak topic minimum |
|---|---|
| Edge Control | Publish topic operasional, `settings/reported`, ACK dan availability milik node sendiri; subscribe `sdr/v2/{node_id}/cmd/#`. |
| Edge Bulk | Publish hanya `sdr/v2/{node_id}/telemetry/angular`. |
| Ground controller | Subscribe topic operasional/ACK dan `settings/reported`; publish `sdr/v2/{node_id}/cmd/#`. |
| Ground viewer | Subscribe saja; tanpa hak publish command dan tanpa credential controller. |

Prefix, `node_id`, ACL, broker, credential dan konfigurasi Edge/Ground harus cocok. Jangan memberi hak wildcard lintas node bila Ground hanya mengelola satu node. Pada broker yang mengizinkan anonymous CONNECT, ACL tetap wajib membatasi operasi yang dapat dilakukan client.

## Mendiagnosis telemetri invalid di Ground

Pada API Edge `GET /api/v2/link`, `mqtt_topic_delivery.<suffix>.state` dan `confirmation`
menunjukkan tahap pengiriman. Untuk QoS 0, `confirmation="SOCKET_WRITE"` hanya membuktikan
penulisan ke socket lokal; untuk QoS 1, `PUBACK` membuktikan broker mengakui PUBLISH.
Tidak ada konfirmasi delivery aplikasi dari Ground ke Edge. Periksa Ground API untuk status
penerimaan dan validasi.

Pada API Ground, `GET /api/v2/link` menunjukkan status client dan counter kumulatif `rejected`.
Counter itu juga naik ketika antrean penerimaan penuh; ia bukan error code dan tidak menunjukkan
alasan tiap payload ditolak. `GET /api/v2/angular/latest` menampilkan frame LIVE atau `null`;
`GET /api/v2/angular/diagnostic/latest` menampilkan candidate Angular yang disimpan terpisah.
Gunakan `GET /api/v2/snapshot` untuk memeriksa `node_state`, `config`, `health_fresh`,
`detection`, dan `diagnostic_doa`.

### Urutan validasi Angular

1. Subscribe ke prefix node yang benar: `sdr/v2/{node_id}/telemetry/angular`. `node_id` pada
   Edge, Ground, broker ACL, dan topic harus identik. Tunggu retained `state` dan `capabilities`;
   `settings/reported` hanya tiba setelah `config.get` eksplisit dan bukan prasyarat telemetry.
   Telemetry non-retained yang datang sebelum state dikenali dapat diabaikan.
2. Kaitkan frame dengan `state.sid`. `revision` tetap metadata; Ground tidak mensyaratkan
   kesamaan revision dengan `state.cfg` untuk LIVE. Saat `sid` berubah, buang cache sesi lama.
3. Decode satu payload PUBLISH langsung sebagai satu object JSON v2 dengan
   `encoding="json"`. Jangan jalankan decoder binary/RDF2, base64 decode, atau chunk reassembly.
   `values` harus berupa array berisi tepat 360 angka finite, masing-masing dalam rentang
   `-1e8..1e8`.
4. Validasi waktu source `timestamp_ms` terhadap jam Ground: paling lama 10 s dan tidak boleh
   lebih dari 1 s di masa depan. Jangan mengganti timestamp source dengan waktu penerimaan untuk
   membuat frame stale terlihat fresh.
5. Wajib ada `telemetry/health` dengan `daq=1`, timestamp health fresh (maksimum 8 s), dan
   sequence `q` yang maju. Health menunjukkan kesehatan DAQ; koneksi MQTT atau `state.daq` saja
   tidak menggantikannya.
6. Terapkan flags secara terpisah dari validasi struktur:

| Bit | Nilai mask | Bukti |
|---:|---:|---|
| 0 | 1 | Record berhasil diparse. |
| 1 | 2 | Clock dipercaya dan source fresh. |
| 2 | 4 | DAQ sehat. |
| 3 | 8 | Konvensi sudut diverifikasi. |
| 4 | 16 | Atribusi konfigurasi cocok. |
| 5 | 32 | Authority source diverifikasi. |

Untuk `telemetry/angular`, mask integritas minimum adalah `1|2|4|16 = 23`. Jika
`flags & 23 != 23`, frame tidak memenuhi bukti integritas dan ditolak. Jika mask minimum lolos
dan `flags == 63`, frame dapat masuk LIVE setelah seluruh gate lain lolos. `revision` tidak
dibandingkan dengan `state.cfg`. Jika mask minimum lolos tetapi `flags != 63`, simpan sebagai
`UNVERIFIED` di jalur diagnostic; hilangnya bit convention dan/atau authority tidak membuatnya
valid.

`telemetry/diagnostic/angular` juga membawa satu JSON object dengan 360 values. Ground menyimpannya
terpisah sebagai `UNVERIFIED`; ia tidak mengisi Angular LIVE. Endpoint diagnostic menambahkan
umur dan validation reasons agar UI dapat membedakan candidate stale dari candidate yang hanya
belum memiliki authority/convention.

### Petunjuk dari hasil validasi

| Hasil / gejala | Periksa |
|---|---|
| `BAD_ANGULAR_JSON` | Payload harus JSON object dengan `v=2` dan `encoding="json"`; decoder binary lama tidak kompatibel. |
| `EXPECTED_360_SAMPLES` | Payload harus memuat seluruh 360 angka dalam satu PUBLISH, bukan potongan atau array parsial. |
| `SOURCE_TIMESTAMP_NOT_FRESH` | `timestamp_ms` adalah Unix ms dari source; cek satuan, freshness source, dan sinkronisasi clock kedua host. |
| `ANGULAR_EVIDENCE_INCOMPLETE` | Salah satu bit parsed, fresh, DAQ, atau config tidak ada. Perbaiki bukti di Edge/source; jangan mengisi bit di Ground. |
| `ANGULAR_WITHOUT_HEALTH` | Health belum diterima, sudah stale, atau `daq` bukan `1`. Tunggu health fresh sebelum memproses frame LIVE. |
| `flags` lolos mask `23`, tetapi bukan `63` | Frame trust-only `UNVERIFIED`; baca endpoint diagnostic. `detection.valid=false` adalah hasil yang diharapkan, bukan kegagalan JSON. |
| `link.rejected` bertambah | Counter tidak mengungkap alasan. Periksa bentuk payload/topic dan korelasi `sid`, waktu, health, flags, dan sequence. |

Untuk DoA, `telemetry/doa` dengan `ok=1` dapat mengisi detection setelah freshness, sequence,
sample values, dan health lolos; Ground tidak membandingkan `rev` dengan `state.cfg`.
`ok=0` hanya diterima sebagai raw `UNVERIFIED` bila `trust`, `angle_reference="RAW"`, revision,
health, dan reasons yang diizinkan cocok; ia tidak mengisi detection. `telemetry/diagnostic/doa`
juga selalu terpisah dari deteksi LIVE.

Versi Edge yang mengirim Angular JSON tidak kompatibel dengan Ground RDF2 yang mengharapkan
binary chunk. Perbarui decoder Ground ke kontrak JSON v2 sebelum mengubah gate validitas.
Jangan mempromosikan frame parsial menjadi LIVE hanya agar dashboard tampak valid.

## Referensi implementasi

- `src/rdf_node/agent.py`: topic Edge, payload JSON, jadwal publish dan capability.
- `src/rdf_node/ground.py`: subscription, validasi JSON Angular/telemetry/settings dan command publish.
- `src/rdf_node/control.py`: daftar operasi, envelope validation, idempotency dan ACK.
- `src/rdf_node/codec.py`: validasi field dan 360 nilai Angular JSON.
- `docs/PROTOCOL.md`: kontrak wire dan batas freshness.
- `docs/MQTT_GROUND.md`: transport, provisioning dan ACL Ground.
