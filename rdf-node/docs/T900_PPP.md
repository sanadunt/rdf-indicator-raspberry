# T900/PPP: restart dari panel, pemulihan link, dan pencegahan

Dokumen ini untuk operator yang menyiapkan terbang dan untuk teknisi yang merawat link
Raspberry (Edge, `10.90.0.2`) ⇄ T900 ⇄ Ubuntu (Ground, `10.90.0.1`).

RDF Node **tidak** memiliki PPP, udev, atau radio T900. `install.sh` tidak memasang atau mengubah
unit PPP, aturan udev, maupun opsi pppd. Satu-satunya aksi PPP dari RDF Node adalah restart unit
tetap `t900-ppp.service` di Raspberry lewat helper root. Contoh konfigurasi untuk pengerasan link
ada di [`deploy/examples/t900/`](../deploy/examples/t900/README.md) dan harus dipasang manual.

## 1. Ringkasan

- Tombol **Restart T900** di panel Edge (`Config > Login > Kontrol`, bagian **Raspberry**)
  me-restart `t900-ppp.service` di Raspberry. Tombol ini tidak memutus daya radio, tidak
  me-reset USB, tidak menyentuh pppd di Ubuntu, dan tidak menyentuh broker cadangan.
- Setelah restart diminta, panel menampilkan kartu progres sampai Edge punya bukti bahwa link
  ke Ground benar-benar kembali: **Tersambung ke Ground ✓**. Bila belum ada bukti dalam 120 detik,
  hasilnya **belum pasti**, bukan sukses.
- Tombol lokal ini tetap bisa dipakai saat MQTT/PPP putus total. Tombol `Restart T900 PPP` di
  Ground hanya bisa dipakai selama MQTT Control utama masih tersambung.
- Kebutuhan cabut-colok T900 dapat dikurangi dengan pengaturan di **kedua** sisi (bagian 6).

## 2. Persiapan satu kali (Raspberry)

Jalankan dari terminal Raspberry, dengan akses lokal atau LAN cadangan:

```bash
systemctl show t900-ppp.service -p Type -p LoadState -p ActiveState -p SubState -p Job
sudo rdf-node controls approve --ppp-restart
```

- Unit yang sehat dan tidak sedang diproses systemd menampilkan `LoadState=loaded`,
  `ActiveState=active`, dan `Job=` **kosong**. systemd menulis `Job=` tanpa angka bila tidak ada
  job; angka (misalnya `Job=87`) berarti job start/stop masih antre.
- `Type=` sebaiknya `simple` dengan pppd `nodetach`. Unit `notify` (`up_sdnotify`) atau `forking`
  dengan `updetach` membuat Restart T900 ditolak selama radio putus, dan unit `oneshot` (wrapper
  `pon`) harus diubah sebelum memasang drop-in `Restart=always`; lihat bagian 6.3.
- CLI meminta `APPROVE`, lalu memeriksa unit (harus `loaded`, tanpa job antre, dan `active`,
  `failed`, atau `activating/auto-restart`), lalu meminta teks `T900 PPP RESTART`.
- Approval menyimpan `allow_ppp_restart` di `/etc/rdf-node/helper.yaml`, mengaktifkan
  `control.ppp_restart_enabled`, menyetel `runtime_mode: controlled`, lalu me-restart
  `rdf-control-helper.service` dan `rdf-edge.service`. Approval berlaku lintas reboot, tanpa lease
  waktu dan tanpa `sudo` per aksi.
- Setiap pemakaian tetap memerlukan sesi PIN Admin dan konfirmasi di layar.

Bila tombol nonaktif, panel menampilkan alasannya:

| Teks di panel | Arti |
|---|---|
| `Helper kontrol belum tersedia.` | `rdf-control-helper.service` tidak berjalan atau socket helper tidak terbaca. |
| `Perlu approval root: sudo rdf-node controls approve --ppp-restart` | Mode bukan `controlled`, flag `control.ppp_restart_enabled` mati, atau helper belum mengizinkan. |

Di Ubuntu, pastikan pppd Ground juga pulih sendiri setelah Raspberry me-restart PPP (bagian 6).
Tanpa `persist` atau `Restart=always`, pppd Ground dapat keluar saat Raspberry memutus link dan
tidak tersambung lagi.

## 3. Prosedur pra-terbang

1. Nyalakan Raspberry, radio, dan Ubuntu. Tunggu boot selesai.
2. Buka tab **Link** di panel Edge, tampilan **Koneksi**:
   - `T900 USB` = `PRESENT` (bukan `MISSING`/`IDENTITY_MISMATCH`),
   - `PPP / interface` = `UP / ppp…`,
   - `Ping 10.90.0.1` = `BALASAN`,
   - `MQTT CTRL / BULK` = `CONNECTED / CONNECTED`.
   Di tampilan **Rincian**, `Layanan PPP` sebaiknya `ACTIVE`. Angka `restart N` yang terus naik
   berarti pppd sering keluar; periksa log (bagian 8).
