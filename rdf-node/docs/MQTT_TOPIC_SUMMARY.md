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

Payload JSON adalah object v2 langsung, tanpa wrapper tambahan. `telemetry/angular` dan `telemetry/diagnostic/angular` adalah payload binary RDF2, bukan JSON atau Base64. MQTT tidak mengirim raw IQ, raw CSV, koordinat GPS, kredensial broker, atau PIN admin.

## Koneksi dan pemisahan kanal

Edge membuka dua MQTT client:

| Kanal | Client ID | Hak data |
|---|---|---|
| Control | `{client_id}-control` | Publish JSON telemetry (termasuk `telemetry/doa` UNVERIFIED dan topik diagnostik), binary `telemetry/diagnostic/angular`, state, capabilities, config report, availability, ACK; subscribe command dan receipt. |
| Bulk | `{client_id}-bulk` | Publish `telemetry/angular` normal, termasuk varian UNVERIFIED dengan flags parsial. Tidak subscribe command dan tidak memiliki hak command. |
| Ground | `{node_id}-ground` | Subscribe data operasional/ACK; publish command dan receipt. |

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
| `telemetry/diagnostic/angular` | Edge -> Ground | Control | 0 | Tidak | Tiap 6 s saat gate integritas source gagal; tiap 30 s jika source eligible tetapi Bulk terblokir/menunggu stabilisasi; tidak diduplikasi ketika Angular normal mengalir; profile `control` menahan array. Expiry 3 s | Frame RDF2 candidate dengan source timestamp; Ground menyimpan terpisah sebagai `UNVERIFIED`, tidak menunggu receipt dan tidak mengisi `aq`. |
| `telemetry/health` | Edge -> Ground | Control | 0 | Tidak | Tiap 1 s; expiry 5 s | Status run, DAQ, clock, umur sumber, temperatur, dropped frames. |
| `telemetry/health/detail` | Edge -> Ground | Control | 0 | Tidak | Tiap 10 s; expiry 15 s | Detail host, USB, sync DAQ, trafik, parse dan abort counter. |
| `telemetry/angular` | Edge -> Ground | Bulk | 0 | Tidak | Profile-dependent; expiry 3 s | Frame 360 sampel; flags `63` untuk LIVE atau trust-only partial untuk UNVERIFIED. |
| `state` | Edge -> Ground | Control | 1 | Ya | Saat state berubah dan paling lambat tiap 60 s | Status run/DAQ, profile, clock dan identitas boot. Jangan dipakai sebagai heartbeat. |
| `capabilities` | Edge -> Ground | Control | 1 | Ya | Saat generation koneksi Control berubah | Versi, identitas, codec, profile dan capability yang diizinkan. |
| `config/reported` | Edge -> Ground | Control | 1 | Ya | Saat startup/reconnect atau report diminta/perubahan revision | Safe settings, revision, digest dan tingkat proof. Tidak berisi secret. |
| `availability` | Edge -> Ground | Control | 1 | Ya | Online saat koneksi siap; Last Will saat koneksi hilang | Ketersediaan Control, bukan bukti DAQ sehat. |
| `ack/config` | Edge -> Ground | Control | 1 | Tidak | Saat progress/hasil command `config.*`; expiry 30 s | ACK command konfigurasi. |
| `ack/operation` | Edge -> Ground | Control | 1 | Tidak | Saat progress/hasil command selain `config.*`; expiry 30 s | ACK command operation. |
| `ground/receipt` | Ground -> Edge | Control | 0 | Tidak | Sekitar tiap 5 s bila health Ground masih fresh; expiry 5 s | Bukti aplikasi Ground menerima/memproses sequence tertentu. |
| `cmd/config/get` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Minta config report terbaru. |
| `cmd/config/patch` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Intent perubahan field safe yang diizinkan. |
| `cmd/processing/set` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Intent `RUNNING` atau `STOPPED` untuk SDR stack yang di-approve. |
| `cmd/service/restart` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Restart SDR stack yang di-approve. Bukan restart PPP atau bridge. |
| `cmd/service/ppp/restart` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Minta restart fixed `t900-ppp.service`; memerlukan remote grant dan approval PPP terpisah. |
| `cmd/system/reboot/prepare` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Meminta challenge reboot sekali pakai. |
| `cmd/system/reboot/execute` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Eksekusi reboot dengan `prepare_id` dan `challenge` yang masih valid. |
| `cmd/system/shutdown/prepare` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Meminta challenge shutdown sekali pakai; approval terpisah. |
| `cmd/system/shutdown/execute` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Menjadwalkan poweroff dengan `prepare_id` dan challenge yang masih valid. |
| `cmd/operation/get` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Query hasil operation berdasarkan ID. |
| `cmd/stream/set` | Ground -> Edge | Control | 1 | Tidak | Command TTL default 15 s, maksimum 30 s | Ganti profile telemetry. |

