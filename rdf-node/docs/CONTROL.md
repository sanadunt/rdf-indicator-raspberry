# Kontrol RF, lifecycle, reboot dan shutdown

## Default dan approval

Installer menjalankan bridge read-only. Helper root hanya mendengar Unix socket lokal;
credential peer diverifikasi dengan SO_PEERCRED. Tidak ada shell command, arbitrary path,
atau nama service dalam payload MQTT. Konfigurasi helper root-owned dan tidak writable
oleh group/world. Read-only configuration query tetap dapat digunakan sebelum write enabled.

Approval berjalan dari terminal Raspberry sebagai root. `remote_commands_enabled` pada config
Edge dan `allow_remote_control` pada helper sama-sama mati secara default. `--remote`
mengaktifkan keduanya dan merestart helper/Edge; Ground tetap memerlukan capability tiap
operasi yang menyediakannya.

Contoh approval lengkap:

```bash
sudo rdf-node controls approve --settings --lifecycle --reboot --shutdown --remote --ppp-restart
```

CLI meminta `APPROVE`. `--settings` meminta `SINGLE` untuk mengonfirmasi single writer;
`--lifecycle` meminta `AUDITED` untuk unit SDR/watchdog; `--reboot`, `--shutdown`, dan
`--ppp-restart` meminta masing-masing `REBOOT`, `SHUTDOWN`, dan `T900 PPP RESTART`.
`--ppp-restart` juga menolak approval bila `t900-ppp.service` tidak loaded dan active atau
memiliki systemd job pending. Gunakan hanya flags untuk operasi yang memang diizinkan.

Ground memakai actor internal `ground-controller`; payload MQTT tidak dapat memilih actor.
Dengan kedua remote grant aktif, helper mengizinkan actor Ground melewati maintenance lease
untuk operasi yang disetujui. Operasi lifecycle, reboot, dan shutdown dari panel Edge lokal
tetap memerlukan lease 30..900 detik. Ground tidak dapat membuka, memperpanjang, atau menutup
lease. Settings dan stream lokal tetap mengikuti policy sebelumnya.

Buka lease 300 detik untuk aksi lokal dari panel Edge, lalu tutup setelah selesai:

```bash
sudo rdf-node controls maintenance-open --seconds 300
sudo rdf-node controls maintenance-close
```

Cabut remote access secara lokal:

```bash
sudo rdf-node controls approve
```

Ketik `APPROVE`. Tanpa `--remote`, CLI mematikan kedua remote grant dan merestart helper/Edge;
approval per-action yang sudah tersimpan tetap ada untuk operasi lokal. Jangan memakai
penyembunyian tombol browser sebagai pencabutan izin.

Ground PIN dan CSRF mengotorisasi API lokal, bukan publisher MQTT. Edge tidak membuktikan
identitas publisher dari payload. Broker ACL dan isolasi jaringan membatasi siapa dapat
mengirim command. Jika broker menerima publish anonymous ke topic command, setiap publisher
yang menjangkaunya dapat memakai operasi remote yang aktif. MQTT plaintext mengekspos
credential dan payload; gunakan hanya pada link privat terisolasi/tepercaya. TLS bukan syarat
remote approval.

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
single-writer approval.

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

RDF Node tidak memasang, mengubah konfigurasi, atau mengambil ownership PPP. Operasi
`ppp.restart` hanya menargetkan fixed unit `t900-ppp.service`; tidak ada nama unit atau command
dari payload. Capability `ppp_restart` mati secara default dan memerlukan approval root
terpisah pada helper serta config Edge, selain remote grant untuk Ground.

Saat approval dan setiap request, helper memeriksa `LoadState=loaded`, `ActiveState=active`,
dan `Job=0` lewat `systemctl show`. Pemeriksaan dan pemanggilan tetap
`/usr/bin/systemctl --no-block restart t900-ppp.service` berjalan di bawah lock helper yang
sama. Ground hanya dapat mengirim saat MQTT Control tersedia dan health fresh. Jika seluruh
PPP/MQTT link sudah putus, request tidak dapat mencapai Raspberry.

`PPP_RESTART_REQUESTED` berarti systemd menerima request, bukan bahwa PPP atau MQTT pulih.
Ground hanya mengubahnya menjadi `APPLIED` setelah menerima health yang lebih baru dalam sesi
node yang sama; hasil itu tetap bukan bukti unit PPP pulih. Hasil unresolved tidak dikirim
ulang. Request baru memerlukan health fresh, konfirmasi operator, dan pemeriksaan unit ulang;
record lama tetap unknown. Verifikasi nama/state unit dan waktu pemulihan memerlukan acceptance
pada Raspberry.


## Lifecycle stack

`processing.set` memakai desired RUNNING/STOPPED dan menjalankan seluruh unit SDR pada
`link.engine_service`; `service.restart` memakai unit yang sama. Setup otomatis memilih
`rdfsdr.service` bila terdaftar. Konfigurasi lama yang menunjuk unit lain tidak ditimpa;
sebelum approval, ubah target secara eksplisit dengan `sudo rdf-node setup --engine-unit
rdfsdr.service`.
Dashboard tidak menghentikan `rdf-edge.service` atau PPP.

