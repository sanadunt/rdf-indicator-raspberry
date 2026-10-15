# MQTT dan Ground companion
Ringkasan lengkap topic, payload, command, settings request, dan contoh Ground: [MQTT_TOPIC_SUMMARY.md](MQTT_TOPIC_SUMMARY.md).

## Pilihan yang paling mudah

Gunakan installer role Ground dan provision script pada README. Paket membuat broker TLS
terpisah pada port8883, receiver pada API8791, bukan mengubah broker/dashboard lama.
Provisioner menunggu alamat PPP10.90.0.1 sehingga startup tidak mengandalkan radio sudah up.
Jika paket mosquitto OS menyalakan broker default1883, itu tetap service OS terpisah;
audit listener/firewall OS tersebut. Script RDF tidak mengklaim mengamankan broker lain.

Bila port8883 sudah dipakai, jangan menumpuk listener. Gunakan broker existing dengan ACL
berikut atau tentukan port alternatif secara terkoordinasi. Provisioner tidak merotasi
credential/certificate yang sudah ada diam-diam; backup terlebih dahulu saat migrasi.

## ACL minimum

Node control:
- publish telemetry/doa, telemetry/diagnostic/doa, telemetry/diagnostic/angular,
  telemetry/health, telemetry/health/detail, state, capabilities, availability,
  settings/reported, ack/config, ack/operation milik node.
- subscribe cmd/# milik node sendiri.

Node bulk:
- publish telemetry/angular saja; tidak memperoleh hak reboot/command.

Ground controller:
- subscribe `telemetry/#`, `state`, `availability`, `settings/reported`, `capabilities`, and `ack/#`.
- publish `cmd/#` milik node sendiri.

Viewer:
- subscribe `telemetry/#`, `state`, `availability`, `settings/reported`, `capabilities`, and `ack/#`.
- tidak publish command dan tidak mendapat credential controller.

Prefix `sdr/v2/uav-01` harus cocok di konfigurasi agent, receiver dan ACL.
Broker harus mempertahankan retained state, capabilities, dan availability. Command, telemetry,
dan `settings/reported` tidak retained. Subscription command memakai Retain Handling 2 dan
Retain As Published; live retained command tetap dapat dikenali kemudian ditolak.

Provisioner memberi Node Control hak tulis `telemetry/diagnostic/doa` dan
`telemetry/diagnostic/angular`. Pada broker yang sudah ada, tambahkan kedua aturan
`topic write <prefix>/telemetry/diagnostic/doa` dan
`topic write <prefix>/telemetry/diagnostic/angular` ke ACL user Node Control; Ground
controller/viewer dengan `telemetry/#` sudah subscribe keduanya. Jangan beri hak diagnostic
itu ke Node Bulk.

## Memakai broker existing

Di Raspberry, atur root-owned `/etc/rdf-node/config.yaml`:

```yaml
mqtt:
  enabled: true
  host: 10.90.0.1
  port: 8883
  tls: true
  ca_file: /etc/rdf-node/credentials/ca.crt
  control_credentials_file: /etc/rdf-node/credentials/control.json
  bulk_credentials_file: /etc/rdf-node/credentials/bulk.json
  keepalive: 15
  reconnect_max: 30
  allow_insecure_loopback: false
```

Ini potongan dalam schema config penuh, bukan pengganti seluruh file. Unknown keys ditolak.
Jika autentikasi broker dipakai, setiap credential JSON berisi `username` dan `password` nyata yang hanya disimpan di perangkat. Pada panel, pasangan kosong mempertahankan credential per-channel yang sudah ada; tanpa kredensial terkonfigurasi, client memakai anonymous CONNECT.
`allow_insecure_loopback` hanya diperlukan untuk tes plaintext pada loopback; `tls: false`
secara eksplisit memilih plaintext untuk endpoint lain. Folder root:rdf-edge0750, files root:rdf-edge0640.
CA public boleh dibaca service; private key CA tetap di mesin provisioning, tidak disalin ke Raspberry.

Pada receiver Ubuntu, gunakan `/etc/rdf-ground/config.yaml`, MQTT endpoint loopback8883,
CA yang sama dan credential controller sendiri. Start:

```bash
sudo systemctl enable --now rdf-ground.service
```