### Peran setiap topic di Ground

| Topic | Yang dilakukan Ground |
|---|---|
| `telemetry/doa` | `ok=1`: validasi revision, sequence, timestamp dan health sebelum detection. `ok=0`: tampilkan sebagai raw diagnostic UNVERIFIED; jangan gabungkan ke detection, receipt atau keputusan kontrol. |
| `telemetry/diagnostic/doa` | Tampilkan XML terpisah sebagai diagnostik UNVERIFIED dengan timestamp dan reasons; jangan gabungkan ke detection, receipt, atau keputusan kontrol. |
| `telemetry/diagnostic/angular` | Simpan frame lengkap terbaru terpisah sebagai UNVERIFIED; expose lewat `GET /api/v2/angular/diagnostic/latest`; jangan gabungkan ke `telemetry/angular`, DoA, detection atau receipt. |
| `telemetry/health` | Gunakan sebagai sumber utama freshness dan status DAQ; pisahkan MQTT tersambung dari `daq=1`. |
| `telemetry/health/detail` | Pakai untuk diagnosis host, USB, sync, trafik dan error; jangan jadikan pengganti gate health. |
| `telemetry/angular` | Rakit semua chunk sebelum menggambar kurva. `flags=63` baru dapat maju ke LIVE/receipt `aq`; partial yang hanya kehilangan authority/convention masuk diagnostic UNVERIFIED. |
| `state` | Bootstrap identitas boot/sesi dan profile; saat `sid` berubah, buang cache telemetry sesi lama. Retained state bukan heartbeat. |
| `capabilities` | Tampilkan operasi sesuai capability node; tetap tegakkan ACL dan policy Ground. |
| `config/reported` | Simpan safe settings, revision dan proof. Jika revision berubah, buang DoA/Angular dari revision sebelumnya. |
| `availability` | Tampilkan status koneksi Control sebagai petunjuk saja; cek health untuk mengetahui kondisi DAQ. |
| `ack/config` | Cocokkan `id` dengan journal command konfigurasi; tampilkan progress dan hasil tanpa menganggap ACK broker sebagai hasil operasi. |
| `ack/operation` | Cocokkan `id` untuk lifecycle, stream, reboot dan shutdown; jangan anggap `SHUTDOWN_SCHEDULED` sebagai host telah mati. |
| `ground/receipt` | Kirim sequence health/DoA/Angular yang benar-benar sudah diproses; jangan kirim hanya karena broker mengirim PUBLISH. |
| `cmd/config/get` | Minta safe config terbaru; proses ACK `revision`/`proof` dan report terpisah di `config/reported`. |
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

Telemetri tidak retained supaya subscriber tidak menganggap data lama sebagai data live. `state`, `capabilities`, `config/reported`, dan `availability` retained untuk bootstrap, tetapi nilai retained tetap harus diuji freshness dan session-nya. ACK tidak retained dan punya expiry terbatas; Ground perlu menyimpan ID operation dan query ulang bila reconnect melewatkan ACK.

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

Angka berikut diukur dari serializer JSON compact (`util.compact`) dan paket MQTT v5 yang dibuat `publish_packet`, memakai prefix contoh `sdr/v2/uav-01`. `Payload` adalah byte JSON UTF-8 atau binary saja. `PUBLISH` mencakup topic, framing MQTT, field properti (termasuk expiry bila disetel), dan packet ID untuk QoS 1; tidak mencakup TCP/TLS/WebSocket/IP/PPP. Ukuran JSON adalah ukuran fixture di bawah, bukan ukuran maksimum; digit timestamp, ID, nilai opsional, dan field command dapat mengubahnya.

