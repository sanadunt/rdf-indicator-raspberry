# Operasi, upgrade, recovery dan rollback

## Jangan memutus satu-satunya jalur akses

Perubahan unit PPP atau reboot tidak diperlukan untuk installer ini. Maintenance reboot,
engine-control audit, dan cabut radio harus dilakukan ketika ada cara akses lokal/LAN
cadangan. SSH melalui10.90.0.2 ikut putus ketika radio atau OS restart.
Pengerasan PPP/udev/systemd yang disarankan di [T900_PPP.md](T900_PPP.md) bersifat opsional,
dipasang manual dari `deploy/examples/t900/`, dan juga harus dikerjakan dengan akses cadangan.

## Pra-terbang: link T900/PPP

1. Tab Link > Koneksi: `T900 USB` `PRESENT`, `PPP / interface` `UP`, `Ping 10.90.0.1` `BALASAN`,
   dan `MQTT CTRL / BULK` `CONNECTED`. Tab Link > Rincian: `Layanan PPP` `ACTIVE`.
2. Ground menerima health fresh dari sesi node saat ini.
3. Bila PPP/MQTT utama tidak pulih sendiri dalam sekitar satu menit, pakai
   `Config > Login > Kontrol > Raspberry > Restart T900` (perlu approval satu kali
   `sudo rdf-node controls approve --ppp-restart`). Terbang hanya setelah kartu progres
   menampilkan **Tersambung ke Ground ✓** dan Ground kembali menerima health.
4. Bila hasilnya **Belum tersambung dalam 120 dtk**, periksa pppd Ubuntu, kabel, daya, dan
   antena sebelum mencoba lagi. Jangan mengulang tanpa batas; kumpulkan diagnosis lebih dahulu.