TLS mode mewajibkan certificate chain, SAN hostname/IP, dan waktu mesin valid. `tls: false`
memilih MQTT/TCP atau `ws://` tanpa enkripsi; credential dan payload dapat dibaca di jaringan.
Gunakan hanya pada link privat/tepercaya yang disetujui. `allow_insecure_loopback` tetap mewajibkan
opt-in eksplisit untuk tujuan loopback plaintext, tetapi bukan untuk endpoint remote yang dipilih
secara eksplisit. Tidak ada mode TLS yang mematikan verifikasi.

## Transport and certificate trust

The Edge and Ground clients support `mqtt.transport: tcp` (default) or `websocket`, with
`mqtt.websocket_path` (default `/mqtt`). `tls: true` (the default) uses verified TLS for either
transport: TCP/TLS or WSS. `tls: false` uses plaintext TCP or `ws://`. The Ground provisioner
still creates only the TCP/TLS listener on 8883; configure a matching plaintext TCP or WebSocket
listener, path, and ACL separately when that is the required deployment.

With `tls: true` and `ca_file: null`, Python uses the OS default CA store and keeps certificate
and hostname verification enabled. A broker certificate issued by a public CA can therefore work
without installing a private CA bundle. Set `ca_file` to the provisioned CA bundle for a private
or self-signed broker certificate. Trusting system CAs does not make a private certificate trusted.

## Broker cadangan (mirror LAN/online)

Edge dapat mengirim salinan semua publish ke satu broker MQTT kedua, misalnya broker di LAN
atau broker online. Broker utama tetap jalur PPP/T900 ke Ground. Broker cadangan memakai client
dan koneksi sendiri, tidak bergantung pada PPP/T900, dan tetap menerima data saat PPP, T900, atau
broker utama mati. Ini mode **mirror**, bukan failover: saat kedua broker READY, telemetry dan
state dikirim ke keduanya. Menyimpan pengaturan cadangan tidak me-restart client utama, dan
sebaliknya; client lama dihentikan di thread terpisah sehingga koneksi yang macet (DNS, TCP, TLS)
pada satu broker tidak menjeda publish ke broker lainnya. Satu pengecualian: mengubah Client ID dasar
di `Data > Atur` juga menyambung ulang client cadangan dengan ID baru `{client_id}-backup`. Bila `mqtt.enabled: false`, broker cadangan tetap dapat berjalan sendiri. Receiver
Ground bawaan hanya tersambung ke broker utama; consumer pada broker cadangan (dashboard LAN, dsb.)
dikelola terpisah. Mode DEMO tidak pernah membuka koneksi cadangan.

| Hal | Broker utama | Broker cadangan |
|---|---|---|
| Client | `{client_id}-control` dan `{client_id}-bulk` | Satu client `{client_id}-backup`; `{client_id}` adalah Client ID dasar broker utama |
| Prefix/topic | `sdr/v2/{node_id}/...` | Identik |
| Budget | Control 850 B/s + Bulk 350 B/s (agregat maks. 1.500 B/s) | `mqtt.backup.budget_bytes_s` (default 8.000 B/s), di luar agregat T900 |
| Command | Selalu subscribe `cmd/*` | Hanya bila `commands: true` (`Terima perintah`) |
| Last Will | `availability` offline, retained | Sama, per koneksi broker cadangan |

### Konfigurasi `mqtt.backup`

```yaml
mqtt:
  backup:
    enabled: false
    host: null
    port: 8883
    transport: tcp
    websocket_path: /mqtt
    tls: true
    ca_file: null
    credentials_file: null
    commands: true
    budget_bytes_s: 8000
```

| Key | Default | Makna |
|---|---|---|
| `enabled` | `false` | Boolean; menyalakan client cadangan. |
| `host` | `null` | IP atau hostname broker cadangan. Wajib bila `enabled: true` (`MQTT_BACKUP_HOST_REQUIRED`). |
| `port` | `8883` | Integer 1..65535. |
| `transport` | `tcp` | `tcp` atau `websocket`. |
| `websocket_path` | `/mqtt` | Path WebSocket; tetap divalidasi walaupun transport `tcp`. |
| `tls` | `true` | `false` memilih MQTT/TCP atau `ws://` tanpa enkripsi; credential dan payload terbaca di jaringan. Host loopback plaintext tetap memerlukan `mqtt.allow_insecure_loopback: true`. |
| `ca_file` | `null` | `null` memakai trust store sistem (broker dengan sertifikat CA publik). Path absolut berarti hanya CA pada file itu yang dipercaya (CA privat/self-signed). Tidak mewarisi `mqtt.ca_file` broker utama. |
| `credentials_file` | `null` | `null` berarti anonymous CONNECT. Path absolut ke JSON berisi tepat `username` dan `password` non-kosong. |
| `commands` | `true` | Subscribe topic `cmd/*` di broker cadangan. `false` membuat cadangan publish-only. |
| `budget_bytes_s` | `8000` | Token bucket client cadangan, integer 1000..200000 B/s. Tidak masuk pemeriksaan `AGGREGATE_BUDGET_TOO_HIGH`. |