### Telemetri berkala Edge → Ground

| Topic / profile | Interval saat syarat lolos | Payload contoh | MQTT PUBLISH |
|---|---|---:|---:|
| `telemetry/doa` | Maks. 1/s; verified atau UNVERIFIED untuk trust-only blockers | 109 B verified (contoh; varian U menambah metadata) | 147 B verified (contoh; varian U lebih besar) |
| `telemetry/diagnostic/doa` | Setiap 3 s selama XML tersedia; heartbeat mengulang sampel yang sama, independen dari gate DoA normal | 303 B | 352 B |
| `telemetry/health` | 1/s | 113 B | 154 B |
| `telemetry/health/detail` | 1/10 s | 179 B | 227 B |
| `telemetry/angular` (`balanced`, Q16) | 2 chunk/4 s; tiap chunk | 396 B | 438 B |
| `telemetry/angular` (`graph_u8`, U8) | 1 chunk/2 s | 420 B | 462 B |
| `telemetry/angular` (`control`) | Tidak dipublish | — | — |
| `telemetry/diagnostic/angular` (`balanced`, Q16) | 2 chunk/frame; 6 s saat gate integritas gagal, 30 s saat valid-source Bulk blocked; tidak dikirim terus untuk trust-only fallback setelah resume | 396 B/chunk; 792 B/frame | 449 B/chunk; 898 B/frame |
| `telemetry/diagnostic/angular` (`graph_u8`, U8) | 1 chunk/frame; 6 s saat gate integritas gagal, 30 s saat valid-source Bulk blocked; tidak dikirim terus untuk trust-only fallback setelah resume | 420 B | 473 B |
| `telemetry/diagnostic/angular` (`control`) | Tidak dipublish | — | — |

Satu frame Q16 normal berarti 792 B payload/876 B dalam dua PUBLISH; diagnostic Q16 memakai 898 B karena topic lebih panjang. Angular adalah binary, bukan JSON. Dengan biaya limiter `PUBLISH + 142 B` (ditambah 144 B untuk QoS 1), cadence 1/s untuk health, DoA, dan diagnostic DoA bersama detail tiap 10 s memerlukan sekitar 1.116 cost-B/s, di atas budget Control default 850. Karena itu diagnostic DoA dikirim setiap 3 s. Diagnostic angular menambah 197 cost-B/s Q16 atau 102.5 cost-B/s U8 saat gate integritas gagal (interval 6 s); ketika valid-source Bulk diblokir, tambahan turun menjadi 39.4/20.5 cost-B/s (interval 30 s). DoA UNVERIFIED membawa metadata tambahan dibanding contoh verified. Trust-only fallback Angular tidak menambah diagnostic traffic berulang setelah stabilisasi; tidak ada biaya array diagnostik saat Bulk normal tersedia atau profile `control`.

### Topic event-driven dan command

Ukuran event memakai contoh JSON yang dicantumkan di bawah. State dapat dipublish saat berubah dan paling lambat tiap 60 s; capabilities/config/availability dikirim saat startup atau koneksi/event terkait, bukan tiap detik. ACK muncul saat progress/hasil command, dan satu command dapat memicu beberapa ACK.

| Topic / fixture | Kapan dikirim | Payload contoh | MQTT PUBLISH |
|---|---|---:|---:|
| `state` | Perubahan / maks. 60 s | 211 B | 238 B |
| `capabilities` | Generation koneksi Control berubah | 434 B | 468 B |
| `config/reported` | Startup/reconnect, diminta, atau revision berubah | 364 B | 401 B |
| `availability` online | Control siap | 56 B | 89 B |
| Last Will `availability` offline | Broker saat koneksi hilang; payload disertakan di CONNECT | 66 B | Bukan PUBLISH Edge |
| `ack/config` | Progress/hasil `config.*` | 226 B* | 263 B* |
| `ack/operation` | Progress/hasil operation lain | 226 B* | 266 B* |
| `ground/receipt` (Ground → Edge) | Sekitar 1/5 s selama health Ground fresh | 60 B | 98 B |

`*` Kedua baris ACK memakai JSON fixture `ack/config` di bawah hanya untuk mengukur ukuran paket; payload ACK aktual berubah mengikuti status dan `result` operation.

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