Langkah lengkap, arti setiap langkah progres, dan kode penolakan ada di
[T900_PPP.md](T900_PPP.md#3-prosedur-pra-terbang).

## Identitas/status

`USB PRESENT` hanya bukti alias CH340 di physical path sesuai. Itu bukan unit serial unik
atau autentikasi RF. `PPP UP` memerlukan alamat local/peer sesuai. Tidak ada label RSSI T900
palsu dari power Kraken. IP statistics diberi label PPP/IP, bukan throughput RF murni.

Layar membaca snapshot local cache. API error dan snapshot_seq yang tidak bergerak >5s
menampilkan SUMBER STATUS HILANG. Browser yang benar-benar freeze tidak mampu menggambar
alarm; user service restart hanya menangani proses browser keluar. Renderer heartbeat
watchdog tambahan belum ada. Layar/backlight fisik tetap perlu diperiksa terpisah.

## File dan izin

Release root-owned read-only untuk service. State aplikasi berada pada /var/lib.
Menu Data memerlukan PIN admin dan CSRF. Pengaturan MQTT tersimpan di
`/var/lib/rdf-node/mqtt-ui-settings.json`; akun control/bulk tersimpan terpisah dengan mode 0600.
API hanya melaporkan apakah credential tersedia, tidak mengembalikan username/password. TLS/CA
tetap mengikuti provisioning bundle; halaman lokal tidak menyediakan bypass verifikasi sertifikat.
Perubahan broker/credential mengganti kedua client dan menghapus cache session lama. Bulk lanjut
setelah kedua koneksi MQTT siap dan masa `resume_stable_seconds` berlalu. Telemetry tidak
bergantung pada konfirmasi Ground.
Broker cadangan (`Data > Cadangan`) disimpan terpisah di
`/var/lib/rdf-node/mqtt-ui-backup-settings.json` dan akunnya di `mqtt-ui-backup-account.json`,
keduanya mode 0600. File panel yang valid menggantikan `mqtt.backup` di `config.yaml`; hapus kedua
file lalu restart `rdf-edge.service` untuk kembali ke `config.yaml`. Menyimpan pengaturan cadangan
hanya mengganti client cadangan; koneksi broker utama tidak diputus. `ca_file`,
`credentials_file`, dan `budget_bytes_s` cadangan hanya diatur di `config.yaml`. Lihat
[MQTT_GROUND.md](MQTT_GROUND.md#broker-cadangan-mirror-lanonline).
Source permission memakai ACL terbatas pada _share; tidak chmod777 atau recursive home read.
Untuk menampilkan file settings native dari Ground, operator harus memakai `Refresh config`.
Export memerlukan MQTT TLS aktif, source settings tersedia dan fresh, serta compact MQTT
envelope utuh tidak lebih dari 8.192 byte. Edge tidak mengirim settings otomatis saat startup,
reconnect, atau config change.

ACL perlu diperiksa kembali jika deployment engine membuat file dengan mode yang menolak
akses user service. Gunakan `sudo -u rdf-edge rdf-node doctor` setelah engine update.
Dokumen lama mungkin menyebut path berbeda; file config menjadi sumber deployment aktual.

## Logs dan backup

Ambil log terbatas `journalctl -n60 --no-pager`; hindari `-f` atau file besar melalui T900
saat telemetry berjalan. Aplikasi tidak memasukkan raw settings, PIN, atau secret ke log normal.
Redact identifier sensitif sebelum membagikan diagnosis.

Backup yang disarankan pada media aman:
- /etc/rdf-node (termasuk credentials, harus dianggap rahasia);
- /var/lib/rdf-node (SQLite journal, MQTT credential UI mode 0600 termasuk akun broker cadangan
  `mqtt-ui-backup-account.json`; perlakukan sebagai rahasia);
- /etc/rdf-node/helper.yaml dan /var/lib/rdf-node-control (intent/backupsettings);
- /etc/rdf-ground dan /etc/rdf-ground-mqtt pada Ubuntu;
- konfigurasi asli unit SDR/watchdog sebelum approval lifecycle;
- `/etc/ppp`, aturan udev T900, dan unit/drop-in PPP di kedua host sebelum menerapkan contoh
  `deploy/examples/t900/`.

Gunakan mekanisme backup SQLite yang konsisten atau hentikan bridge sementara melalui
management; jangan hanya menyalin file WAL terpisah secara acak. Tidak ada raw telemetry
history besar yang dibangun aplikasi.

## Upgrade

Ekstrak ZIP release baru ke folder terpisah, jalankan installer yang sama.
Installer mempertahankan config, hash PIN, journal serta menyimpan symlink previous.
Konfigurasi lama tidak ditimpa oleh default release. Field legacy
`telemetry.require_ground_receipt_for_bulk` tidak lagi dikenal; hapus field itu sebelum upgrade
karena schema strict menolak key yang tidak dikenali. Sesudah upgrade dari versi password,
jalankan `sudo rdf-node set-pin` lalu restart service yang sesuai untuk mengganti credential lama
dan mencabut sesi admin aktif. Checksum diverifikasi sebelum copy. Script tidak mengganti path
source otomatis. Sesudah upgrade: doctor, health/curve, command read-only, lalu test subset
controlled. Schema config unknown key ditolak; periksa release notes saat berpindah versi.

## Rollback code

```bash
sudo bash /opt/rdf-node/current/scripts/rollback.sh
```

Rollback mengubah current ke previous dan try-restart service baru, bukan PPP/SDR.
Ia tidak merollback database, settings RF, credential atau perubahan hardware. Untuk data
schema yang berubah pada versi mendatang, backup dan migration policy tetap diperlukan.

## Ground writes and broker trust boundary

Semua command write Ground yang didukung berjalan tanpa remote grant dan tanpa approval root
per aksi. Ground UI tetap memakai PIN login+CSRF; autentikasi itu hanya melindungi HTTP API.
Edge tidak mengautentikasi publisher MQTT atau mengikat command ke sesi UI. Publisher mana pun
yang diizinkan ACL broker ke topic command dapat menjalankan operasi remote dengan target siap.

Private PPP/T900 bukan autentikasi publisher. Gunakan TLS, credential broker unik, dan ACL
deny-by-default yang memberi akses publish command hanya kepada Ground tepercaya; jangan
mengaktifkan anonymous publish. TLS memverifikasi broker dan mengenkripsi transport, tetapi
otorisasi publisher tetap dilakukan oleh broker.

Flag CLI `--remote` telah dihapus; `--reboot` mengatur gate reboot lokal Edge melalui approval root
satu kali yang persisten, tanpa lease maintenance. Key lama `remote_commands_enabled` dan
`allow_remote_control` tetap diterima untuk kompatibilitas; `controls approve` mengatur gate aksi
lokal Edge, dan menjalankannya tanpa flag **tidak mencabut** Ground.
Untuk menutup jalur write, cabut ACL publish pada subtree `cmd/#` di broker atau nonaktifkan
MQTT Control. Mematikan MQTT Control juga memutus telemetry/control channel.

Target Ground yang perlu disiapkan satu kali:

- `sudo rdf-node controls approve --settings` menetapkan path settings fixed dan single-writer
  confirmation. Ground patch tetap dibatasi allowlist dan compare-digest.
- `sudo rdf-node controls approve --lifecycle` mengaudit target SDR dan watchdog serta memasang
  stop-intent guard. Ground Start/Stop/Restart hanya memakai target ini.
- PPP selalu fixed `t900-ppp.service`; helper memeriksa unit loaded dan tidak ada job pending pada
  setiap request. Unit `active`, `failed`, atau `activating/auto-restart` dapat direstart dari
  semua asal; unit `inactive` hanya dari panel Edge lokal. Restart PPP memakai jalur sendiri,
  maksimal satu sekaligus, dengan jeda minimum 15 s (lihat
  [CONTROL.md](CONTROL.md#restart-layanan-ppp-t900)).
- Reboot/shutdown tetap memakai Prepare/Execute, challenge sekali pakai, durable journal/intent,
  dan konfirmasi UI. Edge tidak mengikat konfirmasi browser ke publisher MQTT.

Prerequisite target settings/lifecycle bukan approval root per Ground command. `controls approve --reboot`,
`--shutdown`, dan `--ppp-restart` tetap mengatur gate panel Edge lokal dan tersimpan lintas reboot.
Lifecycle, PPP restart, shutdown, dan reboot lokal tidak memakai timed lease; settings lokal tetap
memerlukan approval single-writer. Ground tidak memakai approval root lokal.

Ground membutuhkan MQTT Control ready dan session/health node fresh untuk mutasi. Jika MQTT
Control putus sepenuhnya, Ground tidak dapat mengirim PPP restart atau command lain; restart PPP
lokal dari panel Edge tetap tersedia setelah approval `--ppp-restart`.

## Broker cadangan: keamanan dan routing

Broker cadangan mengirim salinan semua data Edge ke broker LAN/online dan tetap berjalan saat
PPP/T900 atau broker utama mati. Bila `Terima perintah` aktif (default), command dari broker
cadangan dijalankan **seperti command Ground**, termasuk reboot, shutdown, restart PPP, dan patch
settings, tanpa approval root per aksi. Batasi ACL publish `<prefix>/cmd/#` di broker cadangan
hanya untuk controller tepercaya dan jangan izinkan anonymous publish. Jangan aktifkan
`Terima perintah` pada broker publik atau bersama. Untuk menutup seluruh akses write remote, cabut
ACL `cmd/#` di broker utama, lalu untuk broker cadangan cabut ACL `cmd/#`-nya atau set
`Terima perintah: Nonaktif`. `Terima perintah: Nonaktif` saja tidak menutup jalur command Ground
lewat broker utama.

Broker dengan ACL publish-only harus dipasangkan dengan `Terima perintah: Nonaktif`; bila broker
menolak subscribe `cmd/*`, client cadangan terus reconnect dengan error `SUBACK_REJECTED` dan
tidak pernah READY.
Broker LAN self-signed memerlukan `mqtt.backup.ca_file` (dan SAN yang cocok dengan host/IP) atau
TLS nonaktif pada LAN tepercaya; tanpa itu muncul `TLS_CERTIFICATE_VERIFY_FAILED`.

Edge mengikuti routing OS. Sebelum mengaktifkan broker online, jalankan
`ip route get <host-cadangan>`. Bila keluar lewat PPP (`ppp0`), trafik cadangan memakai bandwidth
T900 di luar budget agregat T900 (default cadangan 8.000 B/s) dan dapat mengganggu telemetry dan
command utama. Pastikan ada uplink LAN/Wi-Fi/seluler dengan route ke broker cadangan.

## Troubleshooting PPP/T900

Gejala dan langkah singkat; data diagnosis lengkap ada di
[T900_PPP.md](T900_PPP.md#8-data-diagnosis-yang-perlu-dikumpulkan).

| Gejala | Langkah |
|---|---|
| `T900 USB` `MISSING` atau `IDENTITY_MISMATCH` | Periksa kabel/port dan alias `/dev/t900`; `udevadm info --query=property --name=/dev/t900` harus cocok dengan `link.expected_*`. |
| `PPP` `DOWN`, `Layanan PPP` `ACTIVE` | pppd hidup tetapi link belum terbentuk; periksa `journalctl -u t900-ppp.service` dan pppd Ubuntu. |
| `Layanan PPP` `FAILED`/`INACTIVE` | Tombol Restart T900 lokal dapat menyalakannya lagi; pasang drop-in `Restart=always` agar pulih sendiri. |
| `restart N` di `Layanan PPP` terus naik | pppd sering keluar; cari `Modem hangup`, echo timeout, atau USB disconnect di log. |
| Restart T900 berakhir `PPP_CYCLE_NOT_OBSERVED` | Tidak ada bukti unit/interface berganti siklus; periksa `systemctl status t900-ppp.service`. |
| Restart T900 berakhir `PPP_RECOVERY_TIMEOUT` | PPP berganti siklus tetapi Ground belum membalas atau MQTT belum siap; periksa sisi Ubuntu, daya, dan antena. |
| Ditolak `PPP_RESTART_TOO_SOON` / `PPP_RESTART_IN_PROGRESS` | Tunggu 15 s atau sampai pemantauan sebelumnya selesai (maks. 120 s). |
| Hanya pulih setelah cabut-colok T900 | Lihat [T900_PPP.md](T900_PPP.md#5-mengapa-cabut-colok-t900-berhasil) dan terapkan bagian 6 di kedua sisi. |

## Uninstall

```bash
sudo bash /opt/rdf-node/current/scripts/uninstall.sh
```

Menghapus unit baru, tidak menghapus config/journal/certificates/release secara diam-diam.
Jika lifecycle ownership pernah diaktifkan, sebelum melepas helper tentukan desired state
engine dan kebijakan boot/watchdog yang diinginkan. Drop-in
`/etc/systemd/system/<unit-SDR>.d/50-rdf-node-intent.conf` dan stop marker sengaja tidak
sembarang dihapus karena menghapus guard dapat menyalakan SDR tanpa persetujuan.
Hapus guard/restore watchdog hanya dalam maintenance setelah review; `daemon-reload` sesudahnya.

Kiosk: dari desktop `systemctl --user stop rdf-kiosk.service`, hapus user unit dan
`~/.config/autostart/rdf-node.desktop`; hapus baris yang berlabel RDF_NODE_KIOSK pada
`~/.config/labwc/autostart` bila installer menambahkannya. Jangan menghapus entry aplikasi lain.

## Memulihkan intent shutdown yang belum pasti

Shutdown yang dijadwalkan menulis intent lokal pada helper sebelum pemanggilan systemd. Intent ini
memblokir shutdown kedua pada boot yang sama. Bila UI menampilkan `OUTCOME_UNKNOWN`, periksa
status Raspberry secara lokal; jangan kirim ulang hanya karena ACK tidak diterima. Jika unit
shutdown lama sudah tidak aktif, bersihkan intent dengan:

```bash
sudo rdf-node controls shutdown-reconcile
```

CLI meminta teks tepat `SHUTDOWN RECONCILED`. Helper root memeriksa unit timer dan service tetap
yang tersimpan lewat `systemctl show`; ia menghapus marker hanya bila keduanya `not-found/inactive`
atau `loaded/inactive|failed`. Unit aktif dan kegagalan/hasil systemd yang tidak diketahui
mempertahankan marker dan menolak rekonsiliasi. Boot ID baru juga mengizinkan pembersihan intent
lama pada pemeriksaan berikutnya. Prosedur ini hanya merekonsiliasi intent lokal; tidak menguji
poweroff hardware, tidak membatalkan unit aktif, dan tidak menjadwalkan aksi apa pun. Tes mock
systemd dan tidak mematikan mesin.

## Hal yang belum dibuktikan di perangkat

Acceptance Raspberry belum dilakukan untuk T900 restart/pemulihan link, runtime settings SDR,
cold boot, autologin/compositor, touchscreen, PSU/thermal, performa receiver Chromium,
semantik config native, cakupan detached child process, callback convergence, latency radio,
interoperabilitas Mosquitto, broker cadangan LAN/online dan routing-nya, adapter dashboard lama,
atau reboot/shutdown nyata. Semua ini tetap
perlu diuji pada perangkat; kelulusan test fixture/unit tidak membuktikannya.

Untuk acceptance T900 dengan pengawasan, periksa dahulu unit fixed ini secara read-only:

```bash
systemctl show t900-ppp.service -p LoadState -p ActiveState -p SubState -p Job -p NRestarts
```

Pastikan `LoadState=loaded` dan `Job=0`. Panel lokal dan Ground dapat me-restart unit `active`,
`failed`, atau `activating/auto-restart`; `inactive` hanya dari panel lokal. Lakukan hanya dalam
test window terawasi dengan akses lokal/LAN cadangan. Catat untuk setiap uji: asal permintaan,
waktu tiap langkah kartu progres (`cycled`, `ppp_up`, `peer_reply`, `mqtt_ready`), stage akhir
Edge (`APPLIED` atau `OUTCOME_UNKNOWN` beserta `reason`), waktu sampai health fresh di Ground, dan
waktu sampai Angular kembali. Ground bawaan melaporkan `APPLIED` dari health baru pada sesi yang
sama; itu tidak membuktikan state unit PPP. Uji juga restart dari unit `failed`, restart lokal saat
clock belum sinkron, dan restart saat verifikasi SDR sedang berjalan. Jika MQTT tidak kembali,
pemulihan memerlukan panel lokal atau jalur management independen. Jangan mengulang hasil unknown
secara otomatis.