`keepalive` dan `reconnect_max` mengikuti `mqtt.keepalive` dan `mqtt.reconnect_max`. Schema tetap
strict: key tidak dikenal ditolak (`mqtt.backup.<key>: UNKNOWN_CONFIG_KEY`), tipe YAML yang salah
ditolak dengan prefix path (`INTEGER_REQUIRED`, `STRING_REQUIRED`, `STRING_OR_NULL_REQUIRED`,
`BOOLEAN_REQUIRED`), dan nilai salah ditolak dengan `BOOLEAN_REQUIRED`, `INTEGER_OUT_OF_RANGE`,
`INVALID_MQTT_TRANSPORT`, `INVALID_MQTT_WEBSOCKET_PATH`, `INVALID_MQTT_HOST`,
`MQTT_BACKUP_HOST_REQUIRED`, `ABSOLUTE_PATH_REQUIRED`, atau
`PLAINTEXT_ONLY_EXPLICIT_LOOPBACK_TEST`. Perubahan `config.yaml` berlaku setelah
`sudo systemctl restart rdf-edge.service`.

### Panel `Data > Cadangan` dan prioritas pengaturan

Tombol `Cadangan` berada di sebelah `Atur` pada halaman Data dan memerlukan login PIN Admin yang
sama. Dialog **Broker cadangan** berisi:

| Field | Pilihan / isi |
|---|---|
| Status cadangan | `Nonaktif` / `Aktif` |
| Transport MQTT | `MQTT/TCP` / `WebSocket (WS)` |
| Enkripsi TLS | `Aktif` / `Nonaktif` (nonaktif menampilkan peringatan jaringan tepercaya) |
| IP / host | Wajib bila Aktif; boleh kosong bila Nonaktif |
| Port | 1..65535 |
| Path WebSocket | Hanya tampil untuk WebSocket |
| Terima perintah | `Aktif` / `Nonaktif` (lihat bagian perintah di bawah) |
| Akun | `Tanpa akun` / `Pakai akun` |
| User, Pass | Hanya untuk `Pakai akun`; kosongkan keduanya untuk memakai ulang akun yang ada (akun yang sedang dipakai, akun panel, lalu `credentials_file` dari `config.yaml`). Placeholder `tersimpan` atau `dari config` menunjukkan akun mana yang akan dipakai |

Client ID cadangan ditampilkan read-only. Petunjuk TLS menyebut mode trust (CA sistem, CA khusus
`ca_file`, CA khusus tidak ditemukan, atau plaintext). `ca_file`, `credentials_file`, dan
`budget_bytes_s` hanya dapat diatur di `config.yaml`. Simpan menerapkan perubahan langsung: client
cadangan lama dihentikan dan client baru dibuat tanpa memutus broker utama. Saat cadangan
dinonaktifkan atau dipindah ke broker lain, Edge lebih dulu mengirim `availability` retained
`{online:false, reason:"BACKUP_DISABLED"|"BACKUP_RECONFIGURED"}` ke broker lama (bila masih
tersambung), supaya consumer di sana tidak melihat node tetap online.

Panel menyimpan pengaturan di `state_dir` (default `/var/lib/rdf-node`):

- `mqtt-ui-backup-settings.json`: `{schema:1, enabled, host, port, transport, websocket_path,
  tls, commands, auth}`, dengan `auth` = `none`, `account`, atau `config`; mode 0600.
- `mqtt-ui-backup-account.json`: `{username, password}` dari `Pakai akun`; mode 0600.

