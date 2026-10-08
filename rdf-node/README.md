# RDF Node 1.0.0

**Bridge SDR-DoA/KrakenSDR + panel Raspberry 480x320 + receiver Ground pendamping.**

Paket ini berisi source aplikasi, installer, unit systemd, UI lokal, codec grafik 360 titik,
MQTT 5 (TLS default; plaintext dapat dipilih), command manager, helper terbatas, pengujian, dan
panduan operasi.
Bukan sekadar mockup. Tidak perlu npm, pip, Docker, atau mengubah environment Conda SDR.

> Panel Edge lokal mulai read-only. Ground dapat mengirim semua command write yang didukung tanpa
> remote grant atau approval root per aksi. Guard session/boot, clock, revision, health, journal,
> target helper tetap berlaku; Ground UI juga meminta konfirmasi untuk aksi destruktif.
>
> Edge tidak mengautentikasi identitas publisher MQTT. Private PPP/T900 bukan autentikasi:
> publisher mana pun yang diizinkan ACL broker ke topic command dapat meminta operasi remote.
> Gunakan TLS, credential terpisah, dan ACL broker yang hanya memberi akses command kepada Ground;
> jangan izinkan anonymous publish.
>
> Pengujian lokal paket bukan bukti uji pada Raspberry/layar/T900 milik Anda.
> Baca [TEST_REPORT.md](docs/TEST_REPORT.md) dan [batas implementasi](docs/IMPLEMENTATION.md).

## 1. Apa yang sudah ada

- Collector file lokal `DOA_value.html` (CSV 377 field), `status.json`, dan subset aman settings.
- Gate DAQ, kemajuan frame, freshness, konfigurasi, clock, dan verifikasi konvensi sudut.
- Dua koneksi MQTT: control/health/DoA dan diagnostic angular; bulk membawa angular normal. Akun/ACL control dan bulk terpisah.
- Angular dikirim sebagai satu JSON PUBLISH berisi metadata dan tepat 360 angka source, tanpa Q16/U8 quantization atau chunk.
- Edge tidak menerima konfirmasi aplikasi Ground atas telemetry; MQTT CONNECTED bukan bukti telemetry diproses Ground.
- Panel **Utama | Link | Sistem | Config | Data**, logical viewport **480x320**, login PIN 6 digit dengan keypad layar sentuh.
- API loopback; login PIN dibatasi percobaan, dengan session dan CSRF/Origin checks.
- Halaman Utama menampilkan dial arah relatif (0 di atas, searah jarum jam). Jarum dan kurva
  spektrum 360° (skala relatif per frame) hanya tampil untuk deteksi valid dengan sampel yang sama;
  sudut raw/UNVERIFIED tetap berupa angka. Ketuk dial untuk **mode baca jauh** layar penuh.
- Dial menampilkan jejak arah valid terakhir (maks. 8 titik, 30 detik; dihapus saat deteksi tidak
  valid). Baris CMD menampilkan stage operasi apa adanya dengan warna, plus notifikasi singkat saat
  operasi selesai/gagal. Ikon tab diberi titik kuning/merah bila halaman itu memuat masalah, dan
  halaman dapat digeser kiri/kanan di layar sentuh.
- `Config > Tampilan` menyimpan mode gelap/terang/malam (merah), aksen warna, font lokal, dan
  **layar hitam otomatis** (mati, 1, 5, atau 15 menit tanpa sentuhan). Tombol `Config > Layar hitam`
  menghitamkan layar seketika. Layar hitam hanya menampilkan jam redup (jam sistem Pi; ditandai
  BELUM SINKRON bila Edge melaporkan jam belum dipercaya). Ketukan pertama hanya menyalakan layar (tidak menekan tombol di
  bawahnya). Layar tidak dihitamkan otomatis selama ada alarm error, dan alarm error baru
  menyalakannya kembali. Ini overlay hitam, bukan pemutusan backlight; backlight tetap diatur OS.
