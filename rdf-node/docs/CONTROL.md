# Kontrol RF, lifecycle, reboot dan shutdown

## Default dan approval

Installer menjalankan bridge read-only. Helper root hanya mendengar Unix socket lokal;
credential peer diverifikasi dengan SO_PEERCRED. Tidak ada shell command, arbitrary path,
atau nama service dalam payload MQTT. Konfigurasi helper root-owned dan tidak writable
oleh group/world. Read-only configuration query tetap dapat digunakan sebelum write enabled.

Approval dari terminal Raspberry yang dipercaya:

```bash
sudo rdf-node controls approve --settings --remote
```

Pilih flags sesuai kebutuhan. Lifecycle dan reboot tetap terpisah:

```bash
sudo rdf-node controls approve --lifecycle --reboot --remote
sudo rdf-node controls maintenance-open --seconds 300
```

Shutdown OS memerlukan opt-in terpisah:

```bash
sudo rdf-node controls approve --shutdown --remote
```

Wizard meminta ketikan `SHUTDOWN`; `allow_shutdown` dan `shutdown_enabled` default false.
Ground hanya dapat mengirimnya bila `remote_commands_enabled` juga aktif. Shutdown tidak
memerlukan atau mengubah ownership unit SDR.

Catatan: flags approval menambah capability yang disetujui; remote flag mengatur apakah
permintaan mutating dari Ground boleh diterima. Untuk mencabut capability, edit
`/etc/rdf-node/config.yaml` DAN `/etc/rdf-node/helper.yaml` dengan sudo, kemudian restart
kedua service baru. Jangan mengandalkan menyembunyikan tombol di browser.

## Supported settings

| Contract | Native | Konversi |
|---|---|---|
| center_frequency_hz | center_freq | Hz -> MHz |
| vfo0_frequency_hz | vfo_freq_0 | Hz |
| gain_db | uniform_gain | dB; tabel gain helper |
| vfo0_bandwidth_hz | vfo_bw_0 | Hz |
| vfo0_squelch_db | vfo_squelch_0 | native dB |

MVP hanya satu output VFO0 aktif. Retune center wajib menyertakan VFO0 dengan target sama;
permintaan yang ambigu ditolak. Range default helper24MHz..1766MHz dan tabel gain adalah
baseline kandidat, bukan kalibrasi hardware Anda. Audit/cocokkan unit/native API serta
passband/bandwidth terhadap engine sebelum memberikan single-writer approval.

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

## Lifecycle stack

`processing.set` memakai desired RUNNING/STOPPED; implementasinya `systemctl --no-block`
terhadap unit stack SDR yang di-approve. `service.restart` memakai unit yang sama.
Tidak ada perintah stop bridge/PPP via dashboard rutin.

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

1. `system.reboot.prepare` saat izin+maintenance aktif menghasilkan challenge sekali pakai30s.
2. `system.reboot.execute` membawa prepare_id+challenge, serta envelope session terbaru.
3. Intent durable dibuat sebelum memanggil helper, kemudian systemd menjadwalkan reboot5s.
4. UI memberi REBOOT_SCHEDULED, bukan APPLIED sebelum reboot.
5. Ground menerima boot_id baru dan health fresh, lalu mengonfirmasi APPLIED reboot;
   readiness SDR sesudah boot tetap dinilai terpisah.

Lease maintenance hanya dapat dibuka melalui sudo lokal/trusted management, 30..900 detik,
terikat boot dan monotonic deadline. Remote Ground tidak bisa membuka lease dengan klaim
role di payload. Jika Raspberry tidak kembali, outcome tetap belum diketahui.

## Shutdown Raspberry

1. `system.shutdown.prepare` hanya berhasil bila approval helper/config dan maintenance lease lokal aktif; Ground juga memerlukan `remote_commands_enabled`.
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