Saat start, file panel yang ada dan valid **menggantikan** `enabled`, `host`, `port`,
`transport`, `websocket_path`, `tls`, `commands`, dan pilihan akun dari `config.yaml`, sama seperti
prioritas pengaturan broker utama. `auth: config` berarti memakai `mqtt.backup.credentials_file`;
panel menampilkannya sebagai `Pakai akun` dengan placeholder `dari config`. File yang tidak ada,
rusak, schema berbeda, atau berstatus aktif tetapi CA/credential-nya tidak tersedia diabaikan, lalu
nilai `config.yaml` dipakai. Untuk kembali ke `config.yaml`, hapus kedua file panel tersebut lalu
restart `rdf-edge.service`. Baris `MQTT cadangan` pada `rdf-node doctor` hanya membaca
`mqtt.backup.enabled` dari `config.yaml`; status efektif ada di panel atau `link.mqtt_backup`.

### TLS dan trust sertifikat

TLS cadangan memakai aturan yang sama dengan broker utama: TLS 1.2+, verifikasi rantai sertifikat
dan hostname/IP, tanpa mode bypass. Dengan `ca_file: null`, trust store OS dipakai sehingga broker
online bersertifikat CA publik dapat langsung dipakai. Broker LAN self-signed atau ber-CA privat
memerlukan `mqtt.backup.ca_file` berisi CA/sertifikat tersebut, dan sertifikat broker harus memuat
SAN yang cocok dengan host/IP yang diisi. Bila `ca_file` diisi, hanya CA itu yang dipercaya.
Alternatifnya `tls: false`, hanya pada LAN tepercaya. Bila `ca_file` diatur tetapi filenya tidak
ada, panel menolak mengaktifkan TLS cadangan dengan `MQTT_BACKUP_TLS_CA_UNAVAILABLE`.

### Syarat broker cadangan

- Broker harus mendukung **MQTT 5** (Clean Start, Session Expiry 0, QoS 0/1, retained, message
  expiry, subscription options). Client tidak memiliki fallback MQTT 3.1.1.
- ACL harus mengizinkan publish semua topic Edge di bawah prefix node: `telemetry/doa`,
  `telemetry/diagnostic/doa`, `telemetry/diagnostic/angular`, `telemetry/health`,
  `telemetry/health/detail`, `telemetry/angular`, `state`, `capabilities`, `availability`,
  `settings/reported`, `ack/config`, dan `ack/operation`. Satu credential cadangan memegang semua
  hak itu. PUBACK yang ditolak untuk topic QoS 1 memutus sesi (`PUBLISH_REJECTED`) dan client
  reconnect.
- Bila `Terima perintah` aktif, ACL juga harus mengizinkan subscribe ke topic `cmd/*` node
  (cukup `<prefix>/cmd/#`). Jika broker menolak subscribe, SUBACK berisi error, client mencatat
  `SUBACK_REJECTED`, tidak pernah READY, dan tidak mengirim data ke broker itu. Untuk broker dengan
  ACL publish-only, nonaktifkan `Terima perintah`.
- Broker harus menyimpan retained `state`, `capabilities`, dan `availability`.
- Client ID `{client_id}-backup` harus unik pada broker itu.
- Jangan bridge broker cadangan ke broker utama untuk prefix node yang sama. Edge sudah mengirim ke
  keduanya; bridge membuat Ground menerima duplikat dan command dapat tiba dua kali (jurnal
  menahan ID yang sama, tetapi ACK ikut terkirim ulang).

### Perintah dari broker cadangan

Dengan `Terima perintah` aktif, Edge subscribe topic `cmd/*` yang sama di broker cadangan.
Command dari broker itu diproses **persis seperti command Ground**: actor `ground-controller`,
tanpa approval root per aksi, dengan guard session/boot, expiry, clock, revision, health, jurnal,
dan target helper yang sama, serta satu mutasi aktif yang dipakai bersama dengan broker utama.
Siapa pun yang boleh publish ke `cmd/#` node di broker cadangan dapat meminta reboot, shutdown,
restart PPP, atau patch settings. Batasi ACL publish `cmd/#` di broker cadangan hanya untuk
controller tepercaya, jangan izinkan anonymous publish, dan jangan aktifkan `Terima perintah` pada
broker publik/bersama. Untuk broker yang hanya menampilkan data, matikan `Terima perintah`.

Routing ACK:

- Command dari broker cadangan dijawab (`ack/config`, `ack/operation`) hanya ke broker cadangan,
  sehingga tidak memakai budget T900.
