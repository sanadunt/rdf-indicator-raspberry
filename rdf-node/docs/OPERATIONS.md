# Operasi, upgrade, recovery dan rollback

## Jangan memutus satu-satunya jalur akses

Perubahan unit PPP atau reboot tidak diperlukan untuk installer ini. Maintenance reboot,
engine-control audit, dan cabut radio harus dilakukan ketika ada cara akses lokal/LAN
cadangan. SSH melalui10.90.0.2 ikut putus ketika radio atau OS restart.

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
Perubahan broker/credential mengganti kedua client dan menghapus receipt lama; bulk menunggu bukti Ground baru.
Source permission memakai ACL terbatas pada _share; tidak chmod777 atau recursive home read.

ACL perlu diperiksa kembali jika deployment engine membuat file dengan mode yang menolak
akses user service. Gunakan `sudo -u rdf-edge rdf-node doctor` setelah engine update.
Dokumen lama mungkin menyebut path berbeda; file config menjadi sumber deployment aktual.

## Logs dan backup

Ambil log terbatas `journalctl -n60 --no-pager`; hindari `-f` atau file besar melalui T900
saat telemetry berjalan. Aplikasi tidak memasukkan raw settings, PIN, atau secret ke log normal.
Redact identifier sensitif sebelum membagikan diagnosis.

Backup yang disarankan pada media aman:
- /etc/rdf-node (termasuk credentials, harus dianggap rahasia);
- /var/lib/rdf-node (SQLite journal, MQTT credential UI mode 0600; perlakukan sebagai rahasia);
- /etc/rdf-node/helper.yaml dan /var/lib/rdf-node-control (intent/backupsettings);
- /etc/rdf-ground dan /etc/rdf-ground-mqtt pada Ubuntu;
- konfigurasi asli unit SDR/watchdog sebelum approval lifecycle.

Gunakan mekanisme backup SQLite yang konsisten atau hentikan bridge sementara melalui
management; jangan hanya menyalin file WAL terpisah secara acak. Tidak ada raw telemetry
history besar yang dibangun aplikasi.

## Upgrade

Ekstrak ZIP release baru ke folder terpisah, jalankan installer yang sama.
Installer mempertahankan config, hash PIN, dan journal serta menyimpan symlink previous.
Sesudah upgrade dari versi password, jalankan `sudo rdf-node set-pin` lalu restart service
yang sesuai untuk mengganti credential lama dan mencabut sesi admin aktif.
Checksum diverifikasi sebelum copy. Script tidak mengganti path source otomatis.
Sesudah upgrade: doctor, health/receipt/curve, command read-only, lalu test subset controlled.
Schema config unknown key ditolak; periksa release notes saat berpindah versi.

## Rollback code

```bash
sudo bash /opt/rdf-node/current/scripts/rollback.sh
```

Rollback mengubah current ke previous dan try-restart service baru, bukan PPP/SDR.
Ia tidak merollback database, settings RF, credential atau perubahan hardware. Untuk data
schema yang berubah pada versi mendatang, backup dan migration policy tetap diperlukan.

## Revoke control

Set flags kontrol false pada config agent dan helper policy, restart helper/edge.
Tutup maintenance lease melalui `sudo rdf-node controls maintenance-close`.
Menghapus tombol atau mengubah role payload tidak mencabut izin backend.

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

Cold boot, autologin/compositor, touchscreen, PSU/thermal, receiver performance dengan
Chromium, native config semantics, detached child process scope, callback convergence,
latency radio, Mosquitto interoperability, old dashboard adapter, reboot/shutdown asli belum
diuji pada perangkat pengguna. Ini acceptance lapangan, bukan hal yang bisa disimpulkan
hanya dari lulus unit test.