3. Di Ground, pastikan health node fresh dan DoA/health bergerak.
4. Bila PPP `DOWN`, ping `TANPA BALASAN`, atau MQTT utama tidak `CONNECTED` lebih dari sekitar
   satu menit, buka `Config > Login > Kontrol` lalu tekan **Restart T900** di bagian
   **Raspberry**. Konfirmasi berbunyi: *Restart PPP T900? MQTT utama putus beberapa detik sampai PPP
   tersambung lagi. Broker cadangan (LAN/internet) tidak terpengaruh.*
   - Bila `T900 USB` = `MISSING`, konfirmasi menambah peringatan *T900 USB tidak terdeteksi —
     restart PPP tidak membantu; cek kabel/daya radio.* Perbaiki kabel/daya radio dulu. pppd yang
     masih berjalan dengan `persist` membuka device lagi sendiri setelah radio terdeteksi.
     Sebaliknya, pppd yang baru di-restart tanpa device langsung keluar; tanpa drop-in
     `Restart=always` (bagian 6.3) unit lalu berhenti `failed` dan tidak pulih sendiri saat radio
     dicolok lagi.
5. Ikuti kartu progres (bagian 4). Terbang hanya setelah kartu menampilkan
   **Tersambung ke Ground ✓** dan Ground kembali menerima health fresh.
6. Bila kartu berakhir kuning **Belum tersambung dalam 120 dtk**:
   - **Lihat tab Link sebelum menekan Coba lagi.** Link kadang pulih sedikit setelah 120 detik.
     Bila `PPP / interface` `UP`, ping `BALASAN`, dan `MQTT CTRL` `CONNECTED`, link sudah kembali:
     kartu berubah hijau dengan catatan tersambung setelah batas 120 dtk, dan tombol **Coba lagi**
     disembunyikan. Jangan me-restart lagi; restart memutus link yang baru kembali.
   - Baca alasan di kartu (tabel alasan di bagian 4). `T900_USB_MISSING` mengarah ke kabel/daya
     radio Raspberry. `MQTT_NOT_RECOVERED` berarti PPP dan ping sudah pulih; periksa broker Ground,
     TLS, dan akun MQTT, bukan radio. `PPP_RECOVERY_TIMEOUT` dan `PPP_CYCLE_NOT_OBSERVED` mengarah
     ke pppd/unit dan radio.
   - Periksa sisi Ubuntu: `systemctl status <unit-PPP-Ground>` dan log pppd Ground. Bila pppd
     Ground macet, restart unit itu di Ubuntu (`sudo systemctl restart t900-ground-ppp.service`
     atau nama unit site).
   - Periksa kabel USB, antena, dan daya kedua radio.
   - Bila link masih putus, tekan **Coba lagi** (paling cepat 15 detik setelah permintaan
     sebelumnya). Bila tombol tidak muncul karena approval atau helper hilang, kartu menampilkan
     petunjuk yang sama dengan dialog Kontrol.
7. Bila kartu menampilkan kuning **Hasil restart T900 belum pasti**, helper tidak memberi jawaban
   pasti dan systemd mungkin sudah me-restart unit. Lihat tab Link dulu; tekan **Coba lagi** hanya
   bila link masih putus.
8. Bila masih gagal, cabut-colok T900 di Ubuntu, lalu bila perlu di Raspberry (cabut kabel T900
   saja, jangan memutus daya USB Raspberry yang juga memasok KrakenSDR). Kumpulkan data diagnosis
   (bagian 8) sebelum menyalakan ulang apa pun, agar penyebabnya bisa dilacak.

Grafik Angular kembali paling cepat `telemetry.resume_stable_seconds` (default 20 detik) setelah
MQTT Control dan Bulk siap lagi. DoA dan health berjalan lagi begitu MQTT Control siap.

## 4. Arti kartu progres

Kartu muncul di dalam dialog yang sedang terbuka, atau sebagai banner di atas halaman. Restart
baru membangunkan layar hitam satu kali dan menahan layar hitam otomatis selama berjalan;
`Layar hitam` yang ditekan manual sesudahnya tetap hitam. Kartu tetap terlihat 90 detik setelah
selesai. Batas 90 detik dihitung Edge dengan jam monotonic, jadi kartu tidak hilang lebih cepat
saat jam Pi (tanpa RTC) melompat karena NTP tersinkron lewat link yang baru pulih. Asal permintaan
ditandai chip `[L]` (panel lokal), `[G]` (Ground), atau `[C]` (broker cadangan).