- Command dari broker utama atau dari panel Edge lokal dijawab ke semua broker yang READY.
- `config.get` dari broker cadangan memerlukan TLS aktif pada broker cadangan
  (`TLS_REQUIRED_FOR_SETTINGS_REPORT` bila tidak). `settings/reported` hanya dikirim ke broker yang
  meminta, tidak di-mirror.

Command cadangan memakai paling banyak separuh antrean inbound (16 dari 32 slot); kelebihannya dibuang
tanpa ACK agar command Ground tetap mendapat tempat. Batas `config.get`/`operation.get` satu per
detik dihitung per broker. Command yang masih antre dari sesi cadangan lama dibuang saat pengaturan
cadangan disimpan ulang.

### Jadwal dan budget cadangan

Health, detail, DoA, diagnostic DoA, dan `state` dikirim pada jadwal yang sama ke semua broker
READY. `availability` online dan `capabilities` dikirim saat masing-masing koneksi (re)connect;
reconnect cadangan juga mengirim ulang `state` retained ke broker cadangan tanpa mereset
stabilisasi broker utama. Semua topic cadangan berbagi satu outbox dan satu budget
`budget_bytes_s`.

Angular normal ke cadangan memakai jadwal sendiri:
`max(interval_profile, (payload_bytes + topic_bytes + 160) / backup.budget_bytes_s)`. Dengan default
8.000 B/s, interval profile yang menentukan (`balanced` 4 s, `graph_u8` 2 s). Angular cadangan tidak
dipause oleh alasan khusus jalur T900 (`BULK_NOT_READY`, `CONTROL_BACKLOG`, `WAIT_STABLE`) dan
tidak menunggu `resume_stable_seconds`. Alasan level source (`PROFILE_CONTROL`,
`SOURCE_NOT_ELIGIBLE`, `CONTROL_IN_PROGRESS`) tetap memblokir Angular di kedua broker. Selama
alasan source memblokir dan profile bukan `control`, cadangan menerima
`telemetry/diagnostic/angular` dengan interval minimum 6 s selama detection tidak valid (termasuk
UNVERIFIED) atau 30 s selama detection valid tetapi Angular diblokir (misalnya command berjalan),
diperpanjang sesuai ukuran payload dan budget cadangan.

### Routing jaringan