- Frekuensi center + VFO0 diisi lewat keypad angka di layar, sehingga tidak memerlukan keyboard fisik.
- `Data > Atur` mengubah host/port, transport MQTT/TCP atau WebSocket, pilihan TLS, dua pasangan akun, dan Client ID dasar (akhiran `-control`/`-bulk`).
  TLS aktif memverifikasi sertifikat/nama host; TLS nonaktif mengirim kredensial dan payload tanpa enkripsi—gunakan hanya pada link tepercaya.
  Pengaturan disimpan lokal dan diterapkan tanpa restart layanan; perubahan koneksi memulai sesi transport baru.
- Daftar Data memuat topic/payload keluar dan command dari Ground; angular terkompresi, raw IQ tidak dikirim.
- Jurnal command SQLite, ID dedup, expiry/session/revision validation, satu mutasi aktif.
- Safe settings patch dan Start/Stop/Restart stack SDR hanya memakai target helper yang fixed; panel Edge tetap mengikuti approval lokal, Ground tidak perlu approval root remote.
- Reboot/shutdown Ground memakai challenge + konfirmasi operator tanpa approval root; Ground juga dapat meminta restart fixed `t900-ppp.service`.
- Receiver Ground dan preview grafik/API di port **8791**, terpisah dari dashboard lama.
- Mode DEMO yang tidak mengirim MQTT dan tidak menulis hardware.
- Kedua panel mempertahankan snapshot terakhir sebagai STALE saat API gagal; Retry melakukan fetch segera, polling rutin tetap berjalan, dan tidak ada recovery mutatif otomatis.

**Tidak dikirim:** raw IQ, waterfall, audio, video, logs panjang, atau full settings rutin.
**Tidak dipasang:** driver layar, autopilot, engine KrakenSDR, atau konfigurasi radio/PPP baru.

## 2. Persyaratan

Raspberry/Ubuntu Linux yang sudah boot dengan **systemd**, Python **3.10 atau lebih baru**,
modul standar `ssl` dan `sqlite3`, dan ruang disk untuk aplikasi/jurnal. Panel membutuhkan
**desktop grafis yang sudah bekerja** serta Chromium dari OS. Backend tetap bekerja tanpa layar.

Aplikasi tidak membutuhkan Python package download. Parser YAML pure-Python dan lisensinya
sudah disertakan. `acl` dibutuhkan bila installer memberikan user service akses baca folder SDR.

Periksa pada Raspberry:

```bash
/usr/bin/python3 --version
command -v chromium || command -v chromium-browser
command -v setfacl
```

Bila Chromium/ACL belum ada, install melalui repository OS (koneksi internet diperlukan untuk
paket OS ini saja):

```bash
sudo apt update
sudo apt install -y python3 acl chromium
```

Nama paket browser dapat berbeda antar OS. Jangan mengubah driver atau firmware layar yang
sudah berfungsi hanya untuk memasang aplikasi ini. Resolusi 480x320 harus sudah tersedia
pada desktop; argumen kiosk tidak memasang driver HDMI/DSI/SPI atau mengkalibrasi touchscreen.

## 3. Install pada Raspberry

Salin ZIP ke Raspberry, kemudian:

```bash
unzip rdf-node-1.0.0.zip
cd rdf-node
sudo bash install.sh --kiosk=rdf
```

`rdf` adalah akun desktop dari percakapan project. Ganti hanya bila akun desktop Anda berbeda.
Installer akan memverifikasi checksum, membuat user service `rdf-edge`, menempatkan release
pada `/opt/rdf-node/releases/`, membuat konfigurasi, serta mengaktifkan edge dan helper.

### Wizard sumber RDF

Wizard mencari kandidat folder `_share` secara read-only. Pilih folder **yang benar-benar
berisi output engine aktif**, bukan folder contoh atau repository lama. Path lama
`/home/doasdr/doasdr/_share` tidak dipaksakan karena akun deployment sekarang berbeda.
Setujui akses ACL hanya untuk folder output dan traversal parent yang diperlukan.