Selama `T900 USB` = `MISSING`, kartu juga menampilkan peringatan *T900 USB tidak terdeteksi —
restart PPP tidak membantu; cek kabel/daya radio.* `IDENTITY_MISMATCH` biasanya berarti
`link.expected_*` di config tidak cocok dengan port/VID/PID yang terpasang, bukan radio hilang.

| Langkah | Field `steps` | Bukti yang dipakai Edge |
|---|---|---|
| Restart diminta | `requested` | Helper menjalankan `systemctl --no-block restart t900-ppp.service` dan systemd menerima job. Stage operasi `PPP_RESTART_REQUESTED`. Ini belum bukti PPP turun atau naik. |
| PPP turun/naik | `cycled` | Edge melihat interface PPP sempat tidak `UP`, atau waktu aktif/`NRestarts` unit berubah, atau sesi MQTT Control baru dimulai. |
| PPP UP | `ppp_up` | Setelah siklus itu, interface dengan alamat `10.90.0.2` → peer `10.90.0.1` kembali `UP`. |
| Ping Ground | `peer_reply` | Ping ICMP ke `10.90.0.1` lewat interface PPP dibalas. |
| MQTT utama | `mqtt_ready` | Client MQTT Control ke broker utama `READY` dan sesinya baru, atau sudah menerima data broker setelah PPP naik. Tertulis `nonaktif` bila MQTT utama dimatikan (`null`). |

Selama pemantauan (sampai 150 detik), Edge memeriksa interface, unit, dan ping kira-kira setiap
detik, lebih rapat dari jadwal biasa (interface tiap ~2 detik, ping tiap ~5 detik).

| Hasil akhir (`phase`) | Tampilan | Stage operasi | Arti |
|---|---|---|---|
| `DONE` | hijau, **Tersambung ke Ground ✓** | `APPLIED`, `proof: {ppp:"UP", peer:"REPLY", mqtt:"READY"\|"DISABLED", cycled:true}` | PPP sudah berganti siklus, Ground membalas ping, dan MQTT utama tersambung lewat link baru. |
| `DONE` dengan `late: true` | hijau, dengan catatan tersambung setelah batas 120 dtk; tanpa **Coba lagi** | tetap `OUTCOME_UNKNOWN` dengan `reason` timeout | Link pulih setelah 120 detik, selama hasil `TIMEOUT` masih ditampilkan. Jurnal dan ACK tidak ditulis ulang, jadi baris CMD dan Ground tetap menunjukkan `OUTCOME_UNKNOWN`. |
| `TIMEOUT` | kuning, **Belum tersambung dalam 120 dtk**, tombol **Coba lagi** | `OUTCOME_UNKNOWN`, `reason` dari tabel alasan di bawah | Belum ada bukti link pulih dalam 120 detik. Hasil belum pasti, bukan gagal pasti. |
| `FAILED` dengan stage `OUTCOME_UNKNOWN` | kuning, **Hasil restart T900 belum pasti**, tombol **Coba lagi** | `OUTCOME_UNKNOWN` dengan `error` (`SYSTEMD_ACTION_OUTCOME_UNKNOWN`, atau timeout/error socket helper seperti `TIMEOUTERROR`) | Helper tidak memberi jawaban pasti; systemd mungkin sudah me-restart unit. Langkah kartu tidak dipantau lagi; lihat tab Link. |
| `FAILED` | merah, **Restart T900 gagal**, dengan alasan | `FAILED` dengan `error` | Permintaan restart sendiri ditolak helper atau gagal (tabel penolakan di bawah). |

Selama hasil `TIMEOUT` masih ditampilkan (90 detik), Edge tetap menilai bukti link dan memperbarui
langkah di kartu. Begitu ping Ground dibalas dan MQTT utama siap (atau nonaktif), tampilan berubah
menjadi `DONE` dengan `late: true`.

**Coba lagi** hanya muncul bila restart dapat dijalankan (`caps.ppp_restart`) dan link belum sehat.
Saat tab Link menunjukkan PPP `UP`, ping dibalas, dan MQTT utama siap (atau nonaktif), tombol itu
disembunyikan agar link yang baru kembali tidak diputus lagi. Bila approval atau helper belum ada,
kartu menulis petunjuknya (`Perlu approval root: sudo rdf-node controls approve --ppp-restart` atau
`Helper kontrol belum tersedia.`).

Alasan `TIMEOUT` dipilih dari bukti yang sudah dilihat Edge saat batas 120 detik, dengan urutan
prioritas:

