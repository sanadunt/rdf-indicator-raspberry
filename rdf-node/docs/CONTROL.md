# Kontrol RF, lifecycle, reboot dan shutdown

## Ground direct commands dan approval Edge lokal

Installer tetap menyediakan panel Edge read-only secara default. Helper root hanya mendengar
Unix socket lokal; credential peer diverifikasi dengan SO_PEERCRED. Tidak ada shell command,
arbitrary path, atau nama service dalam payload MQTT. Konfigurasi helper root-owned dan tidak
writable oleh group/world.

Command write Ground yang didukung berjalan tanpa `controls approve --remote` dan tanpa approval
root per aksi. Config lama `remote_commands_enabled` dan `allow_remote_control` masih dibaca agar
instalasi lama valid, tetapi tidak membatasi actor Ground. Flag approval lainnya mengatur aksi
lokal Edge; CLI `--remote` sudah dihapus, sedangkan `--reboot` tetap mengatur gate reboot lokal.

Target fixed tetap harus disiapkan:

- `controls approve --settings` satu kali menetapkan path settings dan single-writer confirmation.
  Ground `config.patch` tetap butuh keduanya, allowlist field, dan compare-digest; flag lokal
  `allow_config` tidak menjadi gate Ground.
- `controls approve --lifecycle` mengaudit unit SDR/watchdog dan memasang stop-intent guard.
  Ground processing/restart hanya memakai unit audited tersebut; `allow_lifecycle` tetap gate
  aksi lokal Edge.