Apabila sumber belum diketahui, tekan Enter. Panel tetap hidup dengan **SETUP REQUIRED**;
DoA/graph tidak akan diterbitkan dengan data tebakan. Setup dapat diulangi:

```bash
sudo rdf-node setup
```

Setup otomatis memilih `rdfsdr.service` bila unit itu terdaftar. Konfigurasi lama yang
menunjuk unit lain tidak ditimpa; ubah eksplisit dengan `sudo rdf-node setup --engine-unit
rdfsdr.service`. Tekan Enter pada prompt folder sumber untuk mempertahankan path yang sudah
tersimpan. Unit lain hanya dipilih eksplisit setelah diverifikasi.

### Buka panel sekarang

Dari terminal **desktop grafis Raspberry**, bukan shell SSH tanpa display:

```bash
rdf-kiosk-session
```

Alternatif buka browser lokal ke:

```text
http://127.0.0.1:8790
```

Backend langsung aktif, tidak perlu reboot. Autostart browser dijalankan dalam graphical
session melalui XDG autostart, dan entri labwc bila konfigurasinya ada. Installer **tidak
mengaktifkan autologin OS**. Agar layar muncul setelah cold boot, OS harus masuk graphical
session yang sesuai; verifikasi nanti pada maintenance window.

### PIN admin

PIN admin tepat 6 digit dibuat acak saat instalasi dan hanya disimpan pada perangkat.
PIN lebih mudah ditebak daripada password panjang. API tetap loopback dan membatasi 6
kegagalan per menit; jangan ubah binding agar panel terbuka ke LAN.

```bash
sudo cat /etc/rdf-node/initial-admin-pin.txt
```

Jangan bagikan PIN, credential, atau hash ke chat/Git. Untuk mengganti PIN Raspberry:

```bash
sudo rdf-node set-pin
sudo systemctl restart rdf-edge.service
```

Untuk Ground, gunakan `sudo rdf-node set-pin --config /etc/rdf-ground/config.yaml`, lalu
restart `rdf-ground.service`. Instalasi lama yang masih memakai password harus mengganti
kredensial melalui CLI sebelum login PIN; hash lama tidak dapat dikonversi menjadi PIN.
Restart mencabut sesi admin aktif. File `initial-admin-pin.txt` tidak berubah setelah PIN
diganti; hapus salinan awal dengan aman bila tidak diperlukan. Saat upgrade, hapus juga
file awal `initial-admin-password.txt` lama setelah mengganti PIN.

### Cek service dan sumber

```bash
systemctl status rdf-edge.service rdf-control-helper.service --no-pager
sudo -u rdf-edge rdf-node doctor
curl -fsS http://127.0.0.1:8790/api/v2/healthz
```

`doctor` membaca sumber dan routing; tidak membuka ulang dongle SDR, tidak reset USB,
tidak stop engine. Keberhasilan HTTP tidak dianggap bukti DAQ sehat.

## 4. Menyambungkan MQTT ke Ubuntu

PPP yang sudah berhasil tetap dipakai:

```text
Raspberry 10.90.0.2 -> T900 -> Ubuntu 10.90.0.1
```

MQTT belum otomatis tersedia hanya karena PPP hidup. Untuk setup pertama, paket ini
menyediakan **broker TLS terpisah + Ground receiver/preview**. Langkah di bawah dilakukan
**di Ubuntu**, bukan di Raspberry:

```bash
unzip rdf-node-1.0.0.zip
cd rdf-node
sudo apt update
sudo apt install -y python3 mosquitto mosquitto-clients openssl
sudo bash install.sh --ground
sudo bash scripts/provision-ground.sh
```