Asumsi steady-state: source valid, normal Bulk angular path tersedia, DoA baru tiap detik, detail tiap 10 s, diagnostic DoA tiap 3 s, state tiap 60 s, semua gate lolos. Diagnostic angular tidak diduplikasi saat stream normal tersedia. Nilai belum memasukkan event retained di luar state periodik, command/ACK, receipt Ground, MQTT control packets, atau overhead transport.

| Profile | Payload JSON/binary | MQTT PUBLISH | Biaya limiter |
|---|---:|---:|---:|
| `control` | 344.4 B/s | 445.0 B/s (3.56 kbit/s) | 795.3 cost-B/s (6.36 kbit/s) |
| `balanced` | 542.4 B/s | 664.0 B/s (5.31 kbit/s) | 1,085.3 cost-B/s (8.68 kbit/s) |
| `graph_u8` | 554.4 B/s | 676.0 B/s (5.41 kbit/s) | 1,097.3 cost-B/s (8.78 kbit/s) |

State periodik tiap 60 s sudah masuk tabel; receipt Ground sekitar 19.6 PUBLISH B/s pada arah balik. Pada `balanced`, Control memakai sekitar 795.3 cost-B/s dari budget 850 saat Bulk normal. Saat valid-source Bulk blocked, Q16 diagnostic menaikkan Control ke sekitar 834.7 cost-B/s; saat gate integritas source gagal, DoA normal berhenti dan diagnostic 6 s memberi sekitar 703.3 cost-B/s. Angka ini memakai payload DoA verified; varian UNVERIFIED lebih besar dan tidak menjadi hard cap link. QoS 0 tetap dapat expiry saat event/backlog.

Health dan DoA normal dijadwalkan 1/s; DoA dapat berupa varian `ok=0` bila hanya trust approval yang belum ada. Detail 10 s, state 60 s, diagnostic DoA 3 s, capabilities/config saat event, command/ACK saat diminta atau ada progress. Angular normal dapat tertahan oleh source tidak eligible/fresh, command berjalan, Bulk tidak siap, backlog Control, atau masa stabilisasi; receipt Ground bukan gate publish. Jika hanya authority/angle approval yang hilang, Angular normal memakai flags partial setelah stabilisasi dan tetap `UNVERIFIED`; jika gate integritas lain memblokir Bulk, Edge mengirim kandidat diagnostik terpisah (6 s untuk source invalid, 30 s untuk valid-source Bulk blocked). QoS 0/expiry 3 s bisa membuang chunk; profile `control` menahan array diagnostik.


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

Ground menerima `ok=1` sebagai detection hanya jika revision, sequence, timestamp, dan health DAQ fresh cocok. `ok=0` dirutekan ke diagnostic DoA view dengan `trust=UNVERIFIED`; ia tidak mengisi detection atau receipt `dq`. Ground menolak varian unverified dengan reason lain, revision mismatch, timestamp stale, atau tanpa health DAQ fresh.

### `telemetry/diagnostic/doa`

```json
{"v":2,"sid":"7a8b9c0d","q":19,"source":"doa.xml","source_timestamp_ms":1790668800123,"observed_timestamp_ms":1790668800444,"raw_doa_deg":200.0,"frequency_mhz":137.0,"trust":"UNVERIFIED","validation_reasons":["DIAGNOSTIC_UNVERIFIED","EMPTY_CSV","DAQ_NOT_HEALTHY","SOURCE_UNVERIFIED","ANGLE_UNVERIFIED"]}
```