| `reason` | Dipilih bila | Tindakan |
|---|---|---|
| `T900_USB_MISSING` | `T900 USB` = `MISSING` (alias `/dev/t900` tidak ada). | Periksa kabel, port, dan daya radio di Raspberry. Restart PPP tidak membantu selama radio tidak terdeteksi. |
| `MQTT_NOT_RECOVERED` | PPP naik dan ping Ground dibalas, tetapi MQTT utama belum terbukti siap. | Link radio sudah pulih. Periksa broker Ground (service broker di Ubuntu), error `MQTT CTRL` di tab Link, TLS/jam, dan akun/ACL MQTT. Jangan cabut-colok T900. |
| `PPP_RECOVERY_TIMEOUT` | Siklus PPP terlihat, tetapi PPP/ping belum pulih. | Periksa pppd Ubuntu, kabel, antena, dan daya kedua radio. |
| `PPP_CYCLE_NOT_OBSERVED` | Tidak ada bukti unit atau interface pernah restart. | Periksa `systemctl status t900-ppp.service` dan `Type=` unit (bagian 6.3). |

`APPLIED` tidak membuktikan radio T900 di-reset, tidak membuktikan receiver Ground memproses data,
dan tidak berarti grafik sudah mengalir. Periksa health di Ground.

Penolakan dan kegagalan yang dapat muncul:

| Kode | Arti dan tindakan |
|---|---|
| `PPP_RESTART_IN_PROGRESS` | Restart sebelumnya masih berjalan atau masih dipantau (maks. 120 detik). Tunggu hasilnya. |
| `PPP_RESTART_TOO_SOON` | Kurang dari 15 detik sejak permintaan restart sebelumnya, dari asal mana pun. |
| `CAPABILITY_DISABLED` | Gate lokal belum lengkap; jalankan approval di bagian 2. |
| `PPP_RESTART_NOT_APPROVED` | Policy helper belum mengizinkan restart dari panel lokal. |
| `PPP_SERVICE_NOT_LOADED` | Unit `t900-ppp.service` tidak ada di systemd, atau tidak dapat dimuat (`LoadState` bukan `loaded`, misalnya `bad-setting` setelah drop-in yang tidak cocok dengan `Type=`; bagian 6.3). |
| `PPP_SERVICE_STATUS_UNKNOWN` | Helper gagal membaca status unit lewat `systemctl show`: perintah gagal, `LoadState`/`ActiveState` kosong, atau baris `Job` tidak ada. `Job=` kosong adalah normal. |
| `PPP_SERVICE_NOT_ACTIVE` | Unit sedang `activating` (selain `auto-restart`) atau `deactivating`, atau `inactive` dan permintaan datang dari Ground/cadangan. Unit `Type=notify` (pppd `up_sdnotify`) atau `Type=forking` dengan `updetach` tetap `activating/start` sampai link PPP pertama terbentuk, jadi restart ditolak justru saat radio putus; ubah unit (bagian 6.3). |
| `PPP_RESTART_PENDING` | systemd masih punya job untuk unit (`Job=` berisi nomor); tunggu job selesai. |
| `SYSTEMD_ACTION_OUTCOME_UNKNOWN` | Pemanggilan systemd tidak memberi hasil pasti; stage `OUTCOME_UNKNOWN`. Periksa unit secara lokal. |

Helper menerima unit `active`, `failed`, dan `activating/auto-restart` dari semua asal. Unit
`inactive` hanya dapat di-start lagi dari panel lokal, karena unit yang berhenti dapat merupakan
`systemctl stop` yang disengaja.

## 5. Mengapa cabut-colok T900 berhasil

Mencabut lalu mencolok T900 USB sekaligus melakukan tiga hal:

1. **Power-cycle radio.** T900 mengambil daya dari USB, jadi firmware radio mulai ulang, sinkron
   RF dibangun ulang, dan buffer radio kosong.
2. **Re-enumerasi USB.** Chip USB-serial (CH340) dideteksi ulang, driver membuat `ttyUSB` baru,
   dan keadaan driver/tty yang macet hilang. Tanpa alias udev, nomor `ttyUSBn` dapat berubah.
3. **Restart pppd.** pppd kehilangan device (hangup), lalu keluar atau membuka ulang device;
   negosiasi LCP/IPCP dimulai dari awal, dan pppd lawan ikut bernegosiasi ulang.

Tombol **Restart T900** hanya melakukan langkah 3 di Raspberry. Bila masalahnya radio macet,
chip USB-serial macet, atau pppd di Ubuntu yang macet, tombol ini saja tidak cukup. Log di
bagian 8 membedakan kasus-kasus tersebut.

## 6. Membuat cabut-colok tidak perlu (kedua sisi)