- PPP selalu hanya menargetkan `t900-ppp.service`; helper memeriksa unit pada setiap request.
  Restart PPP memakai jalur worker sendiri, terpisah dari mutasi SDR (lihat
  [Restart layanan PPP T900](#restart-layanan-ppp-t900)).
- Reboot/shutdown tetap memakai Prepare/Execute dan challenge sekali pakai. Ground UI meminta
  konfirmasi; jalur MQTT tidak mengikat command ke sesi UI tersebut.

Contoh setup target sekaligus approval lokal:

```bash
sudo rdf-node controls approve --settings --lifecycle --reboot --shutdown --ppp-restart
```

CLI meminta `APPROVE`; `--settings` juga meminta `SINGLE`, `--lifecycle` meminta `AUDITED`,
`--reboot` meminta `REBOOT`, `--shutdown` meminta `SHUTDOWN`, dan `--ppp-restart` meminta
`T900 PPP RESTART` setelah preflight unit. Flag ini menyimpan approval root per aksi untuk panel
Edge lokal; persetujuan tetap berlaku lintas reboot dan tidak memakai lease waktu atau `sudo`
per aksi. Panel tetap memerlukan sesi Admin PIN dan konfirmasi layar untuk aksi destruktif.

Ground tidak membuat atau mengubah approval root lokal. Menjalankan `controls approve`
tanpa flag tidak mencabut akses Ground. Batasi publish ke subtree `cmd/#` pada ACL broker dan
cabut credential Ground di broker untuk menutup akses remote; jangan mengandalkan tombol UI atau
remote grant Edge.

Ground PIN dan CSRF melindungi API HTTP lokal, bukan publisher MQTT. Edge menetapkan actor
`ground-controller` untuk command MQTT yang diterimanya, tetapi tidak mengautentikasi publisher
atau mengikatnya ke login Ground. Publisher mana pun yang diizinkan ACL broker dapat mengirim
command; link private PPP/T900 sendiri bukan autentikasi. TLS memverifikasi broker dan melindungi
transport, tetapi otorisasi publisher tetap memerlukan credential broker dan ACL per topic.
Jangan izinkan anonymous publish ke topic command.

### Command dari broker cadangan

Bila broker cadangan aktif dengan `Terima perintah` (`mqtt.backup.commands: true`, default), Edge
juga subscribe topic `cmd/*` di broker itu. Command yang diterima dari sana masuk ke
CommandManager yang sama dengan actor `ground-controller`: tanpa remote grant atau approval root
per aksi, dengan validasi session/boot, expiry, clock, revision, health, jurnal, dan target helper
fixed yang sama. Satu mutasi aktif berlaku bersama untuk broker utama, broker cadangan, dan panel
lokal; mutasi lain saat itu ditolak `BUSY`. Pengecualiannya `ppp.restart`: ia memakai jalur PPP
sendiri yang juga dipakai bersama ketiga asal itu, dengan satu restart aktif dan jeda minimum 15 s.

Akibatnya, setiap publisher yang diizinkan ACL broker cadangan ke `<prefix>/cmd/#` dapat meminta
patch settings, start/stop/restart SDR, restart PPP, reboot, atau shutdown. Batasi ACL publish
`cmd/#` di broker cadangan hanya untuk controller tepercaya, jangan izinkan anonymous publish, dan
jangan aktifkan `Terima perintah` pada broker publik atau bersama. Untuk broker yang hanya
menerima data, pilih `Terima perintah: Nonaktif` atau `commands: false`; Edge lalu tidak subscribe
command di broker itu. Menutup seluruh akses write remote berarti mencabut ACL `cmd/#` di broker
utama **dan**, untuk broker cadangan, mencabut ACL `cmd/#`-nya atau menonaktifkan `Terima perintah`.
Menonaktifkan command cadangan saja tidak menutup jalur command Ground lewat broker utama.

ACK command dari broker cadangan hanya dikirim ke broker cadangan; ACK command dari broker utama
atau panel lokal dikirim ke semua broker yang READY. `config.get` dari broker utama dijawab dengan
atau tanpa TLS. `config.get` dari broker cadangan memerlukan TLS pada broker cadangan atau opt-in
`mqtt.backup.settings_report_plaintext: true`, dan `settings/reported`-nya hanya dikirim ke broker
cadangan. Command
cadangan memakai paling banyak separuh antrean inbound; kelebihannya dibuang tanpa ACK. Command
yang masih antre dari sesi cadangan lama dibuang saat pengaturan cadangan disimpan ulang.

## Supported settings

| Contract | Native | Batas helper |
|---|---|---|
| `center_frequency_hz`, `vfo0_frequency_hz` | `center_freq`, `vfo_freq_0` | Integer 24,000,000..1,766,000,000 Hz; kedua field wajib disertakan dengan nilai sama. |
| `vfo0_bandwidth_hz` | `vfo_bw_0` | Integer 100..2,400,000 Hz. |
| `gain_db` | `uniform_gain` | Finite -10..100 dB dan selisih <0.001 dari tabel gain helper. |
| `vfo0_squelch_db` | `vfo_squelch_0` | Finite -200..50 dB. |

MVP hanya satu output VFO0 aktif. Nilai gain yang diterima helper: `0.0, 0.9, 1.4, 2.7,
3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7, 32.8,
33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6`. Batas helper
tidak mengkalibrasi radio. Audit unit/native API, passband, dan bandwidth engine sebelum
menetapkan single-writer target.

Perubahan algoritma, antenna geometry, sample rate, calibration, system reboot lewat field
settings generic, endpoint eksternal, dan secret tidak didukung. Aktifkan hanya command
khusus yang benar-benar ada dalam capabilities.

## Write dan proof

1. Validasi auth channel, topic/op, session, boot, expiry, clock dan revision.
2. Maksimum satu mutasi aktif (restart PPP punya jalur sendiri), tulis intent ke jurnal durable
   dahulu.
3. Helper membaca path yang sudah di-approve dengan no-follow directory/file checks.
4. Raw digest dibandingkan, allowlist patch diterapkan, unknown internal settings dipertahankan.
5. Backup raw disimpan root-only di `/var/lib/rdf-node-control/settings-backup.json`,
   BUKAN di share HTTP/telemetry.
6. Temporary file, fsync, CAS read ulang, atomic replace. `ext_upd_flag` diset untuk watcher.
7. Worker membaca safe state dan evidence runtime; MQTT loop tidak diblokir oleh penantian.

Atomic replace melindungi partial file tetapi tidak membuat semua writer kooperatif.
Ketentuan single writer tetap harus dipenuhi. GUI engine lain yang masih menulis dapat
menimbulkan conflict/lost update; jangan approve sebelum menyelaraskannya.

Bukti APPLIED field-specific:
- VFO frequency: record DoA baru yang sesuai target.
- Center: status DAQ baru dengan rf_center_frequency_hz/rf_center_freq sesuai target.
- Gain: status DAQ baru yang menyertakan gain_db sesuai target.
- Bandwidth/squelch: source native yang tersedia belum menyediakan bukti runtime yang
  tervalidasi; file write dapat menghasilkan PERSISTED_UNVERIFIED, bukan APPLIED palsu.

Pada native status yang tidak menyediakan center/gain evidence, retune tetap dapat
terpersist dan engine watcher dapat menerapkannya, tetapi aplikasi tidak mengklaim telah
memverifikasi seluruh efek. Ini terlihat pada panel/ACK. Freshness DoA tetap diperiksa.

## Restart layanan PPP T900

RDF Node tidak memasang, mengubah konfigurasi, atau mengambil ownership PPP. `ppp.restart`
selalu menargetkan fixed unit `t900-ppp.service`; tidak ada nama unit atau command dari payload.
Prosedur operator, arti progres, dan pengerasan link kedua sisi ada di [T900_PPP.md](T900_PPP.md).

### Gate per asal

| Asal | Gate |
|---|---|
| Panel Edge lokal (`local-admin`) | Sesi PIN Admin + CSRF dan konfirmasi layar; `runtime_mode: controlled`, `control.ppp_restart_enabled`, dan helper `allow_ppp_restart` (`sudo rdf-node controls approve --ppp-restart`). Bila belum lengkap: `CAPABILITY_DISABLED`. Panel tidak mengirim field operasi tambahan; `base_rev` diabaikan dan gate `CLOCK_UNTRUSTED` tidak berlaku, karena Pi tanpa RTC sering mendapat NTP lewat PPP yang justru sedang putus. Deadline memakai jam host yang sama. |
| Ground / broker cadangan (`ground-controller`) | Tanpa remote grant atau approval root. Session/boot, expiry, clock, dan `base_rev` tetap diperiksa seperti command Ground lain; di DEMO ditolak `DEMO_NO_HARDWARE_CONTROL`. |

Capability `ppp_restart` melaporkan gate lokal; `remote_ppp_restart` hanya berarti helper tersedia
di luar DEMO, bukan bukti unit siap.

### Jalur sendiri dan jeda minimum

- `ppp.restart` dari semua asal berjalan di jalur worker PPP kecil, terpisah dari worker mutasi
  SDR. Verifikasi Start/Restart SDR yang sedang berjalan (`BUSY`, sampai
  `control.start_timeout_seconds`) tidak memblokir restart PPP, dan restart PPP tidak membuat
  mutasi SDR ditolak `BUSY`.
- Hanya satu restart PPP aktif. Permintaan baru ditolak `PPP_RESTART_IN_PROGRESS` selama permintaan
  sebelumnya masih dijalankan atau pemulihannya masih dipantau (maks. 120 s).
- Permintaan baru kurang dari 15 s setelah permintaan PPP terakhir yang diterima ditolak
  `PPP_RESTART_TOO_SOON`, dari asal mana pun.
- Penolakan sebelum jurnal hanya di-ACK `REJECTED`; untuk asal remote, penolakan juga dicatat di
  `remote_rejects` snapshot Edge.

### Pemeriksaan helper

Pada setiap request helper membaca `LoadState`, `ActiveState`, `SubState`, dan `Job` lewat
`systemctl show`. Pemeriksaan dan pemanggilan tetap
`/usr/bin/systemctl --no-block restart t900-ppp.service` berjalan di bawah lock helper yang sama.
Untuk unit tanpa job, systemd menulis `Job=` dengan nilai kosong (bukan `Job=0`); helper
memperlakukan `Job=` kosong atau `0` sebagai tidak ada job, dan nomor job lain sebagai job antre.

| Keadaan unit | Ground/cadangan | Panel lokal |
|---|---|---|
| `active` | diterima | diterima |
| `failed` | diterima | diterima |
| `activating` dengan SubState `auto-restart` | diterima | diterima |
| `inactive` | `PPP_SERVICE_NOT_ACTIVE` | diterima (`restart` menyalakan unit yang berhenti) |
| `activating` lain, `deactivating`, atau state lain | `PPP_SERVICE_NOT_ACTIVE` | `PPP_SERVICE_NOT_ACTIVE` |
| `Job` berisi nomor job (job antre) | `PPP_RESTART_PENDING` | `PPP_RESTART_PENDING` |
| Unit tidak dimuat | `PPP_SERVICE_NOT_LOADED` | `PPP_SERVICE_NOT_LOADED` |
| `systemctl show` gagal, `LoadState`/`ActiveState` kosong, atau baris `Job` tidak ada | `PPP_SERVICE_STATUS_UNKNOWN` | `PPP_SERVICE_STATUS_UNKNOWN` |

`inactive` dibatasi ke panel lokal karena unit berhenti dapat berasal dari `systemctl stop` yang
disengaja. Panel lokal tanpa policy helper ditolak `PPP_RESTART_NOT_APPROVED`. Error setelah
pemanggilan systemd menjadi `SYSTEMD_ACTION_OUTCOME_UNKNOWN` (`OUTCOME_UNKNOWN`), bukan gagal pasti.
Preflight `controls approve --ppp-restart` memakai aturan Ground/cadangan, jadi unit `inactive`
tidak dapat di-approve sebelum dinyalakan.

Unit PPP milik site. Unit `Type=notify` dengan pppd `up_sdnotify`, atau `Type=forking` dengan
`updetach`, tetap `activating/start` dengan job start antre sampai link PPP pertama terbentuk.
Selama radio putus, restart karena itu ditolak `PPP_SERVICE_NOT_ACTIVE`. Menerima keadaan itu
juga tidak membantu: `--no-block restart` digabung systemd ke job start yang sedang berjalan.
Gunakan `Type=simple` dengan pppd `nodetach`; lihat
[T900_PPP.md](T900_PPP.md#63-systemd-unit-ppp-hidup-lagi-sendiri).

### Stage dan pemantau pemulihan Edge

```text
ACCEPTED -> APPLYING (jurnal saja) -> PPP_RESTART_REQUESTED -> APPLIED | OUTCOME_UNKNOWN
ACCEPTED -> APPLYING (jurnal saja) -> FAILED | OUTCOME_UNKNOWN   (request helper gagal/ditolak)
```

- `PPP_RESTART_REQUESTED` (`accepted_by_systemd`, `requested_ms`, `service`) hanya berarti systemd
  menerima job. Setelah itu pemantau Agent mengamati link, untuk asal lokal, Ground, maupun
  cadangan.
- Selama pemantauan, monitor host memeriksa interface PPP, unit, dan ping peer kira-kira tiap
  detik (sampai 150 s). Langkah bukti: `cycled` (interface sempat tidak `UP`, waktu aktif atau
  `NRestarts` unit berubah, atau generation MQTT Control berubah), `ppp_up` (interface
  `link.local_ip` -> `link.peer_ip` kembali `UP` setelah siklus), `peer_reply` (ping peer
  dibalas), dan `mqtt_ready` (MQTT Control utama READY dengan sesi baru atau data broker diterima
  setelah PPP naik; `null` bila MQTT utama nonaktif).
- `APPLIED` setelah `peer_reply` dan `mqtt_ready` tidak `false`, dengan
  `proof: {ppp:"UP", peer:"REPLY", mqtt:"READY"|"DISABLED", cycled:true}` dan `elapsed_ms`.
- Tanpa bukti itu dalam 120 s: `OUTCOME_UNKNOWN` dengan `phase`, `steps`, `elapsed_ms`, dan
  `reason` yang dipilih dari bukti saat batas waktu, dengan urutan prioritas:
  `T900_USB_MISSING` (alias USB T900 `MISSING`), `MQTT_NOT_RECOVERED` (PPP naik dan ping peer
  dibalas, tetapi MQTT utama belum terbukti siap), `PPP_RECOVERY_TIMEOUT` (siklus terlihat, PPP/ping
  belum pulih), atau `PPP_CYCLE_NOT_OBSERVED` (tidak ada bukti siklus).
- Selama record `TIMEOUT` masih ditampilkan (90 s), pemantau tetap menilai bukti dan memperbarui
  `steps`. Bila ping peer dan MQTT utama (atau MQTT nonaktif) terbukti pulih terlambat, tampilan
  snapshot menjadi `phase: "DONE"` dengan `late: true`; `stage` tetap `OUTCOME_UNKNOWN` dan
  `reason` tetap alasan timeout. Jurnal dan ACK tidak ditulis ulang, dan tidak ada ACK baru.
- Bila request helper sendiri timeout atau socket-nya putus setelah request terkirim, stage menjadi
  `OUTCOME_UNKNOWN` dengan `error` (`SYSTEMD_ACTION_OUTCOME_UNKNOWN`, `HELPER_TIMEOUT`, atau
  `HELPER_CONNECTION_LOST`), karena systemd mungkin
  sudah me-restart unit. `link.ppp_recovery` lalu berfase `FAILED` dengan `stage`
  `OUTCOME_UNKNOWN`; panel menampilkannya sebagai hasil belum pasti, bukan gagal pasti. Penolakan
  helper yang pasti tetap `FAILED`.
- Snapshot Edge memuat `link.ppp_recovery` selama pemantauan dan 90 s sesudahnya (jam monotonic
  Edge, tidak terpengaruh lompatan jam NTP), serta `link.ppp_service` (`state`, `substate`,
  `restarts`). Pemantau berada di memori; restart `rdf-edge` membuat operasi yang belum final
  menjadi `OUTCOME_UNKNOWN` (`AGENT_RESTARTED_RECONCILE_NO_REPLAY`) dan tidak diulang.
- Saat interface PPP kembali `UP` atau ping peer kembali dibalas, Agent memanggil `kick()` pada
  client MQTT utama agar reconnect tanpa menunggu sisa backoff.

`APPLIED` membuktikan PPP berganti siklus, Ground membalas ping, dan MQTT utama tersambung lewat
link baru. Ia tidak membuktikan radio T900 di-reset atau receiver Ground memproses telemetry.

Panel Edge memberi peringatan di konfirmasi Restart T900 dan di kartu progres selama `link.usb`
bernilai `MISSING`, karena pppd yang baru di-restart tidak dapat membuka device yang tidak ada.
Tombol **Coba lagi** pada hasil `TIMEOUT` atau hasil belum pasti hanya tampil bila
`capabilities.ppp_restart` aktif dan snapshot belum menunjukkan link sehat (`link.ppp` `UP`,
`link.ppp_probe` `REPLY`, dan MQTT Control utama siap atau nonaktif).

### Ground

Ground hanya dapat mengirim saat MQTT Control tersedia dan health fresh. Jika seluruh PPP/MQTT
link sudah putus, request Ground tidak dapat mencapai Raspberry; pakai tombol Restart T900 di
panel Edge.

ACK akhir dari pemantau Edge dikirim di `ack/operation` dengan routing ACK biasa. Receiver Ground
bawaan tetap memakai rekonsiliasinya sendiri: ia mengubah `PPP_RESTART_REQUESTED` menjadi
`APPLIED` setelah menerima health yang lebih baru dalam sesi node yang sama, dan tidak mengganti
record-nya dengan ACK akhir Edge. Hasil Ground dan Edge karena itu dapat berbeda; record Edge
memakai bukti link yang lebih kuat. Hasil unresolved tidak dikirim ulang. Request baru memerlukan
health fresh, konfirmasi operator, dan pemeriksaan unit ulang; record lama tetap unknown.
Verifikasi nama/state unit dan waktu pemulihan memerlukan acceptance pada Raspberry.

## Asal perintah di panel Edge

Jurnal Edge menyimpan asal setiap command: `local` (panel), `ground` (broker utama), atau `backup`
(broker cadangan), serta nilai sebelum perubahan. Keduanya ada di tabel samping `operation_meta`;
tabel `operations` tetap tujuh kolom seperti rilis sebelumnya, sehingga rollback kode tetap dapat
menulis jurnal yang sama. Record operasi publik (snapshot,
`GET /api/v2/operations/latest`, dan hasil `operation.get`) menambah field aditif `origin`,
`summary` (`{kind, changes:{field:{from,to}}}`), dan `reason` (dari `result.error` atau
`result.reason`).

Snapshot Edge juga memuat `last_remote_operation` (mutasi terbaru dari Ground atau cadangan),
`operation_seq`, dan `remote_rejects` (sampai lima penolakan remote terakhir: `t`, `origin`, `op`,
`error`). `operation_seq` naik setiap kali stage operasi berubah, termasuk stage yang hanya ditulis
ke jurnal (`APPLYING`, `VERIFYING`, `REBOOT_SCHEDULED`, `SHUTDOWN_SCHEDULED`); satu perubahan dapat
menaikkannya lebih dari satu, jadi pakai hanya untuk mendeteksi perubahan, bukan menghitung stage.
`last_operation` tidak lagi menampilkan operasi baca (`config.get`, `operation.get`). Panel memakai
field ini untuk banner **Perintah dari GROUND** / **Perintah dari CADANGAN**, chip asal
`[G]`/`[C]`/`[L]`, dan dialog **Riwayat** di tab Config. Penolakan remote baru (selain operasi baca)
juga memunculkan banner yang sama dengan status `ditolak` dan alasannya; banner selain `diterapkan`
tetap tampil sampai `Tutup` disentuh.
Karena Edge tidak mengautentikasi publisher MQTT, banner asal adalah sinyal keamanan: perintah
dari CADANGAN berarti ada publisher yang diizinkan ACL broker cadangan.

## Lifecycle stack

`processing.set` memakai desired RUNNING/STOPPED dan menjalankan seluruh unit SDR pada
`link.engine_service`; `service.restart` memakai unit yang sama. Setup otomatis memilih
`rdfsdr.service` bila terdaftar. Konfigurasi lama yang menunjuk unit lain tidak ditimpa;
sebelum audit target, ubah unit secara eksplisit dengan `sudo rdf-node setup --engine-unit
rdfsdr.service`.
Dashboard tidak menghentikan `rdf-edge.service` atau PPP.

STOP menyimpan intent dan marker terlebih dahulu. Start menghapus marker dan menyimpan
RUNNING. Drop-in ConditionPathExists mencegah unit hidup kembali saat marker stop ada.
Helper startup merekonsiliasi intent terakhir hanya setelah lifecycle target dikonfigurasi dan
diaudit; aksi lokal juga memerlukan approval lokal. Tidak ada intent berarti helper tidak
menyalakan atau mematikan SDR saat pertama dipasang.

Stop verification: unit INACTIVE/FAILED dan cgroup kosong. Detached processes di luar unit hanya
dapat dikelola bila unit/wrapper lama diaudit dengan benar. Audit itu membatasi target yang dapat
dipanggil Ground; helper tetap menolak unit yang tidak dikonfigurasi. Start: unit active + DAQ baru
fresh dan sehat. Restart juga memerlukan start generation baru. Timeout bukan bukti side effect
tidak terjadi: OUTCOME_UNKNOWN.

Watchdog lama `sdr-watchdog.timer` dapat menyalakan ulang SDR yang sengaja dihentikan. Setup
lifecycle meminta permission menonaktifkannya bila dikenal; watchdog lain harus diaudit manual.
Release ini tidak menambahkan recovery loop DSP otomatis yang kedua.

## Reboot Raspberry

1. Reboot dari panel Edge memerlukan sesi Admin dan approval root satu kali `controls approve --reboot`; timed lease tidak digunakan.
   Ground tidak memerlukan remote grant atau approval root per aksi.
2. `system.reboot.execute` membawa prepare_id+challenge, serta envelope session terbaru. Ground UI meminta konfirmasi akhir.
3. Intent durable dibuat sebelum memanggil helper, kemudian systemd menjadwalkan reboot 5s.
4. UI memberi REBOOT_SCHEDULED, bukan APPLIED sebelum reboot.
5. Ground menerima boot_id baru dan health fresh, lalu mengonfirmasi APPLIED reboot; readiness SDR sesudah boot tetap dinilai terpisah.

Approval root lokal disimpan dalam helper policy/config dan tetap berlaku setelah reboot. Timed
maintenance lease sudah dihapus; lifecycle, PPP restart, reboot, dan shutdown lokal tidak lagi
memerlukan lease. Challenge, jurnal, dan pemeriksaan operasi tetap berlaku. Jika Raspberry tidak
kembali, outcome tetap belum diketahui.

## Shutdown Raspberry

1. `system.shutdown.prepare` dari Edge perlu capability shutdown dan approval root satu kali `--shutdown`, tanpa timed lease. Ground tidak memerlukan gate root per aksi.
2. Prepare menghasilkan challenge sekali pakai, terikat operation ID, hanya berlaku 30 detik dan tidak dapat dipakai untuk reboot.
3. UI meminta konfirmasi akhir. `system.shutdown.execute` menulis jurnal durable `SHUTDOWN_SCHEDULED` sebelum helper dipanggil.
4. Helper menyimpan intent root-only, lalu menjadwalkan `/usr/bin/systemctl poweroff` melalui unit transient tetap setelah 5 detik. Tidak ada shell, nama unit dari payload, atau restart SDR/bridge.
5. Intent current-boot memblokir prepare dan execute berikutnya di semua sesi/ID, mencegah jadwal poweroff duplikat; pemeriksaan setelah boot baru membuang intent lama.
6. `SHUTDOWN_SCHEDULED` bukan `APPLIED`: tidak ada bukti host telah mati. Timeout, kehilangan ACK, restart agent/Ground, atau node kembali membuat hasil `OUTCOME_UNKNOWN`. UI tidak mengulang otomatis dan mempertahankan hasil yang belum pasti.
7. `GET /api/v2/operations/pending-shutdowns` merangkum semua shutdown yang belum berstatus gagal pasti, bukan hanya halaman operasi terbaru. Ground juga merekonsiliasi seluruh operation ID pending saat sesi node berubah.
8. Setelah memeriksa Pi secara lokal, operator dapat menjalankan `sudo rdf-node controls shutdown-reconcile`. CLI root meminta teks `SHUTDOWN RECONCILED`; helper memeriksa timer dan service unit tersimpan lewat `systemctl show`, lalu menghapus intent hanya bila keduanya tidak aktif/tidak ada. Unit aktif atau status yang tidak diketahui tetap memblokir shutdown baru.

Konfirmasi UI melindungi user Ground dari salah klik, bukan command MQTT dari publisher lain yang
memiliki ACL broker. Perintah rekonsiliasi hanya membersihkan intent setelah bukti systemd lokal;
ia tidak membatalkan unit aktif dan tidak membuktikan host sebelumnya telah mati. Untuk menyalakan
Raspberry lagi diperlukan akses lokal/power-cycle. Tes mem-mock helper/systemd action dan tidak
menjalankan poweroff nyata.

## Jurnal dan query

SQLite WAL + synchronous FULL untuk metadata operasi. Tidak merekam semua telemetry.
Crash dengan operasi accepted/applying/verifying menjadi OUTCOME_UNKNOWN, bukan direplay
otomatis. Command ID dan result disimpan; Ground dapat query `operation.get` berdasarkan ID.
Hasil reboot berstatus scheduled direkonsiliasi dengan boot baru.

Batas retained terminal journal 2.000 record/30 hari; unresolved unknown tidak dibuang otomatis.
Pantau penggunaan storage dan rekonsiliasi unknown dalam maintenance; release tidak
menjalankan cleanup agresif terhadap bukti operasi yang belum selesai.

## Pesan error di ACK

Setiap penolakan atau kegagalan command membawa `error` (kode stabil), `message` (teks Indonesia
untuk operator), dan, bila ada, `detail` (penyebab teknis, maks. 200 karakter). Teks yang sama
dikembalikan API panel lokal dan disimpan di jurnal, sehingga `operation.get` juga mengembalikannya.
Panel Edge menampilkan label pendek miliknya sendiri. Bila kodenya belum dikenal panel, panel memakai
`message` dari Edge. Preview Ground menampilkan kode dan `message`.

Mutation yang butuh helper root ditolak sebelum masuk jurnal bila helper tidak tersedia. Status
helper yang di-cache agent hanya petunjuk: bila cache kosong, Edge mencoba `status` sekali lagi ke
socket (timeout 1 s) sebelum menolak. Bila socket ada tetapi lambat menjawab, command tetap
diterima, dan worker melaporkan hasil sebenarnya.

| Kode | Stage | Yang terjadi | Yang dilakukan operator |
|---|---|---|---|
| `HELPER_UNAVAILABLE` | `REJECTED` (sebelum jurnal) atau `FAILED` (helper hilang setelah diterima) | Socket `control.helper_socket` tidak ada atau tidak menerima koneksi. Perintah tidak dijalankan. | `systemctl status rdf-control-helper.service`; pastikan `/run/rdf-node-control/control.sock` ada. |
| `HELPER_ACCESS_DENIED` | `REJECTED` atau `FAILED` | Socket ada, tetapi user agent tidak boleh membukanya. Perintah tidak dijalankan. | Periksa owner/mode socket dan `allowed_user` di policy helper. |
| `HELPER_TIMEOUT` | `OUTCOME_UNKNOWN` | Request sudah terkirim ke helper, jawaban tidak datang. | Periksa hasil nyata (state, health, unit) sebelum mengulang. |
| `HELPER_CONNECTION_LOST` | `OUTCOME_UNKNOWN` | Koneksi putus setelah request terkirim. | Sama dengan `HELPER_TIMEOUT`. |
| `HELPER_CLOSED` | `FAILED` | Helper menutup koneksi tanpa jawaban. | Lihat journal `rdf-control-helper.service`. |
| `HELPER_INTERNAL_ERROR` | `FAILED` | Exception tak terduga di helper; `detail` memuat jenis dan pesannya. | Lihat `detail` dan journal helper. |
| `INVALID_COMMAND_FIELD` | `REJECTED` | Tipe atau nilai field salah (misalnya `issued_ms` bukan angka). | Perbaiki pengirim command. |
| `JOURNAL_UNAVAILABLE` | `REJECTED` | Jurnal SQLite tidak bisa ditulis. | Periksa ruang disk dan `state_dir`. |
| `INTERNAL_ERROR` | `REJECTED` atau `FAILED` | Error lain di agent; `detail` memuat penyebabnya. | Laporkan beserta `detail`. |

Kode lain (`CLOCK_UNTRUSTED`, `CONFIG_REVISION_CONFLICT`, `SESSION_MISMATCH`, `BUSY`,
`PPP_RESTART_TOO_SOON`, dan seterusnya) tidak berubah dan kini juga membawa `message`. Tabel lengkap
teksnya ada di `ERROR_MESSAGES` dalam `src/rdf_node/control.py`. Kode lama (`OSERROR`,
`TIMEOUTERROR`, `JOURNAL_OR_REQUEST_ERROR`) tetap punya teks, untuk record yang ditulis sebelum
rilis ini.