`source_timestamp_ms` berasal dari field `TIME` XML sebagai Unix milliseconds; `observed_timestamp_ms` adalah waktu Edge membaca sampel; `frequency_mhz` mempertahankan unit MHz yang ditulis XML; `raw_doa_deg` tidak dikonversi menjadi sudut otoritatif. `validation_reasons` memuat gate `detection` saat publish dan selalu menyertakan `DIAGNOSTIC_UNVERIFIED`. Edge mengirim pesan QoS 0 ini setiap 3 s selama XML tersedia, terlepas dari validitas DoA normal dan perubahan sampel; expiry 3 s, tidak retained. Ground menampilkan topik ini terpisah; data tidak diterima sebagai DoA valid, receipt, atau dasar command. Ukuran contoh payload 303 B / PUBLISH 352 B memakai fixture lima reasons; payload aktual mengikuti panjang reasons.

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
{"v":2,"sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","instance":"8a5bb269-73fd-4cbb-9c6b-a3327f679221","version":"1.0.0","mode":"read_only","codecs":["q16","u8"],"angle":"theta_mirror","native_axis":1,"count":360,"profiles":["control","balanced","graph_u8"],"scope":"SDR_STACK","helper_available":true,"maintenance":false,"remote_commands":false,"config_patch":false,"processing":false,"restart":false,"reboot":false,"shutdown":false,"ppp_restart":false}
```

Boolean capability (`remote_commands`, `config_patch`, `processing`, `restart`, `reboot`, `shutdown`, `ppp_restart`) adalah izin/runtime availability yang dilaporkan node, bukan otorisasi broker. Untuk operasi remote Ground, cek juga `remote_commands`; PPP restart memerlukan `ppp_restart`. Nilainya dapat berbeda antar perangkat karena approval lokal. Profile yang didukung saat ini:

| Profile | DoA | Angular |
|---|---|---|
| `control` | sekitar 1 s | tidak dipublish |
| `balanced` | sekitar 1 s | Q16 sekitar 4 s |
| `graph_u8` | sekitar 1 s | U8 sekitar 2 s |

Interval hanya berlaku saat data valid, client siap, scheduler stabil, dan gate Bulk/command/control-backlog lolos; Edge tidak menunggu receipt Ground. Ini bukan jaminan rate aktual.

### `config/reported`

```json
{"v":2,"sid":"7a8b9c0d","rev":7,"t":1790668800123,"proof":"source_correlated","digest":"<safe-settings-digest>","effective":{"center_frequency_hz":433920000,"gain_db":20.7,"vfo0_frequency_hz":433920000,"vfo0_bandwidth_hz":125000,"vfo0_squelch_db":-20.0,"ant_arrangement":"<native-value>","doa_method":"<native-value>","active_vfos":1,"output_vfo":0,"en_doa":true}}
```

`effective` hanya berisi field safe yang tersedia dari source. Nama yang dapat muncul: `center_frequency_hz`, `gain_db`, `vfo0_frequency_hz`, `vfo0_bandwidth_hz`, `vfo0_squelch_db`, `ant_arrangement`, `doa_method`, `active_vfos`, `output_vfo`, `en_doa`. Field dapat tidak ada bila source tidak menyediakannya. Tidak ada secret atau dump semua native settings.

`proof` melaporkan `unverified`, `source_correlated`, `runtime`, atau `persisted_unverified` sesuai bukti yang tersedia. `digest` mengidentifikasi safe settings, bukan bukti semua parameter sudah diterapkan runtime. `config.get` meminta report baru. Perubahan `rev` membuat Ground membuang DoA/Angular lama sebelum menerima data dari revision baru.

### `availability`

Saat Control siap, retained publish:

```json
{"v":2,"sid":"7a8b9c0d","online":true,"t":1790668800123}
```

Last Will saat koneksi MQTT hilang:

```json
{"v":2,"sid":"7a8b9c0d","online":false,"reason":"CONNECTION_LOST"}
```

Availability adalah nilai terakhir, bukan heartbeat dan bukan bukti DAQ sehat. Cek `state`, `health`, dan timestamp; receipt adalah bukti pemrosesan aplikasi Ground yang terpisah, bukan syarat publish.

### `telemetry/angular` (binary)

Setiap message membawa satu chunk binary little-endian. Envelope 12 byte memakai format `<IIBBH`:

| Bagian | Tipe | Makna |
|---|---|---|
| `sid` | `u32` | Alias session yang sama dengan `sid` JSON. |
| `q` | `u32` | Sequence record angular. |
| `index` | `u8` | Index chunk, mulai dari 0. |
| `count` | `u8` | Jumlah chunk frame. |
| `total` | `u16` | Panjang frame sebelum dipecah. |
| body | bytes | Potongan frame RDF2. |

Setelah semua chunk terkumpul, frame dimulai dengan header 48 byte `<4sBBHIIQIIBBHffHh`, lalu 360 sampel:

| Field header | Makna |
|---|---|
| magic, version, encoding | `RDF2`, versi `2`, encoding `1` Q16 atau `2` U8. |
| flags | Bit parsed/fresh/DAQ/convention/config/source-authority; LIVE harus `63`, diagnostic boleh membawa subset bukti yang tersedia. |
| sid, q, timestamp | Identitas sesi, sequence dan source timestamp. |
| frequency, revision, vfo | Frekuensi Hz, revision (`0xffffffff` berarti unknown), dan index VFO. |
| convention, count | Konvensi angle dan jumlah sampel, wajib `360`. |
| scale, offset | Parameter decode sampel. |
| raw DoA, confidence | Derajat x100 (`65535` berarti unknown), confidence dB x100 (`-32768` berarti unknown). |

Ukuran dan encoding:

- Q16: Frame 768 byte. Sample signed `int16` little-endian, scale `0.01`, offset `0`, range `-327.67` sampai `327.67`; sample `-32768` invalid dan overflow ditolak tanpa clipping. Dua chunk, masing-masing 396 byte termasuk envelope, total 792 byte.
- U8: Frame 408 byte dengan 360 unsigned byte dan scale/offset per frame. Satu chunk berukuran 420 byte termasuk envelope. Array konstan memakai scale `0` dan nilai setiap sampel sama dengan offset.
- Array hasil decode adalah 360 nilai kuantisasi, bukan CSV lossless. Konvensi native index tidak otomatis dikonversi ke true north.

Contoh decode di Ground:

```python
from rdf_node.codec import Assembler