Semua langkah di bagian ini dilakukan manual oleh teknisi, dalam jendela maintenance dengan akses
lokal/LAN cadangan, pada Raspberry **dan** Ubuntu. Contohnya ada di
[`deploy/examples/t900/`](../deploy/examples/t900/README.md).

### 6.1 Nama device stabil (udev)

- Raspberry sudah memakai alias `/dev/t900`; RDF Node memeriksa VID/PID dan `ID_PATH` alias itu
  terhadap `link.expected_vid`, `link.expected_pid`, dan `link.expected_usb_path`.
- Lakukan hal yang sama di Ubuntu (`/dev/t900-ground`), lalu arahkan pppd Ground ke alias itu.
- Contoh: `99-t900-pi.rules` dan `99-t900-ground.rules`. Cari `ID_PATH` dengan
  `udevadm info --query=property --name=/dev/ttyUSB0`. `ID_PATH` mengikuti port fisik, jadi radio
  harus selalu dicolok ke port yang sama.

### 6.2 Opsi pppd

Contoh: `ppp-options.example`. Hal yang penting:

| Opsi | Nilai disarankan | Catatan |
|---|---|---|
| `persist`, `maxfail 0` | aktif | pppd mencoba lagi tanpa batas setelah link putus atau negosiasi gagal. |
| `holdoff` | `5` | Jeda sebelum mencoba lagi. |
| `lcp-echo-interval`, `lcp-echo-failure` | `10`, `6` | Link dinyatakan mati setelah sekitar 60 detik tanpa balasan echo. **Harus identik di kedua sisi**; sisi yang lebih ketat memutus link untuk keduanya. |
| `mtu`, `mru` | `576` | Sama di kedua sisi. Frame kecil membuang lebih sedikit data saat satu byte rusak. |
| `crtscts` / `nocrtscts`, `local` | `crtscts` hanya bila RTS/CTS benar-benar tersambung dan aktif di radio kedua sisi; selain itu `nocrtscts`. `local` di kedua kasus | Flow control yang salah dapat membuat TX macet lalu echo gagal. `local` membuat pppd mengabaikan garis modem (DCD) yang tidak dipakai adapter radio. |
| Baud serial | sama di kedua sisi dan sama dengan setelan radio | Catatan proyek memakai 57600. |
| `nodefaultroute` | aktif | Jangan jadikan PPP default route (bagian 6.5). |
| `nodetach` | sesuai `Type=` unit | `Type=simple` (disarankan) perlu `nodetach`. `Type=forking` tanpa `nodetach` dan tanpa `updetach`. Hindari `updetach` dan `up_sdnotify` (bagian 6.3). |

Pada banyak instalasi Debian/Ubuntu/Raspberry Pi OS, `/etc/ppp/options` bawaan sudah memuat
opsi seperti `auth`, `crtscts`, `modem`, dan `lcp-echo-*` dengan nilai berbeda. Opsi di file
`/etc/ppp/peers/<nama>` dibaca sesudahnya dan menang, jadi tuliskan opsi penting secara eksplisit
di sana dan periksa kedua sisi.

### 6.3 systemd: unit PPP hidup lagi sendiri

Contoh: `t900-ppp.service.d-10-recovery.conf` (`Restart=always`, `RestartSec=5`,
`StartLimitIntervalSec=0`). Pasang sebagai drop-in untuk `t900-ppp.service` di Raspberry dan untuk
unit PPP Ground di Ubuntu (nama unit milik site, misalnya `t900-ground-ppp.service`).
`persist` menangani putus link selama pppd hidup; `Restart=always` menangani pppd yang keluar.
`systemctl stop` manual tetap menghentikan unit. Selama jeda `RestartSec` unit berstatus
`activating/auto-restart`, dan tombol Restart T900 menerima status itu.

**Periksa `Type=` unit sebelum memasang drop-in** (Ubuntu: ganti dengan unit PPP Ground):

```bash
systemctl show t900-ppp.service -p Type -p LoadState
systemctl cat t900-ppp.service
```

| `Type=` unit | Drop-in `Restart=always` | Catatan |
|---|---|---|
| `simple` atau `exec`, pppd `nodetach` | cocok (**disarankan**) | Unit `active` segera setelah pppd jalan; `persist` mengurus link. Restart T900 diterima kapan pun. |
| `forking`, pppd tanpa `nodetach`/`updetach` | cocok | pppd lepas ke background segera setelah start, jadi unit cepat `active`. |
| `notify` dengan pppd `up_sdnotify`, atau `forking` dengan `updetach` | dimuat, tetapi **jangan dipakai** | Unit tetap `activating/start` dengan job start antre sampai link PPP pertama terbentuk, paling lama `TimeoutStartSec` (default 90 s), lalu gagal (dan diulang bila ada `Restart=`). Selama radio putus, Restart T900 ditolak `PPP_SERVICE_NOT_ACTIVE`, tepat saat dibutuhkan. Ubah ke `Type=simple` + `nodetach`. |
| `oneshot` (wrapper `pon`, biasanya dengan `RemainAfterExit=yes`) | **ditolak systemd** | Unit harus diubah dulu ke `Type=simple` + `nodetach` (lihat di bawah). |