Provisioner mendukung **Mosquitto 2.x** dan menolak versi lain untuk tidak mengasumsikan
syntax konfigurasi. Ia tidak menulis ulang konfigurasi broker/dashboard lama.
Unit broker baru bernama `rdf-ground-mqtt.service`; listener hanya
`10.90.0.1:8883` dan `127.0.0.1:8883`. Pastikan port tersebut tidak dipakai broker lain.
Broker menunggu alamat PPP tersedia sebelum start, lalu retry bila belum tersedia.

Provisioner membuat CA/server certificate dan password unik lokal, serta empat akun dengan
ACL terpisah: node control, node bulk, Ground controller, Ground viewer. Tidak ada
password/certificate private key produksi di ZIP ini.

Pada Ubuntu dengan user `rdf`, bundle default berada di:

```text
/home/rdf/rdf-uav-bundle/
```

Salin **folder itu** melalui media atau jalur management aman ke Raspberry, misalnya
`/home/rdf/rdf-uav-bundle`. Jangan gunakan source ZIP untuk membawa secret ke Git.
Kemudian di Raspberry:

```bash
sudo rdf-node import-bundle /home/rdf/rdf-uav-bundle
sudo systemctl restart rdf-edge.service
```

Bundle hanya membawa public CA serta credential node, **bukan private key CA**.
Simpan atau hapus bundle transfer secara aman setelah provisioning.

Di Ubuntu, buka preview Ground:

```text
http://127.0.0.1:8791
```

PIN admin Ground:

```bash
sudo cat /etc/rdf-ground/initial-admin-pin.txt
```

Receiver berjalan walaupun browser Ground ditutup. Ini pendamping integrasi, bukan
pengganti source aplikasi dashboard lama yang tidak disertakan pada project ini.
Integrasi dashboard lama dapat membaca API receiver lokal atau memakai decoder yang disediakan.
Jangan menjalankan dua receiver Ground untuk node yang sama dengan MQTT client ID yang sama; broker akan mengganti koneksi.

Menggunakan broker yang sudah ada juga didukung: atur host, port, transport, credential, dan CA
(bila TLS aktif) pada `/etc/rdf-node/config.yaml` mengikuti [MQTT_GROUND.md](docs/MQTT_GROUND.md).
`tls` tetap aktif secara default dan verifikasi sertifikat/nama host tidak dapat dimatikan saat TLS
digunakan. `tls: false` memilih TCP atau `ws://` biasa; credential dan payload tidak terenkripsi,
jadi gunakan hanya melalui link privat/tepercaya dengan listener broker plaintext yang cocok.

## 5. Mengizinkan data RDF asli menjadi LIVE

Default `authority_verified: false` dan `angle_verified: false` sengaja mencegah source
lama/salah orientasi menjadi telemetry authoritative. Health boleh terkirim lebih dahulu.
Pastikan native data berubah, DAQ sehat, frame maju, jam valid, dan orientasi sudah dicocokkan.
Lalu:

```bash
sudo rdf-node setup --verify-source
sudo systemctl restart rdf-edge.service
```

Pada wizard, pilih sumber aktif atau biarkan sumber yang sudah disimpan; ketik `VERIFIED`
hanya setelah validasi. Konvensi default `theta_mirror` adalah `(360 - CSV_DoA) mod 360`
sesuai dokumen upstream. `csv_native` dapat dipilih dalam config bila integrasi memang
menggunakan konvensi itu. Array angular mempertahankan urutan native 0..359.

Clock menggunakan `timedatectl NTPSynchronized`; bila belum dipercaya, panel tetap berjalan
namun LIVE/command berdeadline ditahan. Perbaiki sinkronisasi waktu melalui management/NTP
OS yang benar. Paket ini tidak diam-diam menyetel jam atau membuat NTP melalui T900.

Bulk Angular mulai setelah gate source, transport, backlog, dan stabilisasi Edge lulus; tidak
menunggu konfirmasi aplikasi Ground.
`GRAPH PAUSED` selama setup bukan data hilang tersembunyi; alasannya ada di tab Link.

## 6. Interval bawaan