assembler = Assembler()
for payload in mqtt_payloads:  # payload adalah bytes dari telemetry/angular
    frame = assembler.add(payload)
    if frame is None:  # frame belum lengkap
        continue
    assert len(frame['values']) == 360
    print({key: frame[key] for key in (
        'encoding', 'sid', 'q', 'timestamp_ms', 'frequency_hz', 'revision',
        'raw_doa_deg', 'confidence_native_db', 'peak_index',
    )})
```

Ground menunggu seluruh frame, memeriksa `sid`, revision, `flags==63`, source age maksimum 10 s, health fresh dengan `daq=1`, dan sequence yang maju untuk LIVE. Flags legacy `31` tidak membuktikan authority dan hanya masuk diagnostic UNVERIFIED bila bit parsed/fresh/DAQ/config lengkap; partial yang juga kehilangan salah satu bit integritas ditolak. Fragment parsial tidak boleh ditampilkan. Assembler menerima maksimal dua frame incomplete dan membuang frame setelah deadline 3 s.

### `telemetry/diagnostic/angular` (binary)

Topic ini memakai framing chunk dan frame RDF2 yang sama seperti `telemetry/angular`; tiap frame membawa 360 sampel terkuantisasi dan timestamp sumber `DOA_value.html`. Edge mengirim candidate hanya saat normal Bulk tidak dapat mengirimnya: tiap 6 s jika gate integritas source gagal selain trust-only approval, tiap 30 s jika source valid tetapi Bulk terblokir/menunggu stabilisasi, dan tidak ada duplikasi saat Bulk normal tersedia. QoS 0, non-retained, expiry 3 s, tanpa menunggu receipt; `control` tidak mengirim array diagnostik. `balanced` memakai Q16, `graph_u8` U8; Q16 overflow memakai U8 tanpa clipping.

Flags menyimpan bukti parsial saat publish: bit 0 parsed, bit 1 clock/fresh, bit 2 DAQ, bit 3 angle convention, bit 4 config attribution, bit 5 source authority. Ground menampilkan frame lengkap dengan `trust=UNVERIFIED` pada diagnostic view bila hanya bit authority/convention yang hilang; flags dan status lain menentukan `stale`/`validation_reasons`. Frame tidak mengubah DoA/Angular LIVE atau `aq`. Kandidat terbaru tersedia melalui `GET /api/v2/angular/diagnostic/latest`, termasuk `values`, `encoding`, `source_timestamp_ms`, `source_age_ms`, `received_age_ms`, `flags`, dan alasan validasi.

## Payload Ground -> Edge

### `ground/receipt`

```json
{"v":2,"sid":"7a8b9c0d","dq":1245,"hq":86,"aq":1238,"rev":7}
```

- `hq`: sequence health terakhir yang diterima.
- `dq`: sequence DoA terakhir yang diterima, `0` bila belum ada.
- `aq`: sequence angular terakhir yang selesai dirakit dan lolos gate, `0` bila belum ada.
- `rev`: revision config report yang digunakan Ground; dapat `null` sebelum report diterima.

Ground mengirim receipt sekitar tiap 5 s selama health-nya masih fresh (<8 s). Edge menolak retained receipt, `sid` berbeda, sequence yang tidak pernah dikirim, dan `hq` yang mundur. Receipt hanya menunjukkan pemrosesan aplikasi Ground, bukan PUBACK dan bukan bukti operator melihat dashboard. Pengiriman telemetry DoA dan Angular tidak menunggu receipt atau keberadaan subscriber Ground; konfigurasi lama `telemetry.require_ground_receipt_for_bulk` diabaikan saat config dimuat.

### Membaca receipt di Edge

`GET /api/v2/link` pada API Edge mengembalikan status receipt pada `ground`. Cuplikan contoh:

```json
{"ground":{"state":"RECEIVING","age_ms":1000,"last":{"v":2,"sid":"7a8b9c0d","dq":1245,"hq":86,"aq":1238,"rev":7},"rejected":0}}
```

`age_ms` mengukur waktu sejak progress sequence health, bukan umur paket MQTT. `UNCONFIRMED` berarti belum ada progress receipt; `LOST`/`LATE` adalah indikator Ground, bukan gate publish. MQTT PUBACK tetap terpisah dari receipt aplikasi.

### Command envelope

Semua command memakai envelope v2 berikut. Ground harus mengisi identitas dan revision yang benar-benar terakhir diterima; contoh ini fixture dan jangan dikirim mentah.

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


| Topic suffix | `op` | Field tambahan (contoh) |
|---|---|---|
| `cmd/config/get` | `config.get` | Tidak ada. Read-only; meminta report config baru. |
| `cmd/config/patch` | `config.patch` | `{"changes":{"center_frequency_hz":433920000,"vfo0_frequency_hz":433920000}}`; subset field safe dan tidak kosong. |
| `cmd/processing/set` | `processing.set` | `{"desired":"STOPPED"}` atau `{"desired":"RUNNING"}`. |
| `cmd/service/restart` | `service.restart` | Tidak ada. Hanya unit SDR stack yang di-approve. |
| `cmd/system/reboot/prepare` | `system.reboot.prepare` | Tidak ada. ACK mengembalikan `prepare_id`, `challenge`, `valid_seconds` (30). |
| `cmd/system/reboot/execute` | `system.reboot.execute` | `{"prepare_id":"<prepare-id>","challenge":"<one-time-challenge>"}` dari ACK prepare. Challenge sekali pakai; jangan log atau simpan sebagai credential permanen. |
| `cmd/system/shutdown/prepare` | `system.shutdown.prepare` | Tidak ada. ACK mengembalikan `prepare_id`, `challenge`, `valid_seconds` (30). |
| `cmd/system/shutdown/execute` | `system.shutdown.execute` | `{"prepare_id":"<prepare-id>","challenge":"<one-time-challenge>"}` dari ACK prepare. Memerlukan helper `allow_shutdown`, config `shutdown_enabled`, maintenance; Ground juga memerlukan policy remote command. |
| `cmd/operation/get` | `operation.get` | `{"target_id":"<operation-id>"}`; query jurnal, hasil dapat `null` bila ID tidak ditemukan. |
| `cmd/stream/set` | `stream.set` | `{"profile":"balanced"}`; profile valid: `control`, `balanced`, atau `graph_u8`. |

### Contoh command melalui Ground

Gunakan intent HTTP Ground yang terautentikasi (session dan CSRF), bukan membangun envelope MQTT secara manual. Contoh body `POST /api/v2/commands` untuk query read-only:

```json
{"op":"config.get"}
```

Ground memeriksa kesiapan MQTT dan health, mengisi `id`, `sid`, `boot`, deadline, serta revision, lalu publish QoS 1 ke `cmd/config/get`. Respons HTTP `202 REQUESTED` berarti intent masuk antrean lokal, bukan hasil command. Hasil datang melalui `ack/config`; report terbaru dipublish terpisah di `config/reported`. Untuk mencari operation tertentu, body-nya `{"op":"operation.get","target_id":"<operation-id>"}` dan hasilnya melalui `ack/operation`.

Gunakan API/UI Ground untuk semua mutation agar policy, identity, revision, journal, dan challenge tetap dikelola backend. Contoh field mutation pada tabel di atas bukan pesan mandiri dan tidak boleh dipublish tanpa common envelope serta policy yang valid.

Command selain `config.get` dan `operation.get` subject ke identity, boot, deadline, clock trusted, revision dan capability/policy lokal. Perintah mutation tidak diaktifkan hanya karena topic dapat dipublish. Konfigurasi default bersifat read-only. Retained command ditolak. Jika `id` sama dan payload sama, Edge mengembalikan progress/hasil jurnal tanpa menjalankan ulang; `id` sama dengan payload berbeda adalah conflict.
`config.patch` hanya menerima field safe pada tabel. Saat mengganti `center_frequency_hz`, sertakan `vfo0_frequency_hz` dengan target yang sama; batas frekuensi, bandwidth dan gain berasal dari policy helper lokal.

## ACK command dari Edge

`config.*` dibalas di `ack/config`; operation lain di `ack/operation`. ACK QoS 1, tidak retained, expiry 30 s.

```json
{"v":2,"sid":"7a8b9c0d","id":"g01-00001234","status":"APPLIED","t":1790668804123,"rev":8,"result":{"revision":8,"proof":{"center_frequency_hz":"FRESH_DAQ_RF_CENTER","vfo0_frequency_hz":"FRESH_DOA_FREQUENCY"},"persisted":true}}
```

`result` bergantung pada operation. `config.get` mengembalikan `revision` dan `proof`, serta memicu report terbaru ke `config/reported`. `operation.get` mengembalikan `operation` berupa record operation publik atau `null`. Error biasanya berisi `{"error":"ERROR_CODE"}`.
Status yang dikenal jurnal mencakup `ACCEPTED`, `APPLYING`, `VERIFYING`, `REBOOT_SCHEDULED`, `SHUTDOWN_SCHEDULED`, `APPLIED`, `FAILED`, `REJECTED`, `EXPIRED`, `CONFLICT`, `PERSISTED_UNVERIFIED`, `OUTCOME_UNKNOWN`, dan `CANCELLED`.

`PERSISTED_UNVERIFIED` berarti perubahan tersimpan tetapi bukti runtime belum lengkap. `OUTCOME_UNKNOWN` berarti hasil side effect belum diketahui, bukan bukti gagal atau berhasil. Ground tidak boleh mengubah dua status ini menjadi sukses/gagal pasti atau mengulang mutation dengan ID baru tanpa rekonsiliasi.

## ACL minimum Ground

| Credential / role | Hak topic minimum |
|---|---|
| Edge Control | Publish topic operasional, ACK dan availability milik node sendiri; subscribe `sdr/v2/{node_id}/cmd/#` dan `sdr/v2/{node_id}/ground/receipt`. |
| Edge Bulk | Publish hanya `sdr/v2/{node_id}/telemetry/angular`. |
| Ground controller | Subscribe topic operasional/ACK node; publish `sdr/v2/{node_id}/cmd/#` dan `sdr/v2/{node_id}/ground/receipt`. |
| Ground viewer | Subscribe saja; tanpa hak publish command dan tanpa credential controller. |

Prefix, `node_id`, ACL, broker, credential dan konfigurasi Edge/Ground harus cocok. Jangan memberi hak wildcard lintas node bila Ground hanya mengelola satu node. Pada broker yang mengizinkan anonymous CONNECT, ACL tetap wajib membatasi operasi yang dapat dilakukan client.

## Referensi implementasi

- `src/rdf_node/agent.py`: topic Edge, payload JSON, jadwal publish, receipt dan capability.
- `src/rdf_node/ground.py`: subscription, validasi telemetry, assembly Angular, receipt dan command publish.
- `src/rdf_node/control.py`: daftar operasi, envelope validation, idempotency dan ACK.
- `src/rdf_node/codec.py`: format binary RDF2 dan chunk Angular.
- `docs/PROTOCOL.md`: kontrak wire dan batas freshness.
- `docs/MQTT_GROUND.md`: transport, provisioning dan ACL Ground.