Edge tidak memilih interface untuk broker cadangan; koneksi mengikuti routing OS. Broker LAN
biasanya lewat Ethernet/Wi-Fi. Broker online memakai default route OS. Jika route ke host
cadangan lewat PPP (default route ke `ppp0`, atau host di subnet PPP), trafik cadangan ikut
memakai bandwidth T900 dan budget cadangan **tidak** dihitung dalam batas agregat T900. Periksa
dengan `ip route get <host-cadangan>` sebelum mengaktifkan, dan pastikan route keluar lewat LAN
atau uplink internet, bukan PPP. Opsi pppd `nodefaultroute` mencegah PPP menjadi default route;
lihat [T900_PPP.md](T900_PPP.md#65-broker-cadangan-jangan-lewat-ppp0).

### Status cadangan

`GET /api/v2/snapshot` dan `GET /api/v2/link` memuat `link.mqtt_backup` (tanpa host atau secret):
`enabled`, `commands`, `state` (`DISABLED`, `CONNECTING`, `CONNECTED`, `ERROR`), `ready`, `error`,
`depth`, `pending_age_ms`, `bulk_pause`, `angular_aborted`, dan `topic_delivery`. Saat client aktif,
objek itu juga memuat `generation`, `tx_bytes`, `rx_bytes`, `reconnects`, `queue_age_ms`, dan
statistik outbox.
`topic_delivery` adalah peta terpisah per suffix topic untuk broker cadangan; `mqtt_topic_delivery`
tetap hanya untuk broker utama. `config.mqtt_backup_configured` bernilai `true` bila cadangan aktif
dengan host. Panel menampilkan `UTAMA` dan `CADANGAN` di ringkasan Data dan baris `MQTT cadangan` di
tab Link. Cadangan aktif yang belum CONNECTED hanya memberi peringatan; status MQTT di halaman Utama
tetap hanya untuk broker utama.

## Readiness dan assurance

CONNECTED setelah CONNACK; READY sesudah semua subscription SUBACK diterima. Penolakan
ACL saat subscribe/publish membuat error, bukan indikator hijau diam-diam.

Maksimum satu outgoing PUBLISH aktif per koneksi. Local latest-value outbox tidak
mengumpulkan history offline. New connection memakai clean start, sessionexpiry0,
subscribe ulang, bootstrap state/capability lalu telemetry. Edge tidak mengirim settings saat
bootstrap; Ground meminta `settings/reported` hanya melalui `config.get`.

Command QoS 1 acknowledgement dari broker tidak sama dengan hasil tindakan. Local send completion
QoS 0 tidak membuktikan Ground menerima. Edge tidak menerima konfirmasi aplikasi atas telemetry;
PUBACK hanya membuktikan broker mengakui PUBLISH QoS 1.

Bulk reset/reconnect tidak merestart control atau engine. Dua koneksi tetap berbagi TCP/IP/
PPP/radio. Token bucket dan coalescing mengurangi burst, bukan menjamin latency RF.
Default pause setelah command/source/link issue dan resume20s stabil dipertahankan.
Angular utama dipause `CONTROL_BACKLOG` hanya bila pesan tertua yang masih menunggu di outbox
Control (bukan yang sedang dikirim) berumur lebih dari 3 s (`queue_age_ms`), atau PUBLISH QoS 1
yang sedang dikirim menunggu PUBACK lebih dari 2,5 s (`pending_age_ms`). PUBACK yang sedikit
lambat pada link T900 (di bawah 2,5 s) tidak lagi memicu pause, tetapi Bulk tetap mengalah sebelum
backlog Control menumpuk.

### Reconnect, keepalive, dan pemulihan PPP

Backoff dan keepalive berlaku untuk semua client MQTT RDF Node: Control, Bulk, dan cadangan di
Edge, serta client receiver Ground. Client cadangan memakai `mqtt.keepalive` dan
`mqtt.reconnect_max` yang sama.

- **Backoff:** setelah koneksi putus atau gagal, client menunggu 1 s, lalu berlipat setiap
  percobaan gagal sampai `mqtt.reconnect_max` (default 30 s), ditambah jitter kecil. Sesi yang
  bertahan minimal 20 s setelah CONNACK mengembalikan jeda ke 1 s. Sesi yang putus lebih cepat
  (misalnya SUBACK/PUBACK ditolak, paket terlalu besar, takeover Client ID) atau tidak pernah
  tersambung terus menaikkan jeda, sehingga tidak ada reconnect dan handshake TLS tiap detik
  lewat radio.
- **Keepalive:** PINGREQ dikirim bila selama `mqtt.keepalive` detik (default 15) client tidak
  mengirim apa pun, **atau** tidak menerima data apa pun dari broker. Jadi link mati tetap
  terdeteksi walaupun telemetry QoS 0 terus terkirim. Bila PINGRESP tidak datang dalam
  `keepalive` detik setelah PINGREQ, sesi direset dengan `PING_TIMEOUT` lalu reconnect. Deteksi
  link mati butuh sekitar dua kali `keepalive`.
- **Link lambat yang masih bekerja:** bila T900 membawa lebih sedikit dari beban yang ditawarkan,
  PINGREQ antre di belakang data client sendiri dan PINGRESP datang terlambat. Client tidak
  mereset sesi selama TCP masih maju, yaitu kernel masih memegang data yang belum di-ACK (atau
  PINGREQ belum diserahkan ke kernel) dan broker mengirim ACK TCP dalam `keepalive` detik terakhir.
  Toleransi ini paling lama 4 kali `keepalive` (default 60 s) sejak PINGREQ. Link mati tidak
  mengirim ACK TCP, jadi tetap direset `keepalive` detik setelah PINGREQ, atau `keepalive` detik
  setelah ACK TCP terakhir bila itu lebih lambat. Berlaku
  untuk TCP, TLS, dan WebSocket; bila info socket Linux (`TIOCOUTQ`, `TCP_INFO`) tidak terbaca,
  berlaku aturan tanpa toleransi. Tanpa toleransi ini, link marginal dapat memicu `PING_TIMEOUT`
  palsu, Last Will `online:false`, dan status `MQTT utama` yang berkedip.
- **Kick setelah PPP kembali:** saat interface PPP berubah dari tidak `UP` menjadi `UP`, atau ping
  peer kembali dibalas, Agent memanggil `kick()` pada client utama (Control dan Bulk). Client yang
  belum tersambung langsung mencoba lagi tanpa menunggu sisa backoff, dan backoff kembali ke awal.
  Client yang sudah tersambung tidak terpengaruh. Client cadangan tidak di-kick karena tidak lewat
  PPP.
- Setelah kedua client utama READY, Angular utama menunggu `resume_stable_seconds` (default 20 s)
  sebelum mengalir lagi (`WAIT_STABLE`).

Restart PPP dari panel Edge dan pemantau pemulihannya dijelaskan di [T900_PPP.md](T900_PPP.md).

## Uji penerimaan nyata

Setelah MQTT dikonfigurasi:
1. Pastikan kedua client READY pada tab Link dan health receiver bertambah dengan DAQ yang benar.
2. Gunakan `Refresh config` secara eksplisit. TLS harus aktif; pastikan `cmd/config/get` diikuti
   `settings/reported` yang cocok dengan session dan request ID. Report tidak muncul otomatis.
3. Validasi output source lalu lihat DoA serta satu PUBLISH `telemetry/angular` berformat JSON,
   dengan `encoding="json"` dan tepat 360 angka pada `values`.
4. Jika authority/angle approval belum ada tetapi seluruh gate integritas data lulus, pastikan Edge tetap mengirim `telemetry/doa` (`ok=0`, RAW) dan `telemetry/angular` (flags parsial) pada topic normal. Ground menampilkan keduanya sebagai `UNVERIFIED`; detection tetap invalid.
5. Pastikan `telemetry/diagnostic/doa` berulang setiap 3 detik selama XML tersedia, termasuk saat sampel sama dan DoA normal valid; data tetap `UNVERIFIED`. Saat gate normal memblokir, pastikan panel utama Edge menampilkan sudut raw dan frekuensi XML secara terpisah.
6. Saat `DOA_value.html` berisi record 360 sampel dan gate integritas source gagal (misalnya service/DAQ berhenti atau source stale), pastikan `telemetry/diagnostic/angular` tiba. Interval 6 detik saat source tidak eligible dan 30 detik saat source eligible tetapi Bulk terblokir adalah minimum; ukuran JSON dan headroom Control dapat memperpanjangnya. Saat Bulk normal mengalir, tidak ada duplikasi. Periksa timestamp sumber dan 360 values di `/api/v2/angular/diagnostic/latest`; kandidat tetap `UNVERIFIED`, tidak mengubah detection LIVE. Profile `control` sengaja tidak mengirim array diagnostik.
7. Putuskan radio sementara dengan jalur management aman, lihat stale/lost, reconnect tanpa burst data lama; jangan reset SDR. Catat waktu sampai MQTT Control READY setelah PPP kembali (kick tanpa menunggu backoff) dan waktu sampai Angular kembali.
8. Jalankan command config/read-only, lalu subset write yang sudah di-approve.
9. Ukur actual PPP counters, capture MQTT/TCP terkontrol, latency dan loss >30menit.
10. Cold boot kedua host, radio belum terpasang, kemudian radio hadir; periksa recovery.
11. Bila broker cadangan dipakai: pastikan `ip route get <host-cadangan>` tidak lewat PPP, baris
    `MQTT cadangan` CONNECTED, dan subscriber di broker cadangan menerima topic yang sama. Putuskan
    PPP/T900 secara terkontrol dan pastikan broker cadangan tetap menerima health/DoA. Bila
    `Terima perintah` aktif, uji command read-only dari broker cadangan dan pastikan ACK hanya
    muncul di broker cadangan; uji juga bahwa publisher yang tidak diizinkan ACL ditolak broker.
12. Bila approval `--ppp-restart` dipakai: tekan Restart T900 di panel Edge dan pastikan kartu
    progres melewati `PPP turun/naik`, `PPP UP`, `Ping Ground`, `MQTT utama`, lalu
    **Tersambung ke Ground ✓** (`APPLIED`). Uji juga saat PPP sudah putus total; catat waktu tiap
    langkah sesuai [T900_PPP.md](T900_PPP.md).

Paket build diuji dengan broker fixture loopback, bukan uji Mosquitto/T900 nyata. Hal yang sama
berlaku untuk broker cadangan: interoperabilitas dengan broker LAN/online tertentu tetap harus
diuji di perangkat.