STOP menyimpan intent dan marker terlebih dahulu. Start menghapus marker dan menyimpan
RUNNING. Drop-in ConditionPathExists mencegah unit hidup kembali saat marker stop ada.
Helper startup merekonsiliasi intent terakhir hanya setelah lifecycle ownership di-approve.
Tidak ada intent berarti helper tidak menyalakan/mematikan SDR saat pertama dipasang.

Stop verification: unit INACTIVE/FAILED dan cgroup kosong. Detached processes di luar unit
hanya dapat dikelola bila unit/wrapper lama diaudit dengan benar. Itulah alasan approval
scope wajib. Start: unit active + DAQ baru fresh dan sehat. Restart juga memerlukan
start generation baru. Timeout bukan bukti side effect tidak terjadi: OUTCOME_UNKNOWN.

Watchdog lama `sdr-watchdog.timer` dapat menyalakan ulang SDR yang sengaja dihentikan.
Approval meminta permission menonaktifkannya bila dikenal; watchdog lain harus diaudit
manual. Release ini tidak menambahkan recovery loop DSP otomatis yang kedua.

## Reboot Raspberry

1. `system.reboot.prepare` perlu capability reboot. Actor lokal perlu maintenance lease; actor Ground perlu kedua remote grant.
2. `system.reboot.execute` membawa prepare_id+challenge, serta envelope session terbaru.
3. Intent durable dibuat sebelum memanggil helper, kemudian systemd menjadwalkan reboot5s.
4. UI memberi REBOOT_SCHEDULED, bukan APPLIED sebelum reboot.
5. Ground menerima boot_id baru dan health fresh, lalu mengonfirmasi APPLIED reboot;
   readiness SDR sesudah boot tetap dinilai terpisah.

Lease maintenance dibuka hanya melalui sudo lokal/trusted management, 30..900 detik, dan
terikat boot serta monotonic deadline. Remote Ground tidak dapat mengubah lease; izin Ground
adalah grant root yang terpisah. Jika Raspberry tidak kembali, outcome tetap belum diketahui.

## Shutdown Raspberry

1. `system.shutdown.prepare` perlu capability shutdown. Actor lokal juga perlu maintenance lease; actor Ground perlu kedua remote grant.
2. Prepare menghasilkan challenge sekali pakai, terikat operation ID, hanya berlaku 30 detik dan tidak dapat dipakai untuk reboot.
3. UI meminta konfirmasi akhir. `system.shutdown.execute` menulis jurnal durable `SHUTDOWN_SCHEDULED` sebelum helper dipanggil.
4. Helper menyimpan intent root-only, lalu menjadwalkan `/usr/bin/systemctl poweroff` melalui unit transient tetap setelah 5 detik. Tidak ada shell, nama unit dari payload, atau restart SDR/bridge.
5. Intent current-boot memblokir prepare dan execute berikutnya di semua sesi/ID, mencegah jadwal poweroff duplikat; pemeriksaan setelah boot baru membuang intent lama.
6. `SHUTDOWN_SCHEDULED` bukan `APPLIED`: tidak ada bukti host telah mati. Timeout, kehilangan ACK, restart agent/Ground, atau node kembali membuat hasil `OUTCOME_UNKNOWN`. UI tidak mengulang otomatis dan mempertahankan hasil yang belum pasti.
7. `GET /api/v2/operations/pending-shutdowns` merangkum semua shutdown yang belum berstatus gagal pasti, bukan hanya halaman operasi terbaru. Ground juga merekonsiliasi seluruh operation ID pending saat sesi node berubah.
8. Setelah memeriksa Pi secara lokal, operator dapat menjalankan `sudo rdf-node controls shutdown-reconcile`. CLI root meminta teks `SHUTDOWN RECONCILED`; helper memeriksa timer dan service unit tersimpan lewat `systemctl show`, lalu menghapus intent hanya bila keduanya tidak aktif/tidak ada. Unit aktif atau status yang tidak diketahui tetap memblokir shutdown baru.

Perintah rekonsiliasi hanya membersihkan intent setelah bukti systemd lokal; ia tidak membatalkan unit aktif dan tidak membuktikan host sebelumnya telah mati. Untuk menyalakan Raspberry lagi diperlukan akses lokal/power-cycle. Tes mem-mock helper/systemd action dan tidak menjalankan poweroff nyata.

## Jurnal dan query

SQLite WAL + synchronous FULL untuk metadata operasi. Tidak merekam semua telemetry.
Crash dengan operasi accepted/applying/verifying menjadi OUTCOME_UNKNOWN, bukan direplay
otomatis. Command ID dan result disimpan; Ground dapat query `operation.get` berdasarkan ID.
Hasil reboot berstatus scheduled direkonsiliasi dengan boot baru.

Batas retained terminal journal2,000/30hari; unresolved unknown tidak dibuang otomatis.
Pantau penggunaan storage dan rekonsiliasi unknown dalam maintenance; release tidak
menjalankan cleanup agresif terhadap bukti operasi yang belum selesai.