| Data | Jadwal |
|---|---|
| Baca DoA lokal | 250 ms |
| Baca status DAQ | 500 ms |
| Periksa settings | 1 detik |
| Panel mengambil snapshot | 500 ms |
| Current DoA radio | 1 detik; hanya sampel baru valid |
| Health radio | 1 detik; tetap berjalan saat RDF STOPPED |
| Detail health | 10 detik |
| Grafik Angular (`balanced`) | Minimum 4 detik; ukuran JSON dan budget Bulk dapat memperpanjang |
| Settings export | Hanya setelah `cmd/config/get`; tidak ada report otomatis |
| State/config | event/reconnect/request; state refresh 60 detik |

Profil CONTROL mematikan grafik. Profile `graph_u8` tetap tersedia untuk konfigurasi lama dan
memiliki interval minimum 2 detik, tetapi kini memakai JSON yang sama, bukan encoding U8.
Nav/spectrum/audio/recording tidak diaktifkan. Jadwal bukan jaminan throughput: saat token
budget/receiver/clock/source tidak memadai, data lama dibuang dan grafik dipause. Tidak ada
jaminan performa T900 tanpa capture nyata.

## 7. Ground remote writes

Ground mengirim command write yang didukung secara langsung lewat MQTT Control, tanpa
`controls approve --remote` atau approval root per aksi. Ground UI tetap perlu PIN login+CSRF;
Edge tetap memeriksa session/boot, expiry, clock, revision, health, validasi field, jurnal, dan
helper. Ground mempertahankan konfirmasi UI untuk stop/restart, PPP, reboot, dan shutdown.

Target operasi yang tetap harus disiapkan sekali:

- Safe settings hanya tersedia bila helper memiliki path settings fixed dan konfigurasi menyatakan
  single writer. Ground patch melewati flag `allow_config`, tetapi tetap memerlukan target dan
  compare-digest; perubahan writer bersamaan dapat ditolak sebagai conflict.
- Start/Stop/Restart hanya tersedia untuk unit SDR fixed yang diaudit dan memakai stop-intent
  guard; tidak ada unit atau shell command dari payload.
- PPP restart hanya menargetkan `t900-ppp.service`, dan helper mengecek unit loaded, active, tanpa
  systemd job pada tiap request.
- Reboot/shutdown tetap memakai Prepare/Execute, challenge sekali pakai 30 detik, konfirmasi akhir,
  intent/jurnal durable, dan tidak mengulang otomatis.

Field config lama `remote_commands_enabled` dan policy `allow_remote_control` tetap diterima untuk
membaca instalasi lama, tetapi tidak lagi membatasi Ground. `controls approve` sekarang mengatur
aksi panel Edge lokal; `--remote` sudah dihapus, sedangkan `--reboot` tetap mengatur gate lokal.
Menjalankan `controls approve` tanpa flag **tidak mencabut** Ground. Batasi publish `cmd/#` melalui
ACL broker dan cabut credential Ground di broker untuk menutup akses remote. Edge tidak membuktikan
bahwa publisher MQTT benar-benar Ground; link private peer-to-peer sendiri bukan autentikasi publisher.

Sebelum mengaktifkan Ground, pastikan akun broker dan ACL topic hanya mengizinkan Ground yang
dipercaya. TLS aktif memverifikasi sertifikat/nama host broker; autentikasi publisher tetap tugas
broker. Ground memerlukan MQTT Control ready dan health/session node fresh untuk mengirim mutasi.
Untuk safe settings, jalankan `sudo rdf-node controls approve --settings` sekali untuk menetapkan
path fixed dan mengonfirmasi single writer. Untuk lifecycle, `--lifecycle` mengaudit unit SDR serta
watchdog yang dikenal sebelum memasang stop-intent guard; jangan beri helper unit yang belum diaudit.

Untuk menyiapkan target lifecycle dan gate panel lokal, jalankan dari terminal Raspberry:

```bash
sudo rdf-node setup --engine-unit rdfsdr.service
sudo rdf-node controls approve --lifecycle --ppp-restart --reboot --shutdown
```