systemd menolak `Restart=always` dan `Restart=on-success` untuk `Type=oneshot`. Pesannya: *Service
has Restart= set to either always or on-success, which isn't allowed for Type=oneshot services.
Refusing.* Unit lalu menjadi `LoadState=bad-setting`. Untuk `oneshot`, systemd hanya menerima
`Restart=no`, `on-failure`, `on-abnormal`, `on-watchdog`, dan `on-abort` (diperiksa pada sumber
systemd 247, 252, dan 255, serta dengan `systemd-analyze verify` pada systemd 255). Namun
`Restart=on-failure` pada wrapper `pon` hanya mengulang bila perintah start itu sendiri gagal.
pppd yang keluar belakangan tidak di-restart, dan unit tetap `active (exited)` walaupun pppd sudah
mati. Jadi `on-failure` bukan pengganti perubahan ke `Type=simple`.

Untuk unit `oneshot`, aktifkan baris `Type=simple` dan seterusnya yang tersedia (diberi tanda `#`)
di contoh drop-in. Hasilnya setara dengan:

```ini
[Service]
Type=simple
RemainAfterExit=no
ExecStart=
ExecStart=/usr/sbin/pppd call <nama-peer> nodetach
ExecStop=
Restart=always
RestartSec=5
```

`<nama-peer>` adalah file di `/etc/ppp/peers/` yang dipakai `pon` (argumen `pon` pada `ExecStart`
lama; `pon` tanpa argumen memakai `provider`). `ExecStart=` kosong menghapus perintah lama dan
`ExecStop=` kosong menghapus `poff`, sehingga systemd menghentikan pppd dengan SIGTERM. Hapus
`updetach` dari file peer bila ada.

Sesudah `sudo systemctl daemon-reload`, periksa:

```bash
systemctl show t900-ppp.service -p Type -p LoadState -p Restart -p RestartUSec -p NRestarts
```

`LoadState` harus `loaded`. Bila `bad-setting`, `systemctl status t900-ppp.service` menampilkan
`Loaded: bad-setting`, dan alasannya terlihat dengan
`journalctl -u t900-ppp.service -b --no-pager | grep -i refusing`. Perbaiki atau hapus drop-in,
lalu `daemon-reload` lagi. pppd yang sedang berjalan tidak langsung berhenti, sehingga kesalahan ini
mudah terlewat sampai PPP tidak start pada boot berikutnya. Tanda lainnya: tab Link langsung
menampilkan `Layanan PPP` `NOT_LOADED`, dan Restart T900 ditolak `PPP_SERVICE_NOT_LOADED`.

### 6.4 Daya

- T900 adalah radio daya USB. Arus puncak saat memancar jauh di atas arus saat diam; tegangan yang
  turun sesaat dapat me-reset chip USB-serial atau radio.
- Beri T900 sumber 5 V sendiri atau hub USB berdaya (powered hub) yang memadai, di Raspberry
  maupun di Ubuntu.
- Di Raspberry, port USB berbagi daya dengan KrakenSDR. **Jangan** memutus daya port USB Raspberry
  (misalnya dengan `uhubctl`) untuk me-reset T900, karena KrakenSDR ikut mati.
- Periksa under-voltage Raspberry dengan `sudo vcgencmd get_throttled`. Tab Sistem dapat
  menampilkan `unknown` bila service tidak berhak membaca nilai ini.

### 6.5 Broker cadangan jangan lewat `ppp0`

Broker cadangan harus lewat LAN/Wi-Fi/seluler, bukan PPP. Periksa:

```bash
ip route get <host-cadangan>
```

