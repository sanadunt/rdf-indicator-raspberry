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
2. Maksimum satu mutasi aktif, tulis intent ke jurnal durable dahulu.
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
Ground tidak memerlukan remote grant atau `ppp_restart` root approval. Gate `ppp_restart` lokal
Edge tetap terpisah.

Pada setiap request helper memeriksa `LoadState=loaded`, `ActiveState=active`, dan `Job=0` lewat
`systemctl show`. Pemeriksaan dan pemanggilan tetap
`/usr/bin/systemctl --no-block restart t900-ppp.service` berjalan di bawah lock helper yang sama.
Ground hanya dapat mengirim saat MQTT Control tersedia dan health fresh. Jika seluruh PPP/MQTT
link sudah putus, request tidak dapat mencapai Raspberry.

`PPP_RESTART_REQUESTED` berarti systemd menerima request, bukan bahwa PPP atau MQTT pulih.
Ground hanya mengubahnya menjadi `APPLIED` setelah menerima health yang lebih baru dalam sesi
node yang sama; hasil itu tetap bukan bukti unit PPP pulih. Hasil unresolved tidak dikirim ulang.
Request baru memerlukan health fresh, konfirmasi operator, dan pemeriksaan unit ulang; record lama
tetap unknown. Verifikasi nama/state unit dan waktu pemulihan memerlukan acceptance pada Raspberry.


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

Batas retained terminal journal2,000/30hari; unresolved unknown tidak dibuang otomatis.
Pantau penggunaan storage dan rekonsiliasi unknown dalam maintenance; release tidak
menjalankan cleanup agresif terhadap bukti operasi yang belum selesai.