Approval meminta `APPROVE`, `AUDITED`, `T900 PPP RESTART`, `REBOOT`, dan `SHUTDOWN`; PPP
melakukan preflight unit. `--lifecycle` menyiapkan target Ground yang fixed/audited, sementara flag
lain mengatur gate panel Edge lokal. Ground tidak memakai approval root per command.

Approval root tersimpan di helper policy/config dan berlaku lintas reboot. `--lifecycle`,
`--ppp-restart`, `--reboot`, dan `--shutdown` mengaktifkan aksi Edge terkait; setelah provisioning,
aksi ini tidak memerlukan timed lease atau `sudo` per aksi. Settings memakai approval
single-writer `--settings` yang terpisah.

Panel Config -> Login -> Kontrol memerlukan sesi Admin dengan PIN. Start/Stop/Restart mengelola
unit `link.engine_service` yang fixed dan audited (`rdfsdr.service` untuk konfigurasi ini), bukan
`rdf-edge.service` atau unit PPP.
Reboot lokal memerlukan approval `--reboot`, challenge satu kali 30 detik, jurnal durable, dan
konfirmasi akhir pada layar. Ground reboot memakai jalur terpisah dan tidak memerlukan approval
root lokal.
Shutdown lokal memerlukan approval `--shutdown`, challenge, dan konfirmasi layar. Ground shutdown
tidak memakai approval root lokal.
Sebelum menjadwalkan, helper menyimpan intent durable dan menolak semua prepare/execute shutdown
berikutnya pada boot yang sama, termasuk dari sesi UI lain. Helper menjadwalkan
`/usr/bin/systemctl poweroff` melalui unit transient tetap dalam 5 detik; `SHUTDOWN_SCHEDULED`
bukan bukti OS sudah mati. API dan kedua panel mempertahankan status belum pasti lintas polling
dan sesi; tidak ada retry otomatis. Setelah memeriksa Pi secara lokal, gunakan `sudo rdf-node
controls shutdown-reconcile` hanya bila unit shutdown lama tidak aktif. Perintah root ini
meminta konfirmasi ketik `SHUTDOWN RECONCILED` dan helper hanya menghapus intent setelah
`systemctl show` memastikan unit timer serta service tidak aktif; status aktif/tidak diketahui
ditolak. Marker boot sebelumnya dibersihkan pada pemeriksaan helper berikutnya. Perangkat harus
dinyalakan lagi secara lokal.
Restart T900 PPP di Ground menargetkan hanya `t900-ppp.service`, tanpa approval root. Pada setiap
request helper memerlukan unit `loaded`, `active`, dan tanpa systemd job pending. Jika MQTT
Control sudah terputus, Ground tidak dapat mengirim request. `PPP_RESTART_REQUESTED` hanya
berarti systemd menerima permintaan; `APPLIED` menunggu health node yang lebih baru dalam sesi
yang sama dan tidak membuktikan unit PPP/link pulih. Ground tidak mengirim ulang hasil unknown;
request baru memerlukan health fresh dan konfirmasi tambahan. Uji unit serta waktu pemulihan
tetap perlu dilakukan pada Raspberry.
Form Ground hanya mengirim perubahan pada field allowlist: center+VFO0 bersama, bandwidth,
gain, dan squelch. Rentang helper dan batas bukti runtime dijelaskan di [CONTROL.md](docs/CONTROL.md).
Read-back file saja menghasilkan **PERSISTED_UNVERIFIED**, bukan APPLIED. Beberapa engine tidak
menyediakan bukti runtime center/gain pada `status.json`.

## 8. Tes tanpa hardware dan troubleshooting

Demo tidak memerlukan install sebagai root:

```bash
python3 run.py demo --port 8790
```

Jangan menjalankan demo pada port yang sudah dipakai service. Gunakan `--port 18790`
untuk preview tambahan. Demo membuat fixture pada temp/state dir, tidak menyentuh engine asli,
MQTT OFF, dan watermark DEMO tidak disembunyikan.