Bila keluar lewat `ppp0`, trafik cadangan (default sampai 8.000 B/s) ikut memakai T900 di luar
budget T900 dan dapat membuat LCP echo serta ACK MQTT terlambat. Pakai `nodefaultroute` di pppd
Raspberry kecuali site memang memerlukan default route lewat PPP. Lihat
[MQTT_GROUND.md](MQTT_GROUND.md#routing-jaringan).

### 6.6 Bandwidth pada link marginal

- Satu frame Angular berukuran sekitar 3 KB dan dikirim dalam satu PUBLISH. Pada link lemah,
  burst itu dapat menunda LCP echo dan ACK di antrean radio.
- Saat jarak jauh atau link marginal, pilih profil `control` (tanpa Angular) dari panel
  `Profil` atau `stream.set` Ground. Profil berlaku tanpa restart service.
- Atau turunkan `telemetry.bulk_budget_bytes_s` (rentang 200..500, default 350) di
  `/etc/rdf-node/config.yaml`, lalu `sudo systemctl restart rdf-edge.service`. Interval Angular
  ikut memanjang.
- Detail estimasi ada di [MQTT_TOPIC_SUMMARY.md](MQTT_TOPIC_SUMMARY.md#estimasi-bandwidth-untuk-15-kbits).

## 7. Perilaku MQTT setelah PPP kembali

- Saat interface PPP berubah dari tidak `UP` menjadi `UP`, atau ping peer kembali dibalas, Agent
  memanggil `kick()` pada client MQTT utama (Control dan Bulk). Client yang belum tersambung
  langsung mencoba lagi tanpa menunggu sisa backoff, dan backoff kembali ke nilai awal. Client
  cadangan tidak di-kick karena tidak lewat PPP.
- Backoff reconnect mulai 1 detik dan berlipat sampai `mqtt.reconnect_max` (default 30 detik).
  Sesi yang bertahan minimal 20 detik setelah CONNACK mengembalikan backoff ke awal; sesi yang
  putus lebih cepat terus menaikkan backoff, jadi tidak ada reconnect tiap detik.
- PINGREQ dikirim bila selama `mqtt.keepalive` detik (default 15) tidak ada yang dikirim,
  **atau** tidak ada data yang diterima dari broker, walaupun telemetry QoS 0 terus terkirim.
  Bila PINGRESP tidak datang dalam `keepalive` detik berikutnya, sesi direset (`PING_TIMEOUT`).
  Link mati terdeteksi dalam sekitar dua kali `keepalive`.
- Pengecualian untuk link lambat yang masih bekerja (T900 membawa lebih sedikit dari beban yang
  ditawarkan): PINGREQ antre di belakang data Edge sendiri, jadi PINGRESP terlambat, bukan hilang.
  Selama TCP masih maju, yaitu masih ada data Edge yang belum di-ACK atau PINGREQ belum terkirim,
  dan broker masih mengirim ACK TCP dalam `keepalive` detik terakhir, client menunggu PINGRESP
  sampai 4 kali `keepalive` (default 60 detik) sebelum `PING_TIMEOUT`. Link mati tidak mengirim
  ACK TCP, jadi tetap terdeteksi seperti biasa. Pemeriksaan ini memakai info socket Linux; bila
  tidak terbaca, berlaku aturan lama.
- Rincian di [MQTT_GROUND.md](MQTT_GROUND.md#reconnect-keepalive-dan-pemulihan-ppp).

## 8. Data diagnosis yang perlu dikumpulkan

Catat waktu kejadian, lalu korelasikan dengan ketinggian, jarak, dan profil telemetry. Ambil log
secukupnya; jangan menarik log besar lewat T900 saat telemetry berjalan.

Raspberry:

```bash
systemctl show t900-ppp.service -p Type -p LoadState -p ActiveState -p SubState -p Job -p NRestarts -p Result -p ExecMainStatus
systemctl cat t900-ppp.service
journalctl -u t900-ppp.service -o short-precise --since "-30 min" --no-pager
journalctl -k -o short-precise --since "-30 min" --no-pager | grep -iE 'ch341|ttyUSB|usb .*disconnect|voltage'
ip -s link show ppp0
ps -o args= -C pppd
readlink -f /dev/t900
udevadm info --query=property --name=/dev/t900 | grep -E '^(ID_PATH|ID_VENDOR_ID|ID_MODEL_ID)='
sudo vcgencmd get_throttled
ip route get <host-cadangan>
curl -s http://127.0.0.1:8790/api/v2/link
```

Ubuntu (ganti nama unit dan alias sesuai site):

```bash
systemctl show t900-ground-ppp.service -p Type -p LoadState -p ActiveState -p SubState -p NRestarts -p Result -p ExecMainStatus
journalctl -u t900-ground-ppp.service -o short-precise --since "-30 min" --no-pager
journalctl -k -o short-precise --since "-30 min" --no-pager | grep -iE 'ch341|ttyUSB|usb .*disconnect|brltty'
ip -s link show ppp0
readlink -f /dev/t900-ground
```

Nama interface bisa selain `ppp0`; lihat kolom interface di tab Link. Yang dicari di log pppd:

| Pesan / status | Petunjuk |
|---|---|
| `No response to N echo-requests`, `Serial link appears to be disconnected.` | Echo LCP habis; radio fade, buffer penuh, atau flow control salah. Bila pppd keluar karena ini, exit status 15. |
| `LCP terminated by peer` | Sisi lawan yang memutus lebih dulu; bandingkan waktu dengan log sisi lain. |
| `Modem hangup` | Device serial hilang atau hangup; periksa log kernel USB dan daya. Bila pppd keluar karena ini, exit status 16. |
| `ch341-uart converter now disconnected from ttyUSBn` | USB-serial terputus atau re-enumerasi; periksa kabel, daya, atau (Ubuntu) brltty. |
| `NRestarts` naik | pppd sering keluar dan di-restart systemd. |
| error/drop pada `ip -s link` | Frame rusak atau dibuang di interface PPP. |

Untuk RSSI radio, pakai alat konfigurasi T900 di darat; port serial dipakai pppd selama terbang.
Power KrakenSDR bukan RSSI T900. Bila ada radio 900 MHz lain di pesawat (misalnya telemetry
autopilot), catat juga statusnya pada waktu yang sama; dua radio yang putus bersamaan mengarah ke
daya, interferensi, atau USB bersama.

## 9. Field API dan MQTT terkait

- `GET /api/v2/snapshot` dan `GET /api/v2/link` (loopback Edge) memuat:
  - `link.ppp_service`: `{state, substate, restarts}`. `state` adalah `ActiveState` systemd unit
    `t900-ppp.service` dalam huruf besar (`ACTIVE`, `ACTIVATING`, `DEACTIVATING`, `FAILED`,
    `INACTIVE`), atau `NOT_LOADED`/`UNKNOWN`. `restarts` adalah `NRestarts` systemd (hitungan
    restart otomatis oleh `Restart=`; dapat kembali nol setelah start/restart manual), `null` bila
    tidak terbaca.
  - `link.ppp_recovery`: `null`, atau `{id, origin, phase, steps, stage, reason, started_ms,
    elapsed_s, timeout_s, late}` seperti bagian 4. `late` bernilai `true` hanya bila link pulih
    setelah `TIMEOUT` (`phase` `DONE`, `stage` tetap `OUTCOME_UNKNOWN`, `reason` tetap alasan
    timeout); Edge lama tidak mengirim field ini. Tetap ada 90 detik (jam monotonic Edge) setelah
    selesai; pulih terlambat memulai hitungan 90 detik baru. Disimpan di memori; bila `rdf-edge`
    restart, operasi yang belum final menjadi `OUTCOME_UNKNOWN`
    (`AGENT_RESTARTED_RECONCILE_NO_REPLAY`).
- `telemetry/health/detail` (tiap 10 detik, juga ke broker cadangan) memuat `ppp`, `peer`,
  `ppp_svc`, dan `ppp_restarts`, sehingga consumer di broker cadangan dapat melihat keadaan link
  utama. Lihat [MQTT_TOPIC_SUMMARY.md](MQTT_TOPIC_SUMMARY.md#telemetryhealthdetail).
- ACK `ack/operation` untuk `ppp.restart`: `ACCEPTED`, `PPP_RESTART_REQUESTED`, lalu `APPLIED` atau
  `OUTCOME_UNKNOWN` dari pemantau Edge (atau `FAILED`/`OUTCOME_UNKNOWN` bila helper gagal).
  `reason` timeout: `T900_USB_MISSING`, `MQTT_NOT_RECOVERED`, `PPP_RECOVERY_TIMEOUT`, atau
  `PPP_CYCLE_NOT_OBSERVED`. Pulih terlambat (`late`) hanya mengubah tampilan panel; tidak ada ACK
  baru.
  Receiver Ground bawaan tetap memakai rekonsiliasinya sendiri (health lebih baru pada sesi yang
  sama) dan tidak mengganti record-nya dengan ACK akhir dari pemantau Edge. Detail gate ada di
  [CONTROL.md](CONTROL.md#restart-layanan-ppp-t900).

## 10. Belum dibuktikan di perangkat

Test fixture memakai mock systemd/monitor. Waktu pemulihan nyata, perilaku pppd kedua sisi,
radio T900, daya USB, dan opsi contoh di `deploy/examples/t900/` belum diuji pada Raspberry/Ubuntu
dan radio sebenarnya. Format `Job=` kosong dan penolakan `Restart=always` untuk `Type=oneshot`
diperiksa terhadap sumber systemd dan `systemd-analyze verify` (systemd 255), bukan pada unit PPP
site. Uji dalam jendela terawasi dan catat hasilnya sebelum mengandalkannya untuk terbang.