Tes lokal:

```bash
python3 run.py selftest
```

Tes memakai temporary files, broker fixture loopback, serta mock untuk systemd/reboot/shutdown.
Tidak ada perintah reboot atau shutdown nyata dari test suite. Jalankan tanpa sudo bila tidak diperlukan.

| Masalah | Langkah |
|---|---|
| SETUP REQUIRED | `sudo rdf-node setup`; pilih output aktif |
| Permission source | `sudo -u rdf-edge rdf-node doctor`; setup ACL |
| SOURCE/ANGLE UNVERIFIED | Validasi asli, lalu `setup --verify-source` |
| CLOCK UNTRUSTED | Periksa time sync OS; jangan bypass freshness |
| MQTT DISABLED | Provision/import bundle atau konfigurasi broker; plaintext hanya untuk link privat tepercaya |
| TLS error | Periksa CA/SAN/clock broker; jangan matikan verifikasi |
| Ground tidak melihat telemetry | Periksa receiver, subscription ACL, session dan health; MQTT CONNECTED di Edge bukan bukti receiver memproses data |
| Ping peer PPP | Tab Link: satu ping ICMP tiap sekitar 5 detik dipaksa melalui interface PPP; tanpa balasan bukan bukti link mati. |
| Grafik tidak mulai | Tab Link: gate source/angle, backlog/stabilisasi, CONTROL profile |
| CAPABILITY DISABLED | Panel Edge lokal: pastikan mode controlled dan approval root satu kali untuk aksi; Ground: periksa helper target fixed/audited, health/session, dan kesiapan MQTT Control. |
| PPP restart tidak tersedia | Cek capability, health fresh, MQTT Control, dan status unit fixed pada Raspberry |
| PERSISTED_UNVERIFIED | Settings tersimpan; runtime evidence belum lengkap, bukan masalah ACK MQTT |
| Browser tidak muncul | Jalankan `rdf-kiosk-session` dari desktop grafis; launcher memakai `--disable-gpu`. Pastikan `rdf-edge.service` aktif pada `127.0.0.1:8790`. |

Log secukupnya saja, terutama melalui radio:

```bash
journalctl -u rdf-edge.service -n 60 --no-pager
journalctl -u rdf-control-helper.service -n 40 --no-pager
```

Jangan membagikan full settings, credential bundle, file hash/password atau log mentah sensitif.

## 9. Lokasi penting dan dokumentasi

| Lokasi | Isi |
|---|---|
| `/opt/rdf-node/current` | Release aplikasi aktif |
| `/etc/rdf-node/config.yaml` | Config bridge Raspberry |
| `/etc/rdf-node/helper.yaml` | Approval/helper policy root-only |
| `/var/lib/rdf-node` | Jurnal dan preferensi bridge |
| `/var/lib/rdf-node-control` | Stop/shutdown intent dan backup settings root-only |
| `/etc/rdf-ground/config.yaml` | Config receiver Ubuntu |
| `/etc/rdf-ground-mqtt` | TLS/ACL broker terpisah |

- [Arsitektur dan perbedaan dari planning](docs/IMPLEMENTATION.md)
- [Protokol wire dan integrasi Ground](docs/PROTOCOL.md)
- [Setup MQTT Ground](docs/MQTT_GROUND.md)
- [Command, bukti hasil, dan keselamatan lifecycle](docs/CONTROL.md)
- [Operasi, backup dan rollback](docs/OPERATIONS.md)
- [Pengujian dan keterbatasan verifikasi](docs/TEST_REPORT.md)
- [Sumber teknis dan lisensi](docs/SOURCES.md)
- [Screenshot panel](evidence/overview-480x320.png)

Lisensi source baru: MIT. Parser YAML yang disertakan memiliki lisensi MIT terpisah.
Tidak ada source DSP KrakenRF, font file, data produksi, atau private key yang dibundel.
