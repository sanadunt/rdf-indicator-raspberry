# Dashboard RDF Raspberry 480x320
## Rencana Implementasi Bridge Telemetry, Indikator Lokal, dan Kendali Terverifikasi

**Project:** Telemetry RDF  
**Nama aplikasi usulan:** RDF Node  
**Versi dokumen:** 1.0 - planning implementasi  
**Tanggal:** 29 September 2026  
**Target layar:** 5 inci, 480 x 320 pixel, landscape  
**Target host:** Raspberry sebagai node RDF; Ubuntu sebagai Ground Station  
**Status:** Rancangan, bukan software yang sudah dipasang atau diuji pada perangkat  
**Dokumen induk:** `Rancangan_Lengkap_RDF_Telemetry_Command_Dashboard_Raspberry_5inci.md`

> Keputusan utama: buat satu paket aplikasi RDF Node dengan backend bridge yang berjalan sebagai service dan layar indikator yang berjalan terpisah. Layar tidak menjadi pengirim MQTT, tidak menjalankan DSP, dan tidak menjadi syarat agar telemetry maupun command bekerja.
>
> Dokumen ini menurunkan rancangan induk menjadi rencana implementasi khusus Raspberry 480x320. Resolusi 800x480 pada dokumen induk digantikan secara eksplisit oleh kebutuhan pengguna 480x320. Kontrak radio v2, codec angular, dan profil bandwidth dipertahankan kecuali perubahan dinyatakan.

## Cara membaca dokumen

- **[C1]**: keterangan dan hasil yang dilaporkan pengguna dalam percakapan; bukan pengujian ulang penyusun.
- **[P1] sampai [P7]**: dokumen project, dengan daftar sumber di bagian 25.
- **[E1] sampai [E8]**: rujukan teknis primer yang diperiksa; bukan bukti versi paket di perangkat pengguna.
- **Usulan/default/target**: keputusan untuk aplikasi yang akan dibuat, bukan fitur yang diasumsikan sudah terpasang.
- Semua nilai contoh UI, konfigurasi, suhu, frekuensi, maupun status adalah ilustrasi, bukan pembacaan live.

## Daftar isi

1. [Baseline, batas bukti, dan keputusan baru](#s01)
2. [Tujuan produk dan ruang lingkup](#s02)
3. [Arsitektur service yang disarankan](#s03)
4. [Pilihan teknologi dan batas dependensi](#s04)
5. [Source adapter dan validitas data RDF](#s05)
6. [Desain layar 480x320](#s06)
7. [Arti indikator dan state model](#s07)
8. [Interval pembacaan, layar, dan radio](#s08)
9. [MQTT: koneksi, pengiriman, dan verifikasi penerimaan](#s09)
10. [Profil bandwidth dan command budget](#s10)
11. [Scheduler, antrean, dan adaptasi link](#s11)
12. [Konfigurasi yang perlu disediakan](#s12)
13. [Command, otorisasi, dan hasil operasi](#s13)
14. [Start, stop, restart RDF, dan reboot Raspberry](#s14)
15. [Sinkronisasi konfigurasi, waktu, dan DAQ](#s15)
16. [API lokal dan integrasi Ground](#s16)
17. [Penyimpanan state, jurnal operasi, dan logs](#s17)
18. [Struktur source code dan kontrak modul](#s18)
19. [Contoh konfigurasi deklaratif](#s19)
20. [Boot, autostart, dan failure recovery](#s20)
21. [Kinerja, layar, dan inventaris deployment](#s21)
22. [Matriks pengujian dan acceptance gate](#s22)
23. [Tahap implementasi](#s23)
24. [Keputusan final dan brief pengembang](#s24)
25. [Sumber, provenance, dan batas validasi](#s25)

---

<a id="s01"></a>
## 1. Baseline, batas bukti, dan keputusan baru

### 1.1 Yang sudah diketahui

Pengguna melaporkan bahwa transparent serial dua arah melalui picocom dan koneksi IP PPP sudah berhasil. Penyiapan service autostart telah dibahas dan diikuti; tidak ada bukti baru cold boot, pengujian reconnect jangka panjang, atau capture MQTT nyata dalam percakapan ini. [C1]

| Komponen | Nilai yang diketahui |
|---|---|
| Raspberry PPP | `10.90.0.2` |
| Ubuntu PPP | `10.90.0.1` |
| Serial Raspberry | Alias `/dev/t900` |
| Serial Ubuntu | Alias `/dev/t900-ground` |
| Baud baseline | 57600, transparent serial |
| Unit PPP Raspberry | `t900-ppp.service` |
| Unit PPP Ubuntu | `t900-ground-ppp.service` |
| Raspberry USB path | `platform-xhci-hcd.0-usb-0:2:1.0` |
| Ubuntu USB path | `pci-0000:00:14.0-usb-0:3:1.0` |
| Kelas USB T900 | `1a86:7523`, CH340/CH341 |
| Akun yang muncul pada terminal | `rdf` |
| Layar yang diminta sekarang | 480x320, 5 inci |

VID/PID dan physical USB path mengidentifikasi kelas perangkat di port tertentu, bukan nomor seri unik unit T900. Perangkat CH340 lain pada port yang sama tetap dapat cocok. Alias USB bukan autentikasi jaringan. [P1, bagian 1.2]

### 1.2 Yang belum boleh diasumsikan

Belum ada kepastian tentang model Raspberry, RAM, OS/versi deployment sekarang, jenis koneksi layar HDMI/DSI/SPI, dukungan touchscreen, compositor/display server, path SDR aktif, versi Paho/Mosquitto, atau semua kemampuan kontrol engine.

Path lama `/home/doasdr/doasdr` berasal dari arsip. Akun sekarang terlihat `rdf`, tetapi itu tidak membuktikan path baru tertentu. Source directory harus dimasukkan setelah verifikasi, bukan ditebak menjadi `/home/rdf/doasdr`. [C1; P1, bagian 1]

Bahan yang tersedia adalah dokumen arsitektur dan output terminal, bukan checkout source code lengkap aplikasi Ground maupun engine deployment sekarang. Nama modul/API baru pada dokumen ini adalah usulan implementasi.

### 1.3 Temuan historis yang tetap relevan

Arsip Data Out pernah menunjukkan `daq_ok=false`, dropped frames meningkat, dan DoA lama. Itu bukan klaim kondisi perangkat saat ini. Sebaliknya, keberhasilan PPP sekarang juga tidak membuktikan DAQ atau DoA telah sehat. [P3, bagian 5-8]

### 1.4 Perubahan terhadap dokumen induk

| Area | Dasar sebelumnya | Keputusan planning ini |
|---|---|---|
| Kanvas layar | 800x480 sebagai asumsi | 480x320 adalah target wajib |
| Layout | Banyak indikator sekaligus | Empat halaman; Overview tanpa scroll |
| Panel lokal | Read-only default | Tetap read-only; kontrol maintenance fase lanjutan |
| Bridge | Edge agent + API + dua MQTT client | Dipertahankan; dibangun modular dalam satu backend |
| Broker | Satu broker di Ground | Dipertahankan; tidak perlu broker Raspberry |
| Grafik Ground | Q16 360 titik tiap 4 detik | Dipertahankan sebagai BALANCED |
| Refresh lokal | Sekitar 2 Hz | 2 Hz untuk snapshot UI, probe lambat dipisah |
| Source code | Rancangan umum | Kontrak modul, konfigurasi, dan acceptance gate diperinci |

---

<a id="s02"></a>
## 2. Tujuan produk dan ruang lingkup

### 2.1 Pertanyaan yang harus bisa dijawab panel Raspberry

Operator harus bisa mengetahui dengan cepat:

1. Apakah RDF sedang berjalan, sengaja berhenti, atau gagal?
2. Apakah DAQ benar-benar sinkron dan data DoA masih baru?
3. Apakah T900 terpasang, PPP naik, dan MQTT tersambung?
4. Apakah backend Ground benar-benar menerima telemetry?
5. Frekuensi apa yang sedang dipakai, dan konfigurasi mana yang sudah diverifikasi?
6. Apakah ada command, peringatan suhu/daya, atau gangguan yang perlu diperhatikan?

### 2.2 Yang dikerjakan bridge

Bridge membaca output lokal, membentuk snapshot terstruktur, menjaga validity dan freshness, mengirim DoA/health/grafik melalui MQTT, menerima request yang diizinkan, dan menyediakan API untuk panel lokal. Ia juga memisahkan keadaan engine, host, jaringan, dan aplikasi Ground. [P1, bagian 3-10]

### 2.3 Yang tidak dimasukkan ke MVP

Tidak ada waterfall, frequency spectrum, audio, raw IQ, video mirroring, recording besar, database time-series berat, cloud dependency, broker kedua, atau pengubahan transport PPP yang sudah berhasil.

Panel Raspberry tidak perlu menggambar polar penuh pada halaman utama. Grafik 360 titik tetap dikirim ke Ground. Preview angular lokal bisa menjadi fitur tambahan pada halaman khusus, tetapi tidak diperlukan untuk indikator 480x320.

Tidak ada fungsi pengendalian penerbangan, motor, atau autopilot. Reboot Raspberry adalah operasi komputer RDF, bukan flight controller. [P1, bagian 2]

### 2.4 Prinsip produk

**RDF berhenti tidak berarti telemetry berhenti.** Health dan command receiver harus tetap hidup.

**Ground hilang tidak berarti layar lokal kosong.** Data lokal tetap tersedia dengan label pengiriman yang jujur.

**Browser mati tidak berarti bridge mati.** UI hanyalah client API lokal.

**Nilai terbaru di layar belum tentu pengukuran terbaru.** Setiap data membutuhkan timestamp, age, dan alasan validitas.

---

<a id="s03"></a>
## 3. Arsitektur service yang disarankan

### 3.1 Rekomendasi utama

Satu paket bernama **RDF Node**, dengan dua komponen runtime utama baru pada fase read-only:

- `rdf-edge.service`: backend bridge, collector, state store, scheduler, MQTT, dan local API.
- `rdf-kiosk.service`: nama usulan user service untuk browser layar 480x320, disesuaikan dengan graphical session.

Komponen ketiga baru ditambahkan saat kontrol aktif:

- `rdf-control-helper.service`: helper berhak terbatas untuk operasi yang membutuhkan privilege.

PPP dan engine SDR tetap komponen terpisah yang sudah ada atau harus diverifikasi nama unitnya. Dua MQTT client berjalan di dalam edge agent, bukan dua broker maupun kewajiban membuat dua daemon tambahan. [P1, bagian 3 dan 17]

### 3.2 Alur keseluruhan

```text
RASPBERRY

KrakenSDR -> Heimdall DAQ -> SDR-DoA engine
                               |
                      file output lokal
                               |
                         SourceAdapter
                               |
                      Validator + gates
                               |
                    Canonical State Store
                      /                \
                     /                  \
       Local API + static assets       Scheduler
                 |                    /         \
          Browser 480x320      MQTT control    MQTT bulk
                                      \         /
                                      PPP/T900
                                          |
UBUNTU                                    |
                                          v
                                 Mosquitto broker
                                          |
                                 Ground backend
                                  /          \
                            Dashboard      receipt
                                              |
                                  kembali ke Raspberry

COMMAND:
MQTT control -> CommandManager -> journal -> adapter/helper
                                           |
                                   evidence/read-back
                                           |
                                    result + state
```

### 3.3 Isolasi kegagalan

| Komponen gagal | Yang boleh ikut terpengaruh | Yang harus tetap bekerja bila host sehat |
|---|---|---|
| Browser lokal | Tampilan layar | Edge, MQTT, PPP, engine |
| Broker Ground | Delivery dan remote command | Collector, indikator lokal, engine |
| PPP/T900 | Koneksi Ground | Panel, collector, engine |
| Engine SDR | DoA/DAQ | Host health, command receiver, panel |
| Bulk MQTT | Kurva angular | Control MQTT, health, command |
| Helper | Operasi privileged | Telemetry read-only, panel, diagnosis |
| Edge | State/API/telemetry baru | PPP dan engine; browser harus menandai kehilangan sumber |

Tidak memasang dependency yang otomatis mematikan edge ketika PPP atau engine berhenti. `After=` adalah ordering, bukan bukti readiness. Kebijakan restart service dan health aplikasi harus dibedakan. [E3; E4]

### 3.4 Engine GUI lama bukan browser baru

Dokumen upstream menyatakan `WebInterface` membuat receiver dan menjalankan `SignalProcessor`. Karena itu proses Python GUI lama tidak boleh dimatikan hanya karena tampilan operator akan diganti. Panel baru tidak menggantikan engine, melainkan membaca hasilnya. Refactor engine headless adalah pekerjaan terpisah. [P4, bagian 2; P1, bagian 3.4]

### 3.5 Jangan membuat collector ganda

Panel tidak membaca `DOA_value.html` langsung. Ground tidak polling file tersebut secara rutin. Satu collector Raspberry membentuk snapshot yang dipakai bersama oleh UI lokal dan publisher. Polling banyak client tidak boleh menggandakan pembacaan hardware atau perintah sistem.

---

<a id="s04"></a>
## 4. Pilihan teknologi dan batas dependensi

### 4.1 Stack usulan

| Lapisan | Rekomendasi planning | Alasan desain |
|---|---|---|
| Backend | Python dalam virtual environment tersendiri | Cocok dengan adapter data dan ekosistem Ground yang terdokumentasi |
| MQTT | Paho MQTT Python dengan dukungan MQTT 5 | Client loop, reconnect, dan callbacks tersedia; policy aplikasi tetap dibuat sendiri |
| Local API | FastAPI + ASGI server, atau library setara yang sudah dipakai repo | API kecil dengan schema eksplisit; hindari menambah framework tanpa kebutuhan |
| UI lokal | HTML/CSS + TypeScript ringan; aset dibangun sebelum deployment | Empat halaman indikator, tidak membutuhkan frontend besar |
| Rendering | Browser kiosk sebagai user biasa | Mudah melihat UI yang sama saat development dan di perangkat |
| Journal | SQLite untuk command/config metadata; memory untuk telemetry terbaru | Pisahkan data durable dari update cepat |
| Service manager | systemd | Lifecycle backend dipisahkan dari graphical session |

Ini pilihan desain, bukan benchmark perbandingan framework. Paho mendukung MQTT 5; fitur loop dan reconnect didokumentasikan resmi. Raspberry Pi juga menyediakan pola browser kiosk, tetapi instruksi autostart harus mengikuti OS/desktop pengguna yang sebenarnya. [E1; E5]

### 4.2 Yang tidak dipilih untuk awal

Tidak memakai Electron, Docker sebagai prasyarat, Redis, message broker lokal tambahan, atau menjalankan Vite development server di produksi. Kebutuhan indikator tidak memerlukan infrastruktur tersebut.

React tetap boleh dipakai bila reuse komponen Ground nyata menghemat pekerjaan. Namun jangan menyalin seluruh Ground Dashboard ke panel 480x320. Dokumen Ground memang memakai React/TypeScript/Vite dengan Python backend, tetapi itu tidak mewajibkan panel Raspberry memakai struktur UI yang sama. [P6]

### 4.3 Alternatif apabila browser terlalu berat

Pertahankan kontrak Local API agar frontend dapat diganti dengan UI native, misalnya Qt, tanpa mengubah MQTT dan adapter. Pergantian ini dilakukan berdasarkan pengukuran CPU/RAM/dropped frame pada Raspberry asli, bukan asumsi bahwa salah satu UI pasti lebih ringan.

### 4.4 Pisahkan environment engine

Jangan install paket bridge ke environment Conda SDR secara otomatis. Buat environment aplikasi terpisah agar update API/MQTT tidak mengganti dependency DSP lama. Interpreter path, package lock, dan versi deployment dicatat. Versi final library dipilih setelah preflight, lalu dikunci dan diuji.

---

<a id="s05"></a>
## 5. Source adapter dan validitas data RDF

### 5.1 Sumber yang digunakan

| Sumber | Tujuan | Aturan |
|---|---|---|
| `DOA_value.html` | Metadata DoA dan 360 angular samples | Parse CSV, bukan HTML; pilih satu output VFO yang terverifikasi |
| `status.json` | DAQ, frame/sync, drop, status engine | Freshness tersendiri, jangan samakan dengan host heartbeat |
| `settings.json` | Konfigurasi lokal | Baca terkontrol; ekspor allowlist, bukan isi mentah |
| `doa.xml` | Pelengkap/cross-check | Tidak wajib; jangan gabungkan bila correlation belum terbukti |
| sysfs/proc/service observation | Host dan jaringan | Probe terbatas, tidak membuka perangkat SDR aktif |

Sumber project mencatat CSV 377 field, dengan 360 nilai angular, perbedaan arah CSV/XML, dan risiko snapshot lama atau parsial. [P3, bagian 3-8; P4, bagian 7]

### 5.2 Pipeline pembacaan

```text
baca dengan size cap
  -> parse snapshot utuh
  -> validasi jumlah field dan finite numbers
  -> cocokkan VFO/frekuensi
  -> periksa timestamp dan perubahan record
  -> ambil health DAQ yang relevan
  -> tentukan source authority dan convention
  -> buat snapshot immutable
  -> update state lokal
  -> evaluasi boleh publish atau diagnostic-only
```

Bila file sedang ditulis dan parsing gagal, simpan nilai lama sebagai last-known beserta age. Catat error, coba lagi pada tick berikutnya. Jangan menahan loop MQTT atau command sampai file berhasil dibaca.

### 5.3 Informasi yang tidak boleh dipalsukan

- Nilai `confidence` native tidak otomatis probabilitas. Gunakan label `PAPR` atau `Quality native` dengan satuan yang telah diverifikasi, bukan persen akurasi.
- Power RDF bukan RSSI T900. Jangan membuat signal-bar radio dari angka power Kraken.
- Grafik 360 titik tidak direkonstruksi dari satu DoA.
- Sudut relatif bukan bearing true-north tanpa heading, mounting offset, konvensi, dan korelasi waktu yang benar.
- GPS belum valid berarti unavailable, bukan koordinat contoh atau posisi 0,0 yang digambar sebagai fix.
- `q` dari adapter bukan nomor frame DAQ asli; simpan provenance.

Aturan interpretasi ini mempertahankan revisi eksplisit dokumen induk, bukan mengadopsi contoh confidence persentase dari rancangan lama. [P1, bagian 1.4, 4, dan 5]

### 5.4 NO_DETECTION memerlukan bukti

DAQ sehat dan file DoA tidak berubah belum cukup untuk memastikan tidak ada sinyal; writer dapat macet. Tampilkan `NO_DETECTION` bila engine memberi bukti squelch/no-detection atau adapter sudah tervalidasi membedakan kondisi itu. Tanpa bukti tersebut, tampilkan `NO_FRESH_DOA` atau `STALE`, dengan alasan yang tersedia.

### 5.5 Korelasi konfigurasi

Saat frekuensi/config berubah, record lama tidak boleh diberi revision baru. Kosongkan eligibility LIVE sampai output dapat diatribusikan pada runtime baru. Grafik lama boleh disimpan sebagai `PREVIOUS CONFIG`, tidak ditampilkan seolah hasil frekuensi yang baru.

### 5.6 Pengujian source sebelum live

Gunakan fixture valid, stale, malformed, file parsial, perbedaan CSV/XML, missing timestamp, nilai NaN/Infinity, VFO ambigu, dan clock jump. Fixture sintetis harus diberi mode DEMO, tidak masuk namespace live.

---

<a id="s06"></a>
## 6. Desain layar 480x320

### 6.1 Struktur navigasi

Empat tab tetap di bagian bawah:

**Utama | Link | Sistem | Config**

Overview tidak menggunakan scroll. Halaman detail memakai subhalaman/pagination ketika data melebihi ruang. Tidak memakai carousel otomatis; operator tidak perlu mengejar informasi yang berpindah sendiri.

Interaksi awal mendukung mouse/keyboard. Target touch besar tetap disediakan, tetapi adanya touchscreen belum dianggap fakta. Driver dan kalibrasi touch tidak dipasang berdasarkan resolusi saja.

### 6.2 Alokasi pixel Overview

Koordinat berikut adalah target logical viewport 480x320. Physical display mode, browser zoom, DPR, dan desktop scaling harus diperiksa pada perangkat agar target ini benar-benar tercapai.

| Area | y awal | Tinggi | Isi |
|---|---:|---:|---|
| Header | 0 | 28 | RDF NODE dan status processing |
| Link strip | 28 | 44 | PPP, MQTT, Ground receipt |
| Data utama | 72 | 88 | DoA + age, frekuensi aktif |
| DAQ/config strip | 160 | 44 | DAQ health dan config sync |
| Command strip | 204 | 28 | Hasil/progress command terakhir |
| Alert/host strip | 232 | 40 | Alarm prioritas; suhu ringkas saat normal |
| Navigasi | 272 | 48 | Empat tab, masing-masing lebar 120 |
| **Total** | | **320** | Tidak melebihi viewport |

Padding horizontal konten 8 pixel, lebar efektif 464. Untuk tiga kartu dengan dua gap 8 pixel, lebar tiap kartu sekitar 149 pixel. Untuk dua kolom, masing-masing 228 pixel dengan gap 8 pixel.

Ini spesifikasi layout, bukan hasil render browser atau uji keterbacaan hardware. Perlu memastikan tidak ada clipping pada teks panjang dan bahasa yang dipakai.

### 6.3 Wireframe Overview

```text
+------------------------------------------------+
| RDF NODE                       RDF BERJALAN    |
+---------------+---------------+----------------+
| PPP: UP       | MQTT: OK      | GRD RX: 3 dtk  |
+-----------------------+------------------------+
| ARAH RELATIF          | FREKUENSI AKTIF        |
|       137.4 deg       |      433.920 MHz       |
| Umur 0.4 dtk          | VFO 0 / terverifikasi  |
+-----------------------+------------------------+
| DAQ: SINKRON          | CFG: SAMA r8           |
+------------------------------------------------+
| Perintah: Ubah frekuensi / TERVERIFIKASI         |
+------------------------------------------------+
| Sistem normal                 Suhu 61 C        |
+-----------+-----------+-----------+------------+
|  Utama    |   Link    |  Sistem   |   Config   |
+-----------+-----------+-----------+------------+
```

Wireframe ASCII menjelaskan isi, bukan ukuran proporsional karakter. Nilainya sintetis. Label `GRD RX` berarti backend Ground mengonfirmasi penerimaan, bukan operator manusia sedang melihat browser.

### 6.4 Tipografi dan kontrol

Target awal: angka DoA 34-38 px; frekuensi 22-24 px; status penting 16-18 px; metadata 13-14 px. Jangan mengecilkan semua isi untuk memuat satu halaman. Tombol utama memakai tinggi 48 px; touch hit area harus diuji pada panel asli.

Pakai theme gelap atau terang yang dapat dipilih lokal. Warna hanya pelengkap: setiap keadaan memiliki teks/icon, misalnya `MQTT PUTUS`, bukan titik merah tanpa keterangan. Hindari animasi terus-menerus, blinking keras, map, logo besar, dan grafik dekoratif.

### 6.5 Perilaku informasi kritis

Jika DoA invalid/stale, angka besar diganti `--` atau dipindah sebagai nilai terakhir dengan label `DATA LAMA`. Jangan mempertahankan angka terang yang terlihat LIVE.

Header menunjukkan processing, bukan online global. Alert strip memilih alarm paling penting: sumber UI hilang, host/power problem, DAQ failure, command unknown, lalu link degraded. Daftar alarm lengkap ada di Sistem; alarm prioritas tidak disembunyikan rotasi otomatis.

### 6.6 Halaman Link

Halaman pertama, maksimal lima kelompok:

```text
T900 USB     TERPASANG
PPP          UP / peer sesuai
MQTT         CONTROL OK / BULK OK
GROUND RX    3 dtk / health seq 86
TRAFFIC      TX 6.2 / RX 1.7 kbit/s
```

Subhalaman Data: DoA last sent, angular last sent/received, queue/drop, profile aktif, alasan bulk pause, error koneksi terakhir. Nilai traffic diberi label layer `PPP/IP counters` atau `MQTT bytes`; jangan dinamai throughput RF murni.

`T900 USB TERPASANG` hanya berarti device cocok dan tersedia. Itu tidak membuktikan radio peer paired atau RF sehat. `PPP UP` tetap dibedakan dari Ground receipt.

### 6.7 Halaman Sistem

Gunakan dua subhalaman:

- **RDF/DAQ**: process state, frame progress, frame/sample-delay/IQ sync, drop delta, ADC overdrive, data freshness.
- **Host**: CPU, RAM, suhu, storage, throttling/undervoltage bila tersedia, uptime dan clock state.

Jangan menjalankan `rtl_test`, reset USB, atau membuka receiver lagi untuk mengisi halaman ini ketika DAQ sedang aktif. Arsip diagnostik memang membedakan enumeration USB dari receiver yang usable. [P7, bagian 1, 4, dan 11]

### 6.8 Halaman Config

Tampilan awal read-only: node ID, profile, interval ringkas, current effective frequency/VFO, revision/proof, dan status otorisasi.

Pengaturan tampilan yang ringan dapat diubah lokal: theme, tingkat detail, display timeout, dan orientasi UI yang didukung. Brightness hanya tersedia jika driver panel menyediakan mekanisme yang terverifikasi.

Pengaturan koneksi, source path, credential, dan RF/DSP tidak ditaruh sebagai form panjang pada layar kecil. Setup awal dilakukan melalui file deployment atau management session yang terkontrol. Halaman ini menampilkan hasil validasi dan alasan fitur disabled.

### 6.9 Kontrol lokal

MVP read-only. Pada fase kontrol, halaman Config dapat membuka panel maintenance setelah authorization. Start/Stop memakai CommandManager yang sama dengan request Ground. Reboot tidak pernah berada di Overview. Konfirmasi visual atau tombol tahan bukan pengganti pemeriksaan backend.

### 6.10 Saat API atau browser macet

Browser menghitung waktu sejak snapshot baru secara lokal. Request HTTP yang selalu sukses tetapi `snapshot_seq` tidak maju harus tetap dianggap sumber macet. Batas awal: warning >2 detik, status hilang >5 detik. Jangan hanya memeriksa HTTP 200.

Jika proses browser benar-benar freeze, logika browser tidak dapat memperbarui pesan error. Opsional watchdog kiosk membaca heartbeat renderer melalui API lokal dan merestart browser saja. Heartbeat ini menunjukkan renderer merespons, bukan bukti panel fisik atau backlight menyala. Uji layar terlepas adalah pengujian terpisah.

---

<a id="s07"></a>
## 7. Arti indikator dan state model

### 7.1 Dimensi state

| Dimensi | Nilai utama | Sumber bukti |
|---|---|---|
| Processing | STOPPED / STARTING / RUNNING / STOPPING / ERROR | Intent dan proses engine |
| DAQ | HEALTHY / DEGRADED / UNKNOWN | Status fresh, sync flags, frame progress |
| Detection | VALID / NO_DETECTION / NO_FRESH_DOA / UNVERIFIED | Output dan gate |
| USB | PRESENT / MISSING / UNKNOWN | Alias dan identitas device |
| PPP | UP / NEGOTIATING / DOWN | Interface/address peer dan event transport |
| MQTT control/bulk | CONNECTING / CONNECTED / DISCONNECTED / ERROR | CONNACK dan state client |
| Ground reception | RECEIVING / LATE / LOST / UNCONFIRMED | Receipt cocok dengan session/sequence |
| Config | SYNCED / PENDING / CONFLICT / UNVERIFIED | Revision, digest, runtime proof, receipt |
| Clock | SYNCED / HOLDOVER / UNTRUSTED | Provider waktu dan uncertainty |
| UI source | LIVE / STALE / UNAVAILABLE | Kemajuan snapshot API |

Jangan menyatukan semua menjadi `ONLINE`. Model multidimensi berasal dari dokumen induk, dengan penambahan label `NO_FRESH_DOA` dan `UNCONFIRMED` untuk menghindari diagnosis yang belum terbukti. [P1, bagian 10]

### 7.2 Threshold awal, bukan hasil uji

| Item | Normal | Warning | Stale/lost |
|---|---|---|---|
| Snapshot API lokal 2 Hz | <=2 detik | >2 detik | >5 detik |
| DoA radio 1 Hz | source age <=2.5 detik dan gate valid | >2.5 detik | >5 detik |
| Health di Ground 1 Hz | <=3 detik | >3 detik | >8 detik; unreachable >15 detik |
| Angular 0.25 Hz | <=6 detik | >6 detik | >10 detik |
| Receipt Ground 0.2 Hz | <=10 detik | >10 detik | >15 detik |

Threshold dievaluasi sesuai profile. Kurva sengaja dipause oleh command diberi status `PAUSED FOR CONTROL` sekaligus age, bukan langsung dianggap radio failure. Source status sendiri memiliki umur yang berbeda dari heartbeat edge.

### 7.3 Indikator Ground harus menilai progress

Receipt baru yang mengulang health sequence lama tidak membuktikan telemetry terbaru diterima. Edge membandingkan `hq` terhadap health yang pernah dikirim dan memperhatikan progress. Saat RDF sengaja STOPPED, `dq` boleh tidak bertambah; `hq` harus tetap bertambah.

Saat belum ada implementasi receipt di Ground, tampilkan `GRD: BELUM DIKONFIRMASI`, bukan hijau dan bukan klaim RF rusak. Bila session baru belum dipetakan, tunggu handshake sebelum mengakui receipt.

### 7.4 Jangan menebak penyebab kehilangan

Tidak ada health berarti node tidak terjangkau dari perspektif Ground. Itu belum membuktikan Raspberry mati. Penyebab bisa broker, PPP, RF, service, atau consumer. Pesan spesifik hanya ditampilkan bila observasi mendukung.

---

<a id="s08"></a>
## 8. Interval pembacaan, layar, dan radio

### 8.1 Tiga interval berbeda

**Collect interval**: seberapa sering Raspberry membaca sumber lokal.

**Publish interval**: seberapa sering informasi dikirim melewati T900.

**Display interval**: seberapa sering halaman Raspberry mengambil snapshot dan memperbarui indikator.

Ketiganya tidak harus sama. Mengambil UI lokal 2 Hz tidak berarti semua field radio dikirim 2 Hz.

### 8.2 Default lokal usulan

| Kegiatan | Interval | Catatan |
|---|---:|---|
| Baca kandidat DoA lokal | 250 ms / 4 Hz | Hanya snapshot baru yang mendapat q baru; tidak memaksa DSP 4 Hz |
| Baca status DAQ | 500 ms / 2 Hz | Validasi age, frame, dan sync |
| Bentuk snapshot API | 500 ms / 2 Hz | Agregasi state yang sudah tersedia |
| Browser mengambil snapshot | 500 ms / 2 Hz | Satu request aktif; timeout 1 detik; tanpa request bertumpuk |
| CPU/RAM/suhu | 1 detik | Ringkas; tidak subprocess berat per request UI |
| Service/process state | 2 detik | D-Bus atau query terkontrol dengan timeout |
| USB inventory | Event-driven + fallback 5 detik | Jangan membuka receiver |
| Mtime/config check | 1 detik | Baca/parsing ulang hanya bila perlu |
| Disk space | 30 detik | Event critical bila perubahan penting |
| Journal maintenance | 60 detik | Retensi; tidak menulis semua telemetry |

MVP dapat memakai polling HTTP loopback sederhana; SSE menjadi opsi setelah diperlukan. Poll lokal yang terputus tidak menyentuh MQTT, PPP, atau file engine.

### 8.3 Default radio BALANCED

| Data | Interval | QoS | Retain |
|---|---:|---:|---|
| DoA ringkas | 1 detik | 0 | Tidak |
| Health ringkas | 1 detik | 0 | Tidak |
| Detail health | 10 detik | 0 | Tidak |
| Angular Q16 360 titik | 4 detik per frame | 0 | Tidak |
| Ground receipt | 5 detik | 0 | Tidak |
| State | Event + refresh 60 detik | 1 | Ya, last-known |
| Config reported | Startup/change/request/reconnect | 1 | Ya, last-known |
| Command/result | On-demand | 1 | Tidak |
| Nav | OFF awal | 0 bila aktif | Tidak |

DoA/curve hanya dipublikasikan saat ada snapshot baru yang lolos gate. Health tetap dibuat baru oleh edge walaupun RDF berhenti. Angka ini mempertahankan profile induk, bukan kembali ke usulan 2 Hz + grafik 0.5 Hz + nav 1 Hz sekaligus. [P1, bagian 7-9]

Alarm dan state penting boleh segera dijadwalkan di luar tick rutin, dengan coalescing/rate limit. Event storm tidak boleh menghilangkan bandwidth health.

<a id="s09"></a>
## 9. MQTT: koneksi, pengiriman, dan verifikasi penerimaan

### 9.1 Topologi

Satu broker Mosquitto di Ubuntu. Edge Raspberry memakai dua client: control dan bulk. Browser lokal serta browser Ground tidak menyimpan credential broker dan tidak subscribe langsung ke MQTT. [P1, bagian 3 dan 16]

Broker endpoint rencana: `10.90.0.1:8883` dengan TLS. Ini default desain, bukan klaim listener sudah dipasang. Koneksi IP PPP yang berhasil tidak membuat broker otomatis ada.

### 9.2 Parameter awal

| Parameter | Default usulan |
|---|---|
| Protocol | MQTT 5 setelah capability versi terverifikasi |
| Control client ID | `uav-01-control` |
| Bulk client ID | `uav-01-bulk` |
| Clean Start | True untuk koneksi baru sesuai policy |
| Session Expiry | 0; tidak bergantung pada antrean broker offline |
| Keep Alive | 15 detik |
| Reconnect | Backoff 1, 2, 4, 8, 16, maksimum 30 detik; jitter bila wrapper mendukung |
| In-flight QoS >0 | Batas awal 4; validasi pada library terpasang |
| Queue QoS >0 | Batas awal 16 pesan dan batas byte aplikasi terpisah |
| Telemetry slots | Satu nilai terbaru per kelas, bukan history |
| Angular | Satu frame aktif + satu calon terbaru |
| Pending mutation | Maksimum satu per node |
| Credentials | Referensi file rahasia; tidak masuk frontend/config export |

Paho mendokumentasikan network loop, exponential reconnect, dan batas in-flight untuk QoS di atas nol. Batas tersebut bukan pengganti scheduler latest-value atau pembatasan socket untuk trafik QoS 0. [E1]

### 9.3 Definisi MQTT READY

Urutan readiness aplikasi:

```text
PPP peer/routing tersedia
 -> TCP/TLS berhasil
 -> CONNACK sukses
 -> subscribe topic yang diperlukan
 -> SUBACK sukses untuk semua subscription wajib
 -> kirim identity/state/config ringkas dengan pacing
 -> terima Ground receipt yang cocok
```

UI menampilkan `MQTT CONNECTED` setelah koneksi MQTT sukses, tetapi `COMMAND READY` baru boleh aktif setelah subscription, capability, auth, clock, dan policy mutasi siap. Ground receipt adalah indikator terpisah, bukan syarat agar panel lokal bekerja.

Callback MQTT harus cepat. Parsing besar, file I/O, fsync journal, dan operasi service berjalan di worker berdeadline; network loop tidak boleh menunggu restart engine selesai.

### 9.4 Topic v2

Prefix usulan: `sdr/v2/uav-01`. Ini mempertahankan perubahan semantik dari dokumen induk; jangan memasukkan payload v2 ke decoder v1 diam-diam. [P1, bagian 5]

| Suffix | Pengirim | Kegunaan |
|---|---|---|
| `telemetry/doa` | Raspberry | DoA compact |
| `telemetry/health` | Raspberry | Health utama |
| `telemetry/health/detail` | Raspberry | Detail lambat |
| `telemetry/angular` | Raspberry bulk | Chunk array 360 |
| `state` | Raspberry | State terakhir beserta identitas |
| `availability` | Raspberry/broker | Online/planned offline/LWT |
| `config/reported` | Raspberry | Konfigurasi aman dan proof |
| `capabilities` | Raspberry | Codec/operation/field yang didukung |
| `ground/receipt` | Ground backend | Penerimaan aplikasi |
| `cmd/config/get` | Ground | Refresh snapshot konfigurasi |
| `cmd/config/patch` | Ground | Ubah subset settings |
| `cmd/processing/set` | Ground | Desired RUNNING atau STOPPED |
| `cmd/service/restart` | Ground | Restart stack yang diizinkan |
| `cmd/system/reboot/prepare` | Ground maintenance | Persiapan reboot |
| `cmd/system/reboot/execute` | Ground maintenance | Eksekusi challenge valid |
| `cmd/operation/get` | Ground | Rekonsiliasi hasil command |
| `cmd/stream/set` | Ground | Pilih profil yang telah diuji |
| `ack/config` | Raspberry | Status operasi settings |
| `ack/operation` | Raspberry | Status lifecycle/operasi lain |

Tidak perlu subscribe `#` lintas semua node. Bulk client tidak memperoleh permission reboot. Read-only dashboard tidak memperoleh permission publish command.

### 9.5 QoS, expiry, dan retain

Policy aplikasi memakai QoS 0 untuk data latest-value, QoS 1 untuk command/result, dan tidak memakai retained command. Retained state hanya last-known. MQTT 5 memberi fasilitas message/session expiry; expiry pada broker tetap dilengkapi deadline dan validasi session pada aplikasi. QoS 1 dapat mengirim duplikat, sehingga tindakan tidak boleh diulang hanya karena pesan kembali tiba. [E2]

Default TTL usulan: DoA 3 detik, health 5 detik, angular chunks 6 detik sejak penjadwalan. Nilai ini tidak mengganti gate source age. Jangan menulis timestamp baru agar file lama lolos. Native source timestamp, receive time, dan sender sample age dipisah.

Command memiliki batas waktu penerimaan tersendiri; timeout pekerjaan sesudah diterima tidak disamakan dengan Message Expiry MQTT.

### 9.6 Receipt Ground

Kontrak dasar memakai field dokumen induk:

```json
{"v":2,"sid":"7a8b9c0d","dq":1245,"hq":86,"aq":1238,"rev":7}
```

Receipt merangkum snapshot yang benar-benar sudah didekode dan diterima backend. `aq` hanya maju setelah seluruh chunk angular lengkap dan valid. Session harus cocok. `rev` mencerminkan effective revision yang dikenal backend, bukan revision draft operator.

Tidak ada PUBACK untuk QoS 0. Bahkan publish QoS 1 yang di-ACK broker tidak membuktikan frontend merender, operator membaca, atau hardware menerapkan command. Karena itu receipt dan result aplikasi tetap diperlukan. [E2]

Ketika Ground restart, receipt lama tidak boleh menghidupkan indikator hijau session baru. Jika perlu nonce freshness khusus untuk recovery, kontraknya harus diberi versi dan budget baru; jangan menambah field diam-diam sambil mempertahankan angka ukuran lama.

### 9.7 LWT dan deteksi kehilangan

Availability LWT dipakai untuk status last-known, bukan satu-satunya detektor loss. Ground juga memakai timer health lokal. Reboot/planned shutdown ditandai sebelum disconnect bila komunikasi memungkinkan. Tidak menerima LWT bukan bukti node masih sehat, khususnya ketika broker sendiri mati.

Keepalive 15 detik bukan janji deteksi gangguan tepat 15 detik. Timer freshness UI sengaja terpisah dan lebih cepat.

### 9.8 Reconnect tanpa backlog

Ketika link terputus: collector dan layar tetap berjalan, telemetry tidak ditimbun, angular aktif dibatalkan bila sudah tidak relevan. Sesudah reconnect: subscribe ulang, sinkronkan session dan state, kirim health, baru DoA dan angular. Jurnal hasil operasi dapat di-query; tidak mengulang seluruh history.

Pastikan broker tidak mengantrekan QoS 0 offline; konfigurasi queue Mosquitto harus diperiksa pada versi yang dipakai. Session persisten, queue client, dan queue broker adalah lapisan berbeda. [E6]

### 9.9 TLS dan provisioning

Gunakan autentikasi dan ACL per node/per peran. Certificate broker harus cocok dengan identitas endpoint: bila koneksi menggunakan IP `10.90.0.1`, verifikasi certificate harus mendukung identitas itu; jangan menonaktifkan verifikasi untuk menghilangkan error.

Password/token/certificate private key tidak ditampilkan pada layar, endpoint snapshot, MQTT telemetry, ataupun log. Autentikasi broker tidak otomatis memberi otorisasi semua jenis command. Metadata `role` pada payload bukan bukti hak akses.

### 9.10 Broker boot sebelum PPP

Deployment broker harus diuji ketika `10.90.0.1` belum ada saat Ubuntu boot. Listener yang diikat hanya ke alamat PPP dapat membutuhkan retry/order yang benar. Alternatif listener lebih luas membutuhkan firewall yang membatasi interface/sumber. Pilihan final dibuat di deployment Ground, tanpa membuka broker anonim ke seluruh LAN. [E6; usulan deployment]

---

<a id="s10"></a>
## 10. Profil bandwidth dan command budget

### 10.1 Definisi angka

Dokumen benchmark project mengukur UDP application payload, arah utama Raspberry ke Ground, dengan datagram 256 byte dan tes 30 detik per rate. Angka 18 kbit/s clean tidak menjamin MQTT/TLS dua arah, dan bukan ukuran seluruh bit UART. [P2, bagian 3-7]

Tabel berikut mempertahankan model dokumen induk: payload contoh, topic v2, MQTT 5 properties, TCP/IPv4, model TLS steady-state, ACK dua arah, allowance PPP control, lalu 15% tambahan ilustratif. Kalkulator lampiran B dokumen induk dijalankan ulang ketika menyusun planning ini dan menghasilkan angka yang sama. Ini pemeriksaan matematika lokal, bukan pengukuran radio. [P1, bagian 9 dan lampiran B]

### 10.2 Profil yang ditawarkan UI

| Profil | DoA | Health | Angular | Nav | Total model + allowance |
|---|---:|---:|---|---|---:|
| CONTROL | 1 Hz | 1 Hz | OFF | OFF | 6.47 kbit/s |
| BALANCED, default | 1 Hz | 1 Hz | Q16 360, setiap 4 detik | OFF | 9.14 kbit/s |
| GRAPH U8, opt-in | 1 Hz | 1 Hz | U8 360, setiap 2 detik | OFF | 9.25 kbit/s |
| FAST Q16, butuh uji | 2 Hz | 1 Hz | Q16 360, setiap 4 detik | OFF | 11.80 kbit/s |
| NAV, butuh alokasi | 1 Hz | 1 Hz | Q16 360, setiap 8 detik | 1 Hz | 10.36 kbit/s |
| Semua cepat, bukan default | 2 Hz | 1 Hz | Q16 360, setiap 2 detik | 1 Hz | 17.03 kbit/s |

Semua profil mencakup detail health 0.1 Hz, receipt 0.2 Hz, state refresh 1/60 Hz, dan asumsi overhead yang sama. CONTROL bukan tanpa health atau tanpa DoA; yang terutama dihentikan adalah bulk angular.

Biaya layar lokal tidak masuk radio, tetapi tetap masuk pemakaian CPU/RAM host.

### 10.3 Biaya command

Model transaksi frekuensi induk terdiri dari command, accepted ACK, applied ACK, reported config, dan state. Total model dua arah 2450 byte; dengan allowance 15% sekitar 2818 byte. Satu transaksi per menit menambah rata-rata sekitar 0.376 kbit/s, sehingga BALANCED menjadi sekitar 9.52 kbit/s. [P1, bagian 9.5]

Command adalah burst, bukan lalu lintas yang benar-benar diratakan selama satu menit. Saat ada mutasi, pause angular baru, pertahankan health, lalu resume setelah operasi dan health stabil. Operator tidak boleh membuat slider frekuensi mengirim command puluhan kali per detik. Gunakan edit draft dan tombol Apply.

Satu transaksi tiap 10 detik memberi tambahan sekitar 2.254 kbit/s pada model. Reboot/reconnect juga membawa TLS handshake dan bootstrap yang tidak dicakup steady-state. Jangan menjanjikan semua profile selalu aman berdasarkan angka rata-rata.

### 10.4 Detail full angular yang dipertahankan

Q16: 48 byte header + 360 x 2 byte = 768 byte frame. Default dua potongan 384 byte, masing-masing ditambah 12 byte envelope; total payload frame pada MQTT 792 byte. Presisi amplitudo kuantisasi awal 0.01 satuan native, bukan lossless CSV.

U8: 48 + 360 = 408 byte frame, satu envelope 12 byte sehingga payload 420 byte. Tetap 360 titik; amplitudo memakai scale/offset. Kuantisasi tidak menambah akurasi RDF. [P1, bagian 6]

Ground melakukan assembly berdasarkan node/session/sequence, memvalidasi semua potongan, lalu mengirim array numerik ke frontend. Tidak menggabungkan chunk berbeda frame atau merender sebagian sebagai kurva lengkap.

### 10.5 Yang mengharuskan hitung ulang

SNR tambahan, timestamp tambahan, identitas lebih panjang, beberapa VFO, nav aktif, receipt baru, message signatures, dan metadata tambahan mengubah ukuran. Full settings atau native CSV manual mengonsumsi bandwidth juga. Jangan mengirim field UI lokal yang banyak melalui radio hanya karena Local API sudah memilikinya.

15% allowance bukan batas atas escaping atau retransmission. PPP escaping dapat berubah menurut byte payload dan negosiasi. Dua koneksi client tetap berbagi kapasitas transport yang sama. [E7; P1, bagian 9]

### 10.6 Mode lapangan yang dipilih

Mulai dengan CONTROL saat bootstrap dan pengujian awal. Setelah source, backend receipt, dan budget lolos, jadikan BALANCED profil operasional. GRAPH U8 menjadi pilihan yang disetujui setelah grafik dibandingkan dengan sumber. FAST tidak otomatis diaktifkan hanya karena ping kecil.

---

<a id="s11"></a>
## 11. Scheduler, antrean, dan adaptasi link

### 11.1 Prioritas aplikasi

| Prioritas | Isi | Kebijakan |
|---|---|---|
| P0 | ACK/result dan alarm penting | Rate limit untuk mencegah starvation |
| P1 | Health dan state transisi | Deadline, tidak dikalahkan grafik |
| P2 | DoA terbaru dan nav valid | Latest-value; boleh menurunkan rate |
| P3 | Angular 360 | Pause/drop paling awal |
| P4 | Detail/config besar/diagnostic | On-demand, bounded, bisa dibatalkan |

QoS 1 tidak otomatis membuat topic prioritas. Prioritas ini harus benar-benar diimplementasikan sebelum data diberikan ke client library.

### 11.2 Bounded queue dari producer sampai socket

Satu slot DoA, satu health terbaru, dan satu frame angular pending. Jangan memanggil `publish()` untuk semua frame lalu menganggap queue sudah terkendali. Batas antrean aplikasi, queue Paho, socket, kernel, dan buffer radio tidak identik.

Completion QoS 0 hanya menyatakan kemajuan lokal library, bukan penerimaan Ground. Bila socket bulk tidak maju, pause atau reset bulk berdasarkan policy tanpa merestart control. TCP tetap stream berurutan dengan retransmission, sehingga data yang sudah masuk stream tidak dapat disisipkan ulang prioritasnya sesuka aplikasi. [E1; E8]

### 11.3 Pacing

Gunakan scheduler cost-aware dan token bucket, dengan kapasitas burst bulk maksimum satu chunk. Sisakan anggaran control; jangan biarkan bulk meminjam seluruh cadangan. Jangan memakai `sleep` panjang dalam callback MQTT.

Dua chunk Q16 tidak langsung dilempar sekaligus. Setelah chunk pertama, beri kesempatan command/health, cek backlog, kemudian kirim chunk kedua. Assembly timeout awal 3 detik, disesuaikan dengan pacing yang benar-benar diuji. Jika sudah basi atau operasi config dimulai, batalkan frame yang belum selesai dan tunggu frame baru.

### 11.4 Adaptasi

Pemicu usulan untuk turun ke CONTROL: receipt lebih lama dari 10 detik, backlog control diperkirakan >500 ms, queue meningkat terus, deadline health terlewat, command aktif, atau config sedang berubah.

Resume angular hanya setelah operasi selesai, konfigurasi jelas, dan kondisi stabil setidaknya 20 detik. Naik satu tingkat, bukan langsung semua stream cepat. Jika link sangat buruk bahkan health 1 Hz tidak dapat dijamin; tampilkan kehilangan komunikasi, bukan menambah antrean tanpa batas.

Saat aplikasi Ground belum mendukung receipt, mode test read-only tetap dapat mengirim telemetry terbatas, tetapi adaptive delivery assurance tidak dinyatakan aktif. Promosi menjadi mode operasional membutuhkan receipt atau mekanisme penerimaan setara yang diuji.

### 11.5 Pisahkan statistik drop

Catat paling tidak `source_parse_errors`, `source_stale_rejections`, `telemetry_superseded`, `angular_aborted`, `sdk_queue_rejections`, dan `ground_sequence_gaps`. Jangan menyebut semua sebagai packet loss RF. Frame yang sengaja diganti latest-value adalah tindakan scheduler.

---

<a id="s12"></a>
## 12. Konfigurasi yang perlu disediakan

### 12.1 Pisahkan lima kelompok

| Kelompok | Contoh | Letak pengaturan awal | Boleh diubah saat normal? |
|---|---|---|---|
| Display | Theme, detail level, timeout | Panel lokal/API lokal | Ya, tanpa memengaruhi radio |
| Telemetry profile | BALANCED/CONTROL, encoding | Config aplikasi; Ground dengan izin | Hanya profil lolos budget |
| SDR operational | Frekuensi, gain, VFO, squelch | CommandManager | Ya setelah adapter/evidence diuji |
| Deployment | Source path, unit engine, broker, TLS | File konfigurasi/provisioning | Maintenance, bukan form biasa |
| Security/policy | ACL, privilege, reboot permission | Protected backend configuration | Tidak lewat telemetry patch rutin |

### 12.2 Halaman config yang berguna di layar kecil

Tampilkan nilai ringkas dan status validasinya: `SOURCE OK`, `BROKER CONFIGURED`, `PROFILE BALANCED`, `RDF READ-ONLY`, `CFG r8 VERIFIED`. Beri tombol Details untuk penjelasan error. Jangan memaksa operator mengetik path panjang atau password di 480x320.

Pilihan interval sebaiknya melalui profile, bukan banyak slider independen. Jika engineering override disediakan, perubahan harus menghitung budget, memperlihatkan konsekuensi, dan tidak dapat menghapus batas keselamatan.

### 12.3 Tiga revision berbeda

- `agent_config_revision`: konfigurasi bridge seperti MQTT/profile/source.
- `sdr_config_revision`: konfigurasi efektif engine, dipakai pada payload `rev`.
- `display_preferences_revision`: theme/panel lokal.

Mengubah theme tidak menaikkan revision RF. Mengubah broker tidak berarti frekuensi berubah. Tetap ada pemetaan jelas antara nilai wire `rev` dan sumbernya.

### 12.4 Validation dan reload

Parse schema dengan strict types dan range. Unknown field ditolak atau diberi peringatan jelas menurut policy; jangan diam-diam diabaikan pada config keamanan. Source path harus directory yang dapat dibaca, dan unit engine harus identifier allowlisted.

Reload display dapat langsung berlaku. Reload profile memakai batas scheduling dan queue drain. Reload broker/TLS memutus koneksi secara terkontrol, menampilkan state, lalu reconnect; tidak dilakukan diam-diam saat mutasi engine berlangsung. Invalid file tidak mengganti config terakhir yang valid.

### 12.5 Mode initial setup

Jika `source.share_dir` belum diisi, API/panel tetap bisa hidup dengan `SETUP REQUIRED`, bukan mencoba direktori tebakan. Publisher DoA nonaktif. Jika broker belum diprovisioning, local mode tetap tersedia. Startup read-only tidak mengambil alih desired engine state atau mengubah service yang sudah berjalan.

---

<a id="s13"></a>
## 13. Command, otorisasi, dan hasil operasi

### 13.1 Fitur bertahap

| Operasi | Tahap | Bukti minimum |
|---|---|---|
| Get config/state | Read-only | Snapshot valid dan timestamp |
| Stream profile select | Sesudah budget diuji | Profile/report cocok |
| Config patch | Sesudah adapter/evidence diuji | Runtime setting yang relevan terverifikasi |
| Start/stop processing | Sesudah lifecycle+watchdog diselaraskan | Desired/observed cocok |
| Restart RDF stack | Maintenance | Generasi stack berubah dan readiness diperiksa |
| Reboot Raspberry | Maintenance terpisah | Prepare/execute, boot baru, hasil direkonsiliasi |

Fitur disabled bukan kegagalan UI. Panel memperlihatkan alasan: `adapter belum tervalidasi`, `clock tidak dipercaya`, `izin tidak cukup`, `maintenance belum aktif`, atau `operasi lain berlangsung`.

### 13.2 Command envelope

Contoh planning berikut mempertahankan schema induk. Nilai hanya ilustrasi.

```json
{
  "v": 2,
  "id": "g01-00001234",
  "sid": "7a8b9c0d",
  "boot": "10aa2345-6789-4abc-9def-1234567890ab",
  "issued_ms": 1790668800123,
  "expires_ms": 1790668815123,
  "base_rev": 7,
  "op": "config.patch",
  "changes": {
    "center_frequency_hz": 433920000,
    "vfo0_frequency_hz": 433920000
  }
}
```

Node/session/revision/expiry diperiksa; identitas operator datang dari channel terautentikasi dan controller yang dipercaya. Jangan mempercayai payload `is_admin=true`.

### 13.3 Lifecycle operasi

```text
REQUESTED di Ground
 -> RECEIVED di node
 -> VALIDATED + JOURNALED
 -> ACCEPTED
 -> APPLYING
 -> VERIFYING
 -> APPLIED / FAILED / PERSISTED_UNVERIFIED
```

Penolakan sebelum eksekusi: `UNAUTHORIZED`, `EXPIRED`, `CONFLICT`, `BUSY`, `UNSUPPORTED`, `INVALID_VALUE`, `CLOCK_UNTRUSTED`.

Ground timeout setelah accepted menghasilkan `OUTCOME_UNKNOWN` sampai rekonsiliasi. Tidak otomatis berarti FAILED dan tidak memicu command baru dengan ID berbeda.

### 13.4 Idempotency

Command ID yang sama dengan payload sama mengembalikan hasil tersimpan atau progress, bukan eksekusi ulang. ID sama dengan payload berbeda ditolak. Journal disimpan sebelum side effect; crash recovery memeriksa keadaan sebenarnya sebelum melanjutkan. Jangan menjanjikan exactly-once side effect hanya dari QoS MQTT.

MVP maksimal satu mutasi per node. Read-only status query tetap boleh berjalan dengan rate limit. Full settings overwrite, shell arbitrary, nama service bebas, dan path file dari payload tidak didukung.

### 13.5 Deadline berbeda

Batas awal penerimaan command 15 detik. Timeout pelaksanaan config, start, atau restart ditentukan per operation dan kemampuan engine; operasi start yang melakukan JIT dapat jauh lebih lama daripada satu interval telemetry. Health harus terus berjalan selama menunggu.

Target bench ACK accepted p95 <=3 detik saat link stabil adalah sasaran uji, bukan SLA. Timeout operasi tidak boleh menyebabkan helper diterminasi secara buta ketika side effect sudah berjalan; catat unknown dan rekonsiliasi.

### 13.6 Feedback panel

Strip command menampilkan tahap yang bermakna: `Mengubah frekuensi`, `Menunggu verifikasi`, `Berhasil`, `Gagal: RANGE`, atau `Hasil belum pasti`. Details menampilkan operation ID dan proof, tanpa log rahasia.

---

<a id="s14"></a>
## 14. Start, stop, restart RDF, dan reboot Raspberry

### 14.1 Bedakan scope

`Stop RDF` tidak sama dengan `Stop Bridge`. `Restart RDF` tidak sama dengan `Reboot Raspberry`. Nama tombol dan operation harus eksplisit. Tidak ada tombol rutin untuk menghentikan jalur command sendiri.

### 14.2 Processing-only versus stack lifecycle

Ada dua kandidat adapter:

1. Processing-only: mengubah state processing pada engine melalui mekanisme yang benar-benar tersedia.
2. Stack lifecycle: start/stop seluruh unit DAQ/DSP yang telah diaudit.

Dokumen source menyebut kemampuan start/stop, tetapi belum membuktikan API terautentikasi siap pakai untuk deployment sekarang. Capabilities harus menyatakan mode yang benar-benar diterapkan. Jangan memalsukan `processing.start` dengan endpoint yang belum ada. [P1, bagian 13; P4, bagian 9]

### 14.3 Intent berhenti harus dihormati

Saat STOP diminta dan diterima:

```text
simpan desired=STOPPED
 -> publish STOPPING
 -> hentikan scope engine yang disepakati
 -> verifikasi scope benar-benar berhenti
 -> publish STOPPED
 -> bridge dan health tetap berjalan
```

Watchdog engine tidak boleh menafsirkan STOPPED sebagai crash. Persisted desired state harus diperhitungkan setelah agent restart maupun OS boot. Fase read-only belum memiliki otoritas untuk mengubah boot behavior engine.

### 14.4 Watchdog lama adalah integration gate

Arsip service memakai watchdog yang memeriksa HTTP UI dan merestart SDR bila tidak merespons. Pola itu dapat membatalkan Stop yang disengaja. Sebelum lifecycle command diaktifkan, watchdog harus sadar desired state atau ownership recovery dipindahkan secara terkontrol ke satu supervisor. Tidak boleh ada dua supervisor yang saling melawan. [P5, bagian 19-22; P1, bagian 13.4]

Restart bridge, kiosk, dan PPP memiliki policy masing-masing. Jangan menjalankan `pkill python`, `killall`, restart USB umum, atau script stop yang belum diperiksa cakupannya.

### 14.5 Reboot Raspberry

Reboot tetap fitur fase lanjutan, default disabled. Precondition: operasi lain tidak aktif, journal durable, operator berizin, maintenance lease aktif, challenge belum kedaluwarsa, dan risiko hilangnya node diterima.

Alur: prepare -> challenge terikat operasi/session -> konfirmasi -> execute sekali -> simpan intent -> tampilkan planned reboot -> helper menjadwalkan reboot -> koneksi hilang -> boot baru -> rekonsiliasi hasil.

Availability yang hilang bukan bukti reboot sukses. Verifikasi memerlukan `boot_id` baru dan state yang kembali; kesiapan RDF dinilai terpisah. Selama OS reboot tidak mungkin mengirim health baru. Ground menampilkan planned outage, lalu timeout/unknown bila tidak kembali. [P1, bagian 14]

Local maintenance bersifat terbatas waktu dan tidak hanya tombol UI. Backend memverifikasi session/permission dan mencatat operator. Tidak ada reboot pada halaman utama atau command retained.

---

<a id="s15"></a>
## 15. Sinkronisasi konfigurasi, waktu, dan DAQ

### 15.1 Jangan memakai satu tombol Sync untuk semua

| Istilah | Makna | Tindakan UI |
|---|---|---|
| Config sync | Ground mengetahui config efektif node | Refresh/Verify Config |
| Time sync | Jam lintas host punya basis dan uncertainty | Tampilkan Clock Status |
| DAQ sync | Frame/sample-delay/IQ coherent | Tampilkan DAQ Status; recovery terpisah |
| UI refresh | Ambil snapshot lokal baru | Refresh halaman, tanpa side effect |

Tombol Refresh Config tidak boleh diam-diam reset DAQ atau mengubah jam OS. [P1, bagian 12.1]

### 15.2 Source of truth

Raspberry adalah sumber konfigurasi efektif. Ground menyimpan mirror dan draft. Pisahkan `requested`, `persisted`, dan `observed_effective`. Read-back file membuktikan persisted, bukan otomatis runtime applied. Bila evidence runtime belum tersedia, status `PERSISTED_UNVERIFIED` tetap terlihat. [P1, bagian 12]

Status `CFG SAMA` pada Overview hanya muncul bila revision/digest aman sudah disepakati dan proof runtime memadai, serta receipt tidak basi. Bila Ground hilang, tampilkan `CFG terakhir sama` pada detail, bukan sync aktif yang tak bisa diverifikasi.

### 15.3 Frekuensi center dan VFO

Config UI membedakan RF center dengan VFO yang menghasilkan DoA. Kebijakan awal kandidat: satu VFO mengikuti target tuning, tetapi konversi unit native dan validitas passband harus diaudit. Mengubah center tanpa menangani VFO dapat menghasilkan tampilan salah walaupun file tersimpan.

Hasil baru setelah perubahan harus berhubungan dengan revision baru; tidak cukup sekadar menyalin `rev` terbaru ke semua snapshot cache.

### 15.4 Konflik writer

Ground, local maintenance, dan GUI engine lama tidak boleh menulis settings secara tak terkoordinasi. Pilih single writer atau lock/protocol yang dipakai semua writer. Bila GUI lama tidak dapat mengikuti, adapter mendeteksi external mutation dan masuk CONFLICT sampai direkonsiliasi.

Atomic write mencegah file parsial, bukan lost update antar-writer. Akses konfigurasi rahasia tetap lokal dan tidak masuk digest/payload public yang tidak diperlukan.

### 15.5 Clock dan data age

Gunakan monotonic clock untuk timeout, rate, lease, dan usia sejak diterima. UTC timestamp dipakai untuk correlation lintas host ketika clock dipercaya. `age_at_source + elapsed_since_receipt` tidak memasukkan transit yang tidak diketahui; jangan diberi label latency end-to-end presisi.

Jika clock belum dipercaya, local display tetap dapat menunjukkan receipt age, source progression, dan status clock. Mutasi dengan deadline absolut disabled pada MVP sampai clock gate tersedia. Tidak menjalankan layanan sinkronisasi waktu lewat T900 secara diam-diam tanpa budget dan desain terpisah.

<a id="s16"></a>
## 16. API lokal dan integrasi Ground

### 16.1 Local API usulan

Bind `127.0.0.1:8790`, configurable. Tidak membuka port baru ini ke T900 atau LAN secara default. Endpoints di bawah belum diimplementasikan.

| Endpoint | Fungsi | Batas |
|---|---|---|
| `GET /` | Static panel | Aset lokal, tanpa CDN |
| `GET /api/v2/snapshot` | Ringkasan Overview | Nonblocking, cached snapshot |
| `GET /api/v2/link` | Detail PPP/MQTT/receipt/queue | Tidak memicu test RF |
| `GET /api/v2/system` | Host dan DAQ | Metrik yang telah dikumpulkan |
| `GET /api/v2/config` | Config aman dan proof | Secret dihapus, bukan raw native |
| `GET /api/v2/capabilities` | Fitur yang didukung/diizinkan | Tidak mengaku fitur dari keberadaan tombol |
| `GET /api/v2/operations/latest` | Operasi terbaru | Journal terbatasi |
| `GET /api/v2/healthz` | Proses API/loop responsif | Tidak menyatakan DAQ atau radio healthy |
| `GET /api/v2/readyz` | Status kesiapan subsistem | Detail reason codes |
| `POST /api/v2/display/preferences` | Preferensi lokal | Terautentikasi sesuai deployment |
| `POST /api/v2/commands` | Local maintenance command | Disabled default, same CommandManager |

Semua response dinamis memakai kebijakan no-store untuk menghindari cache lama terlihat baru. Origin/CSRF/authentication dan size limit tetap diperlukan pada endpoint write; loopback bukan pengganti perlindungan browser. GET tidak melakukan start/stop, write settings, reboot, atau diagnostic berat.

### 16.2 Contoh canonical snapshot

Contoh ini memakai nama panjang untuk API lokal, bukan payload radio. Nilainya sintetis. Status eligibility dan availability tetap dinamis.

```json
{
  "schema_version": 2,
  "mode": "LIVE",
  "node_id": "uav-01",
  "agent_instance_id": "example-instance",
  "snapshot_seq": 412,
  "processing": {
    "desired": "RUNNING",
    "observed": "RUNNING",
    "source_age_ms": 200
  },
  "daq": {
    "state": "HEALTHY",
    "frame_progressing": true,
    "sync": {"frame": true, "sample_delay": true, "iq": true}
  },
  "detection": {
    "state": "VALID",
    "relative_doa_deg": 137.4,
    "frequency_hz": 433920000,
    "source_age_ms": 400,
    "confidence_native_db": 8.27,
    "power_native_db": -54.2,
    "angle_convention": "verified-example"
  },
  "link": {
    "usb": "PRESENT",
    "ppp": "UP",
    "mqtt_control": "CONNECTED",
    "mqtt_bulk": "CONNECTED",
    "ground_reception": "RECEIVING",
    "ground_receipt_age_ms": 3000
  },
  "config": {
    "sdr_revision": 8,
    "proof": "runtime",
    "ground_sync": "SYNCED"
  },
  "host": {"temperature_c": 61.0, "clock_state": "SYNCED"},
  "last_operation": {"id": "example-operation", "stage": "APPLIED"},
  "active_alerts": []
}
```

Ketika sensor tidak tersedia, nilai menjadi `null` dengan state UNKNOWN/UNAVAILABLE, bukan nol. Server tidak boleh terus memperbarui source age menjadi nol pada cache lama. `snapshot_seq` adalah kemajuan agregasi, sedangkan tiap substate tetap punya umur sumbernya sendiri.

### 16.3 Yang harus ditambahkan di Ground

Consumer v2, decoder angular/chunk assembly, safe state store, publisher receipt, command controller, dan UI untuk operation/proof. Ground yang terdokumentasi sebelumnya memiliki monitor MQTT subscriber-only; jalur command dan receipt bukan fitur yang boleh dianggap otomatis ada. [P6; P1, bagian 16]

Pesan radio yang diubah menjadi JSON API lokal Ground tidak dikirim ulang melalui T900. Broker-to-backend pada Ubuntu adalah hop lokal. Browser Ground dapat ditutup tanpa menghentikan penerimaan dan receipt backend.

### 16.4 Source selection di Ground

Pilih satu sumber otoritatif per node: MQTT v2 untuk operasi normal, LAN native adapter untuk maintenance bila dipilih eksplisit. Jangan mencampur DoA MQTT lama dan health HTTP baru lalu menamai gabungannya satu snapshot LIVE. Tampilkan source mode, age, dan provenance.

### 16.5 Tampilan grafik

Ground menampilkan curve age terpisah dari current DoA age. Marker peak kurva memakai frame kurva itu sendiri. Panah current DoA yang lebih baru boleh tampil sebagai marker berbeda, bukan seolah berasal dari snapshot sama.

Saat config berubah, kurva lama tidak dipakai untuk frekuensi baru. Saat chunk incomplete, tunggu atau buang; jangan menggambar kurva buatan. Encoding Q16/U8 dan source convention terlihat pada diagnostics.

---

<a id="s17"></a>
## 17. Penyimpanan state, jurnal operasi, dan logs

### 17.1 Pemisahan data

| Data | Penyimpanan usulan | Retensi |
|---|---|---|
| Latest telemetry/health/curve | Memory | Nilai terbaru, bukan history offline |
| Device/protocol state ringkas | Memory + snapshot terpilih | Rebuild sesudah restart |
| Command journal | SQLite | Bounded, durable untuk dedup/recovery |
| Effective config metadata/digest | SQLite atau file atomik | Persisted, aman, tidak raw secret |
| Processing desired state | Durable store | Memerlukan explicit adoption pada fase lifecycle |
| Display preferences | File atau database lokal | Persisted, terpisah dari revision SDR |
| Application logs | journald dengan quota | Ringkas, event-driven |

MVP tidak menulis setiap DoA/health ke SD card. Grafik tidak ditimbun saat offline. Ground boleh menyimpan histori sesuai kebutuhan, tetapi itu proyek storage terpisah dari bridge lokal.

### 17.2 Journal minimum

Field konseptual: operation ID, request digest, authenticated actor/source, requested action, base/target revision, node boot/session, accepted time, stage, proof, error, dan recovery marker.

Simpan intent secara durable sebelum side effect. Jika journal tidak dapat ditulis karena disk penuh atau permission, tolak mutasi dengan error yang jelas; telemetry read-only tetap berusaha berjalan.

### 17.3 Durability versus lifespan storage

Commit per transition penting, bukan per sample. Batasi retention dengan usia/jumlah/ukuran dan jangan menghapus record reboot/pending yang belum direkonsiliasi. Policy retensi final harus lebih panjang daripada jendela retry dan investigasi operasi yang disepakati.

### 17.4 Logs on-demand

Normalnya logs tetap lokal. Remote diagnosis mengambil ringkasan terbatas, misalnya rentang waktu pendek atau jumlah baris tertentu, dengan size cap dan redaction. Hentikan bulk lain bila diagnostic besar diizinkan.

Tidak menjalankan log tail tanpa batas melalui T900 sebagai bagian layar utama. Berbagai error berulang di-coalesce menjadi count dan last occurrence, bukan dicetak per tick.

---

<a id="s18"></a>
## 18. Struktur source code dan kontrak modul

### 18.1 Layout repository usulan

```text
rdf-node/
  README.md
  pyproject.toml
  requirements.lock
  config/
    example.yaml
    schema.json
  src/rdf_node/
    main.py
    models.py
    config_loader.py
    source_adapter.py
    source_validation.py
    normalization.py
    system_monitor.py
    state_store.py
    freshness.py
    mqtt_control.py
    mqtt_bulk.py
    scheduler.py
    angular_codec.py
    ground_receipt.py
    command_manager.py
    config_manager.py
    lifecycle_adapter.py
    operation_journal.py
    local_api.py
  src/rdf_helper/
    server.py
    policy.py
    actions.py
  frontend/
    src/
      main.ts
      api.ts
      view_state.ts
      overview.ts
      link.ts
      system.ts
      config.ts
      styles.css
    package.json
    package-lock.json
  deploy/
    systemd/
    kiosk/
    broker-policy-examples/
  tests/
    unit/
    fixtures/
    integration/
    mqtt/
    ui_480x320/
    failure_modes/
    performance/
  docs/
    protocol-v2.md
    operation-contract.md
    deployment.md
```

Struktur ini bukan daftar file yang sudah tersedia. Nama package dan folder disesuaikan setelah repository sumber dipilih. Jangan membuat agent kedua ketika repo existing sudah punya adapter yang bisa dipakai dengan audit.

### 18.2 Kontrak modul

| Modul | Input | Output | Tidak boleh |
|---|---|---|---|
| SourceAdapter | File sumber | Snapshot native berprovenance | Write engine atau MQTT publish langsung |
| Validator | Snapshot native + health | Eligibility/reasons | Mengubah stale menjadi fresh |
| StateStore | Observasi dan operasi | Immutable canonical snapshot | I/O blocking dari handler UI |
| SystemMonitor | proc/sysfs/service query | Host/link observation | Reset hardware |
| Scheduler | Snapshot eligible + budget | Publish jobs | FIFO telemetry tak terbatas |
| MQTTControl | Control topics | Events/receipt/result transport | Menjalankan shell dari callback |
| MQTTBulk | Chunk jobs | Angular stream | Memiliki privilege reboot |
| CommandManager | Request terautentikasi | Journaled operation | Menganggap PUBACK sebagai applied |
| ConfigManager | Patch allowlist | Persisted/effective proof | Full file replace dari Ground |
| Helper | Local RPC allowlist | Restricted action/evidence | Arbitrary command/unit/path |
| LocalAPI | StateStore | JSON/static UI | Memanggil collector ulang per request |
| Frontend | LocalAPI | Tampilan 480x320 | Menyimpan credential MQTT |

### 18.3 Concurrency

Gunakan tasks/worker terpisah untuk collector, monitor, scheduler, command worker, dan API. Satu worker mutasi per node. Callback Paho meneruskan pesan yang sudah dibatasi ke antrean internal lalu kembali; jangan menunggu fsync atau restart.

File parser harus tahan error. Snapshot state di-publish secara atomik secara logis agar UI tidak membaca setengah perubahan. Tetapkan shutdown cancellation yang jelas dan jangan kehilangan outcome hanya karena proses UI ditutup.

### 18.4 Mode simulasi

Mode DEMO memakai fixture lokal dan namespace MQTT demo terpisah bila perlu. Watermark `DEMO` selalu tampak. Mode ini dipakai untuk menguji layout, stale transitions, dan command state machine tanpa menjalankan operasi perangkat. Tidak mengisi status live dengan data sintetis ketika source error.

---

<a id="s19"></a>
## 19. Contoh konfigurasi deklaratif

Berikut adalah contoh **schema aplikasi yang diusulkan**, bukan berkas yang sudah dikenali software terpasang. Path `null` harus diisi melalui provisioning. Program harus bisa menampilkan SETUP REQUIRED tanpa mencoba akses lokasi tebakan.

Semua fitur write sengaja disabled sampai acceptance gate terkait lulus. Memindahkan flag menjadi true tidak menggantikan implementasi, otorisasi, atau bukti capability.

```yaml
schema_version: 2
node_id: uav-01
runtime_mode: read_only

source:
  share_dir: null
  doa_filename: DOA_value.html
  status_filename: status.json
  settings_filename: settings.json
  xml_filename: doa.xml
  xml_enrichment_enabled: false
  output_vfo: 0
  max_record_bytes: 8192
  poll_doa_ms: 250
  poll_status_ms: 500
  poll_config_mtime_ms: 1000
  require_authority_verified: true
  require_finite_numbers: true

link:
  serial_alias: /dev/t900
  expected_vid: '1a86'
  expected_pid: '7523'
  expected_usb_path: platform-xhci-hcd.0-usb-0:2:1.0
  local_ppp_ip: 10.90.0.2
  peer_ppp_ip: 10.90.0.1
  ppp_service: t900-ppp.service
  automatic_ppp_reconfiguration: false

mqtt:
  protocol: 5
  host: 10.90.0.1
  port: 8883
  topic_prefix: sdr/v2/uav-01
  control_client_id: uav-01-control
  bulk_client_id: uav-01-bulk
  keepalive_seconds: 15
  clean_start: true
  session_expiry_seconds: 0
  reconnect_min_seconds: 1
  reconnect_max_seconds: 30
  inflight_qos1_limit: 4
  sdk_queue_message_limit: 16
  tls_required: true
  verify_peer: true
  ca_file: null
  control_credentials_file: null
  bulk_credentials_file: null

telemetry:
  profile: balanced
  doa_hz: 1.0
  health_hz: 1.0
  health_detail_hz: 0.1
  state_refresh_seconds: 60
  navigation_enabled: false
  ground_receipt_interval_seconds: 5
  angular:
    enabled: true
    encoding: q16
    sample_count: 360
    interval_seconds: 4
    frame_chunk_bytes: 384
    chunk_envelope_bytes: 12
    max_pending_frames: 1
    receiver_max_incomplete_frames: 2
    assembly_timeout_seconds: 3
    pause_during_mutation: true
  expiry:
    doa_seconds: 3
    health_seconds: 5
    angular_seconds: 6

scheduler:
  policy: latest_value
  aggregate_model_target_kbit_s: 10
  bulk_burst_chunks: 1
  control_backlog_pause_ms: 500
  receipt_pause_after_seconds: 10
  recovery_stable_seconds: 20
  allow_untested_fast_profile: false

freshness:
  local_api_warning_ms: 2000
  local_api_stale_ms: 5000
  doa_warning_ms: 2500
  doa_stale_ms: 5000
  health_warning_ms: 3000
  health_stale_ms: 8000
  health_unreachable_ms: 15000
  angular_warning_ms: 6000
  angular_stale_ms: 10000
  receipt_warning_ms: 10000
  receipt_lost_ms: 15000

monitor:
  host_interval_seconds: 1
  service_interval_seconds: 2
  usb_fallback_interval_seconds: 5
  disk_interval_seconds: 30
  invasive_receiver_tests: false

control:
  config_patch_enabled: false
  processing_control_enabled: false
  stack_restart_enabled: false
  system_reboot_enabled: false
  max_active_mutations: 1
  require_trusted_clock: true
  require_runtime_proof: true
  accept_ttl_seconds: 15
  allow_arbitrary_shell: false
  require_maintenance_for_reboot: true
  maintenance_lease_seconds: 300
  reboot_challenge_seconds: 10
  initial_engine_ownership: observe_only
  engine_service: null
  helper_socket: /run/rdf-control-helper/control.sock

storage:
  state_dir: /var/lib/rdf-node
  journal_file: /var/lib/rdf-node/operations.sqlite3
  persist_telemetry_history: false
  log_raw_settings: false
  max_log_export_bytes: 16384

local_api:
  bind: 127.0.0.1
  port: 8790
  snapshot_interval_ms: 500
  read_only_default: true
  expose_raw_settings: false
  cache_dynamic_responses: false

display:
  width: 480
  height: 320
  orientation: landscape
  theme: dark
  snapshot_poll_ms: 500
  request_timeout_ms: 1000
  max_inflight_requests: 1
  main_page_scroll: false
  tabs: [overview, link, system, config]
  touch_capability: unknown
  minimum_primary_touch_height_px: 48
  external_assets_allowed: false
  local_control_enabled: false
```

### 19.1 Validasi lintas field

`node_id` dan prefix harus konsisten. `profile=balanced` harus cocok dengan rate yang dimuat; override yang berbeda harus ditandai sebagai custom profile dengan budget baru. Target model 10 kbit/s bukan token bucket physical UART mentah: scheduler perlu perhitungan biaya transport dan pengukuran implementasi.

`frame_chunk_bytes=384` adalah bytes isi frame sebelum envelope, bukan seluruh ukuran MQTT PUBLISH. `angular interval=4` berarti satu grafik tiap empat detik, bukan tiap chunk empat detik.

`ground_receipt_interval_seconds=5` adalah kontrak yang harus disepakati Ground. Mengubah config Raspberry saja tidak mengubah scheduler Ground secara otomatis.

`max_log_export_bytes=16384` adalah cap atas diagnostic, bukan ukuran yang selalu boleh dikirim seketika. Ekspor membutuhkan pacing dan penilaian apakah lebih tepat lewat management LAN.

### 19.2 Provisioning wajib

Isi source path, engine service yang benar, identity/certificate/credentials, dan kemampuan display. Lakukan validasi read-only sebelum aktivasi. Jangan mengganti `tls_required` atau `require_authority_verified` menjadi false hanya agar status terlihat hijau.

---

<a id="s20"></a>
## 20. Boot, autostart, dan failure recovery

### 20.1 Boot awal read-only

```text
OS boot
 +-> t900-ppp.service: menunggu/retry radio sesuai konfigurasi existing
 +-> engine SDR existing: tetap sesuai mekanisme yang sudah ada
 +-> rdf-edge.service: local API + monitor hidup
 |      +-> source belum ada: SOURCE UNAVAILABLE, tetap responsif
 |      +-> broker belum ada: MQTT DISCONNECTED, tetap collecting
 +-> graphical session: kiosk membuka local page
 +-> broker tersedia: handshake/state, health, DoA, angular terakhir
```

Local API tidak menunggu Ground. PPP tidak menunggu browser. Collector tidak membuka serial T900 yang sudah dimiliki pppd. Jangan menambahkan ketergantungan keras pada network-online sebagai pengganti reconnect handling.

### 20.2 Target service edge

Kriteria deployment, belum unit siap install:

| Item | Keputusan |
|---|---|
| Nama unit | `rdf-edge.service` untuk kompatibilitas rancangan induk |
| Package code | `rdf_node` pada layout usulan |
| Runtime user | Dedicated unprivileged service user |
| Program | Interpreter environment terpisah, absolute path |
| Restart | On failure dengan delay dan start-limit |
| API binding | Loopback 8790 |
| Akses source | Read-only kecuali config manager yang diizinkan |
| State directory | Writable terbatas untuk journal/config metadata |
| SDR dependency | Observasi, bukan Requires yang mematikan bridge |
| PPP dependency | Observasi/reconnect, bukan BindsTo yang mematikan bridge |
| Watchdog native | Hanya jika aplikasi benar-benar mengirim notify heartbeat |

systemd `Type=exec`/restart/watchdog harus dipilih sesuai kemampuan aplikasi, bukan label readiness DSP. Kegagalan browser tidak memicu restart engine. [E3]

### 20.3 Kiosk

Kiosk berjalan sebagai user graphical session yang benar, bukan root. Native display server, rotation, output name, dan touchscreen diperiksa. Tutorial resmi Raspberry Pi memakai mekanisme desktop tertentu; jangan menyalin path autostart tanpa memverifikasi deployment. [E5]

Jangan menggunakan browser `--no-sandbox` sebagai jalan pintas. Jangan membuka Chromium kedua/ketiga setiap retry autostart. Satu instance kiosk, profile browser terisolasi, dan restart hanya untuk kegagalan UI yang terdeteksi.

Halaman tidak memakai service worker yang menyimpan status lama sebagai fallback LIVE. Static assets boleh cache dengan versi, data status tidak. Tampilan `Tidak terhubung ke agent` harus tersedia saat request gagal.

### 20.4 Helper privileged

Helper hanya mempunyai Unix socket lokal dengan akses terbatas, tanpa listener TCP. Operation bernama dipetakan ke action allowlist dan unit/service fixed yang sudah diverifikasi. Tidak menerima shell string atau path bebas dari MQTT.

MQTT parser, HTTP server, dan browser tidak berjalan sebagai root. Helper memvalidasi ulang operation, maintenance state, dan request origin lokal sesuai policy. Sebelum memberi izin helper, audit script engine karena start/stop wrapper lama mungkin menghentikan proses lebih luas daripada yang diinginkan.

### 20.5 Recovery matrix

| Gangguan | Respons yang dirancang |
|---|---|
| USB T900 hilang | PPP reconnect policy existing; panel USB MISSING |
| PPP down | MQTT backoff; engine lokal lanjut |
| Broker tidak tersedia | Panel lokal tetap hidup; jangan restart engine |
| Ground consumer mati | MQTT bisa tetap OK; GRD RX menjadi LATE/LOST |
| File DoA parsial | Reject snapshot, retry terbatas, age tetap bertambah |
| DAQ gagal | Health DEGRADED; DoA tidak dipublish LIVE |
| RDF sengaja stop | Desired STOPPED dihormati; health tetap hidup |
| Edge crash | systemd restart edge; session baru dan journal recovery |
| Kiosk crash | Restart kiosk saja |
| Helper gagal | Write command ditolak; read-only tetap jalan |
| Disk penuh | Batasi log; tolak side effect yang tak bisa dijurnal |
| Reboot terencana | Planned outage; konfirmasi boot baru setelah kembali |

---

<a id="s21"></a>
## 21. Kinerja, layar, dan inventaris deployment

### 21.1 Resource gate

Tolok ukur utama bukan sekadar aplikasi tampil, melainkan tidak merusak processing SDR. Bandingkan kondisi engine-only, edge tanpa UI, dan edge dengan kiosk.

Ukur CPU/RAM masing-masing proses, suhu, dropped frame delta, frame interval, freshness, queue age, serta latensi command. Jangan menyimpulkan browser ringan hanya dari resolusi kecil.

Target awal engineering: tidak ada pertumbuhan memory/queue tak terbatas, tidak ada peningkatan dropped frame yang konsisten akibat fitur indikator, API p95 <=200 ms pada perangkat uji, dan perubahan tab tidak memicu burst radio. Nilai performa adalah acceptance target yang harus disesuaikan hardware, bukan hasil yang sudah dicapai.

### 21.2 Display gate

480x320 wajib diuji pada browser logical viewport dan panel fisik. Verifikasi scale, zoom, font, orientasi, safe area, dan hit target. Tidak mengubah resolusi kernel/driver secara otomatis dalam installer bridge.

Panel SPI, HDMI, dan DSI memerlukan pendekatan deployment berbeda. Jika display mengonsumsi CPU transfer framebuffer cukup besar, perlu diukur bersama DAQ. Dokumen ini tidak menetapkan driver dari perkiraan jenis layar.

### 21.3 Preflight read-only yang perlu dikumpulkan

Contoh berikut bukan installer dan tidak menghentikan layanan:

```bash
cat /etc/os-release
uname -m
free -h
python3 --version
systemctl status t900-ppp.service --no-pager
ip -br addr
ip route get 10.90.0.1
systemctl status sdr-doa.service --no-pager
printf 'Session type: %s\n' "$XDG_SESSION_TYPE"
printf 'Display: %s\n' "$DISPLAY"
printf 'Wayland display: %s\n' "$WAYLAND_DISPLAY"
```

Nama `sdr-doa.service` di atas adalah kandidat dari arsip. Jika tidak ada, cari nama unit sebenarnya; jangan membuat unit kedua hanya agar nama cocok. Variabel graphical session bisa kosong pada SSH walaupun desktop lokal berjalan, sehingga bukan bukti desktop tidak tersedia.

Setelah operator memverifikasi lokasi, periksa permission dan nama file output yang diperlukan. Jangan membagikan `.env`, private key, seluruh raw settings, atau output log yang belum di-redact.

### 21.4 Paket deployment masa implementasi

Rilis nantinya mencakup wheel/venv atau package yang dipilih, static assets, config example, validator, service templates, rollback procedure, dan release manifest. Tidak melakukan apt upgrade seluruh sistem, perubahan Conda SDR, rewrite udev, atau reboot otomatis sebagai efek samping installer aplikasi.

Unit existing dibaca dan dibackup sebelum perubahan yang diotorisasi. Deployment pertama read-only; lifecycle takeover menjadi fase tersendiri.

---

<a id="s22"></a>
## 22. Matriks pengujian dan acceptance gate

Semua pengujian berikut masih **rencana**. Status belum dijalankan pada perangkat pengguna. Uji offline/sintetis tidak menggantikan bench test Raspberry dan Ubuntu.

| ID | Skenario | Hasil yang harus terjadi |
|---|---|---|
| T01 | Source path belum diisi | SETUP REQUIRED; API tetap hidup |
| T02 | Source directory tidak dapat dibaca | Error terstruktur, tidak crash loop |
| T03 | CSV valid 377 field | Metadata + 360 nilai terparse |
| T04 | CSV parsial saat rewrite | Reject snapshot, retry, bukan LIVE palsu |
| T05 | CSV NaN/Infinity/out-of-range | Ditolak dengan reason |
| T06 | CSV multi-record/VFO ambigu | Tidak memilih baris sembarang |
| T07 | CSV timestamp tidak berubah | Data age naik, tidak q baru palsu |
| T08 | Status live tetapi DAQ false | Health DEGRADED; DoA gate tertutup |
| T09 | No-detection tanpa evidence eksplisit | NO_FRESH_DOA, bukan diagnosis pasti |
| T10 | SNR XML tidak berkorelasi | Tidak digabung ke CSV |
| T11 | Clock jump/future timestamp | UNTRUSTED/invalid sesuai policy |
| T12 | Config berubah, CSV lama | Tidak diberi revision baru |
| T13 | Power native negatif | Tidak abs/log ulang atau fake radio RSSI |
| T14 | PPP tidak tersedia saat boot | Edge/API/kiosk tetap dapat hidup |
| T15 | Broker tidak aktif | Backoff; tidak restart engine |
| T16 | TLS identity salah | Reject TLS, alasan aman terlihat |
| T17 | SUBACK wajib gagal | Command belum READY |
| T18 | Broker hidup, consumer Ground mati | MQTT OK tetapi GRD RX LOST |
| T19 | Receipt session salah | Ditolak |
| T20 | Receipt baru dengan health q macet | Data-delivery progress bermasalah terlihat |
| T21 | QoS 0 saat offline | Tidak menimbun telemetry sejarah |
| T22 | Bulk socket macet | Pause/reset bulk, control tetap diusahakan |
| T23 | Reconnect berulang | Session/state pulih, tidak banjir history |
| T24 | Client ID duplikat | Terdeteksi dan dilaporkan, bukan flapping tersembunyi |
| T25 | Q16 encode/decode | 360 sampel, error sesuai toleransi codec |
| T26 | Chunk hilang/duplikat/berbeda | Timeout atau reject; tidak kurva campuran |
| T27 | Grafik lama, DoA lebih baru | Age/marker terpisah |
| T28 | Command ketika bulk aktif | Chunk baru ditunda; health/ACK diprioritaskan |
| T29 | ID command sama, payload sama | Hasil lama/progress, tidak side effect ulang |
| T30 | ID sama, payload beda | Ditolak |
| T31 | Expired/base_rev salah | EXPIRED/CONFLICT |
| T32 | Request mutasi kedua | BUSY dengan operation aktif |
| T33 | File config tersimpan, runtime tidak berubah | PERSISTED_UNVERIFIED, bukan APPLIED |
| T34 | GUI lama menulis bersamaan | Konflik terdeteksi, tidak lost update diam-diam |
| T35 | Stop RDF | Engine berhenti, edge/health/MQTT tetap hidup |
| T36 | Watchdog setelah Stop | Tidak menyalakan engine melawan intent |
| T37 | Agent restart setelah Stop | Desired STOPPED tetap dihormati pada fase ownership |
| T38 | Crash setelah accepted sebelum result | Journal direkonsiliasi, bukan execute ulang buta |
| T39 | Reboot tanpa maintenance/challenge | Ditolak |
| T40 | Reboot sukses | Boot ID baru; hasil dan kesiapan RDF terpisah |
| T41 | Reboot tidak kembali | Ground planned outage lalu unknown/timeout |
| T42 | Overview 480x320 | Tidak scroll/clipping; nav 48 px |
| T43 | Label/alarm panjang | Wrap atau details, bukan hilang di luar viewport |
| T44 | API HTTP 200 tetapi snapshot beku | UI source STALE |
| T45 | Browser ditutup | Bridge/engine/PPP tidak terhenti |
| T46 | Browser frozen | Optional kiosk watchdog hanya restart browser |
| T47 | Tanpa internet | Semua static assets dan panel lokal berfungsi |
| T48 | Touch tidak tersedia | Navigasi keyboard/mouse dapat dipakai |
| T49 | Disk penuh/journal gagal | Mutasi ditolak, read-only tetap sebisa mungkin |
| T50 | Unauthorized local POST/MQTT command | Ditolak dan diaudit tanpa secret |
| T51 | BALANCED dengan command burst | Byte rate, age, queue, dan ACK diukur |
| T52 | Nav diaktifkan | Profil/budget diperbarui, tidak tetap klaim 9.14 |
| T53 | Kiosk+edge dibanding engine-only | Tidak memperburuk DAQ tanpa terdeteksi |
| T54 | Cold boot Ground dan Raspberry berbeda urutan | Reconnect benar, tidak perlu manual start |
| T55 | USB hotplug T900 | Alias/PPP recovery diuji; tidak reset Kraken |
| T56 | Long-duration soak | Memory/queue/log stabil dan state jujur |

### 22.1 Target penerimaan yang konkret

- Semua fitur read-only melewati test parser/freshness/UI sebelum valid telemetry dipublikasikan.
- Layout lolos viewport 480x320 dan observasi panel nyata, termasuk status error.
- Health ringkas tetap tersedia ketika engine sengaja berhenti selama bridge/link sehat.
- Radio profile dibuktikan melalui pengukuran TX/RX dua arah, bukan payload JSON saja.
- ACK accepted p95 <=3 detik pada kondisi bench stabil menjadi target awal; catat juga max, timeout, dan kondisi pengujian.
- Tidak ada command applied tanpa proof yang didefinisikan.
- Tidak ada secret pada UI, payload, atau logs normal.
- Cold boot/hotplug/reconnect diuji sebelum menganggap instalasi unattended siap.

Untuk soak, mulai dengan sesi bench terkontrol, lalu durasi lebih panjang sesuai operasi yang diharapkan. Durasi dan kondisi harus dicatat, bukan diberi label lulus tanpa log bukti.

---

<a id="s23"></a>
## 23. Tahap implementasi

### Fase 0 - Inventaris dan kontrak

Verifikasi source path, unit engine, OS/interpreter, display mode/touch, versi broker/client, dan baseline DSP. Sepakati namespace v2 serta decoder Ground. Hasilnya manifest deployment dan daftar capability yang benar-benar bisa dibuat.

### Fase 1 - Panel 480x320 dan backend read-only

Buat SourceAdapter, StateStore, SystemMonitor, LocalAPI, dan empat halaman. Mulai dengan fixture DEMO, lalu source nyata dengan label validitas. Belum ada write config/start/stop/reboot. Definition of done: panel berfungsi tanpa internet/Ground dan sumber error tampil jujur.

### Fase 2 - MQTT control telemetry

Pasang koneksi client, autentikasi/ACL, DoA/health/state, receipt Ground, reconnect, queue bounds, dan CONTROL profile. Broker atau Ground outage tidak menghentikan panel. Definition of done: telemetry end-to-end terbukti dan GRD RX berdasarkan receipt nyata.

### Fase 3 - Full angular 360

Gunakan codec/chunk v2 dari dokumen induk, decoder Ground, pacing, dan BALANCED. Lakukan uji bandwidth dengan health/ACK, bukan array sendirian. Definition of done: grafik Ground sesuai source dalam toleransi encoding, age/correlation terlihat.

### Fase 4 - Config get/patch

Implementasikan allowlist, conversion units, journal/dedup, single-writer, runtime proof, dan sync state. Local config panel awal tetap ringkas. Definition of done: perubahan yang sukses terbukti, konflik dan unknown tidak disembunyikan.

### Fase 5 - Start/stop/restart stack

Audit lifecycle dan selaraskan watchdog sebelum mengaktifkan tombol. Adoption desired state dilakukan eksplisit. Definition of done: STOP tidak merusak bridge dan tidak dibatalkan watchdog, start/restart mempunyai evidence.

### Fase 6 - Reboot maintenance

Implementasikan prepare/execute, authorization, helper, durable reboot intent, dan proof boot baru. Definition of done: replay/expired/unauthorized ditolak dan planned outage ditampilkan benar.

### Fase 7 - Hardening dan rilis

Soak test, cold boot, radio reconnect, display recovery, log quota, package lock, update/rollback, dan dokumentasi operasi. Tidak menaikkan rate default sampai hasil ukur mendukung.

### Urutan yang disarankan untuk pengguna

Bangun **panel indikator + bridge read-only dahulu**, kemudian MQTT dan grafik, baru remote control. Ini memberi hasil yang cepat dapat dinilai tanpa membahayakan konfigurasi SDR yang sudah berjalan. Semua fase tetap menuju aplikasi lengkap yang diminta, bukan menghapus kebutuhan command.

---

<a id="s24"></a>
## 24. Keputusan final dan brief pengembang

### 24.1 Keputusan yang siap dijadikan baseline

| ID | Keputusan |
|---|---|
| D01 | Target viewport 480x320 landscape, bukan scaled-down 800x480 |
| D02 | Empat halaman: Utama, Link, Sistem, Config |
| D03 | Read-only default; menu maintenance tidak muncul sebagai kontrol aktif sebelum gate |
| D04 | Python bridge + Local API + frontend ringan; browser bukan broker/publisher |
| D05 | Engine existing dan PPP tidak direwrite pada instalasi awal |
| D06 | Satu broker Ground, control/bulk client terpisah dalam agent |
| D07 | API loopback usulan 8790, static assets lokal |
| D08 | Poll UI 2 Hz; DoA/health radio 1 Hz; Q16 angular tiap 4 detik |
| D09 | Nav OFF sampai sumber dan budget disepakati |
| D10 | MQTT CONNECTED dan GROUND RECEIVING adalah indikator terpisah |
| D11 | Quality native dan power native tidak dipalsukan menjadi persen/dBm/RSSI T900 |
| D12 | Source revision, timestamps, dan curve/current DoA correlation tetap eksplisit |
| D13 | Mutasi satu per node, journal, idempotency, expiry, revision, runtime proof |
| D14 | Stop engine tidak menghentikan edge; watchdog menghormati intent |
| D15 | Reboot hanya maintenance terotorisasi dengan verifikasi boot baru |
| D16 | Konfigurasi display, bridge, dan SDR mempunyai revision berbeda |
| D17 | Aplikasi dapat hidup dalam SETUP REQUIRED; tidak menebak path dan capability |
| D18 | Semua angka bandwidth adalah model sampai diukur pada deployment |

### 24.2 Keputusan yang menunggu inventaris, bukan asumsi

Model Raspberry/RAM, OS versi aktual, jenis display/touch, source path, unit engine, runtime control evidence, broker version, certificates, clock provider, dan repo Ground actual.

Tidak perlu menjawab semua ini untuk menyepakati arsitektur. Namun deployment script yang dapat mengubah host tidak boleh dibuat dengan menebak nilai tersebut.

### 24.3 Brief implementasi untuk pengembang atau AI coding

```text
Bangun RDF Node sesuai planning 480x320 ini dan kontrak v2 dokumen induk.

Mulai hanya fase read-only:
- satu backend service untuk source adapter, monitor, state store, MQTT, API;
- satu frontend kiosk 480x320 yang membaca API lokal;
- jangan ubah PPP, environment DSP, atau engine lifecycle;
- jangan berasumsi root SDR atau service name dari username;
- gunakan fixture berlabel DEMO sebelum source nyata;
- file CSV/XML/settings bukan kontrak langsung frontend.

Setiap perubahan:
- jelaskan file yang diubah dan fase yang dikerjakan;
- tambah test untuk failure mode, tidak hanya happy path;
- validasi parser, freshness, config, dan layar 480x320;
- jangan mengekspos credential;
- jangan mem-publish DoA stale/unverified sebagai LIVE;
- jangan aktifkan write flag sebelum capability dan acceptance gate;
- tampilkan hasil yang belum pasti sebagai UNKNOWN, bukan sukses.

Radio:
- preserve namespace v2, unit, session, codec, dan receipt contract;
- profile CONTROL dulu, BALANCED setelah pengukuran;
- health dan command tidak boleh terhambat bulk;
- tidak ada queue history offline tak terbatas;
- grafik 360 berasal dari source, bukan dari satu current DoA.

Deployment:
- template bukan bukti executable sudah terpasang;
- tidak ada remote restart/reboot tanpa otorisasi tugas tersendiri;
- source path, OS/display, dan service mapping harus diverifikasi;
- seluruh hasil test harus menyebut apakah lokal sintetis atau perangkat nyata.
```

### 24.4 Definisi hasil akhir aplikasi

Raspberry menyala, engine/PPP mengikuti konfigurasi yang disepakati, edge hidup mandiri, dan layar langsung memberi indikator 480x320. Saat Ground tersedia, telemetry serta kurva masuk backend Ground. Saat Ground hilang, layar menunjukkan kehilangan delivery tetapi tetap menampilkan kondisi lokal. Command berjalan melalui jalur terotorisasi dengan outcome yang dapat diverifikasi. Gangguan UI tidak menghentikan RDF atau komunikasi.

---

<a id="s25"></a>
## 25. Sumber, provenance, dan batas validasi

### 25.1 Sumber project

**[P1] `Rancangan_Lengkap_RDF_Telemetry_Command_Dashboard_Raspberry_5inci.md`**  
Rujukan induk: bagian 1-3 untuk baseline dan arsitektur; 4-9 untuk data/codec/MQTT/budget; 10-14 untuk state, command, config, lifecycle, reboot; 15-20 untuk panel/service/security; lampiran B untuk kalkulator. Planning ini mengganti asumsi resolusi layarnya secara eksplisit, tidak mengganti fakta historis.

**[P2] `Dokumentasi_Throughput_T900_PPP_dan_Payload_SDR.md`**  
Bagian 3-7 dan 26-28: metode dan hasil bench historis serta batas pemakaiannya.

**[P3] `SDR_DOA_8081_DATA_REFERENCE.md`**  
Bagian 3-8: format CSV/XML/status/settings, angular 360, nilai snapshot, freshness, dan interpretasi.

**[P4] `SDR_DOA_UPSTREAM_ARCHITECTURE.md`**  
Bagian 2, 4, 5, 7, dan 9: engine/UI coupling, DSP/DAQ, settings watcher, source convention, kemampuan kandidat.

**[P5] `Dokumentasi_Service_SDR_T900_PPP_Autostart.md`**  
Bagian 7, 14-22: service/wrapper dan watchdog historis yang perlu diperiksa sebelum lifecycle takeover.

**[P6] `SDR_DOA_FRONTEND_WORKFLOW.md`**  
Arsitektur Ground Python + React/TypeScript/Vite dan monitor subscriber-only; bukan bukti command/receipt sudah tersedia.

**[P7] `SDR_DOA_RASPBERRY_READONLY_DIAGNOSTICS.md`**  
Bagian 1, 4, 7, 11: batas diagnosis, USB enumeration, service state, dan health DAQ.

**[C1] Percakapan pengguna, 29 September 2026.**  
Output identitas USB, akun `rdf`, koneksi picocom/PPP berhasil, kebutuhan full angular dan kontrol, serta resolusi aktual yang diminta 480x320. Tidak ada klaim pengujian live tambahan oleh penyusun.

### 25.2 Rujukan teknis eksternal

Diperiksa pada 29 September 2026. Rujukan mendukung perilaku teknologi, bukan menyatakan versi paket terpasang. URL ditulis sebagai kode agar dapat disalin dari Markdown mandiri.

**[E1] Eclipse Paho MQTT Python - client documentation.** Network loop, callbacks, reconnect, dan queue/in-flight API.

`https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html`

**[E2] OASIS MQTT Version 5.0, OASIS Standard.** Semantik MQTT, QoS, session/message expiry, dan acknowledgements.

`https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html`

**[E3] systemd - source manual systemd.service.** Lifecycle unit, restart, type, dan watchdog. Source XML resmi digunakan karena halaman rendered manual tidak berhasil diambil saat pemeriksaan.

`https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml`

**[E4] systemd - source manual systemd.unit.** Ordering dan dependency unit.

`https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.unit.xml`

**[E5] Raspberry Pi - How to use a Raspberry Pi in kiosk mode.** Pola browser kiosk; detail autostart tetap mengikuti desktop aktual.

`https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/`

**[E6] Eclipse Mosquitto - mosquitto.conf manual.** Listener, authentication-related configuration, dan broker queue policy. Gunakan versi deployment untuk syntax final.

`https://mosquitto.org/man/mosquitto-conf-5.html`

**[E7] IETF RFC 1662 - PPP in HDLC-like Framing.** Framing dan escaping; angka model bukan capture serial nyata.

`https://www.rfc-editor.org/rfc/rfc1662.html`

**[E8] IETF RFC 9293 - Transmission Control Protocol.** Ordered byte stream dan retransmission sebagai batas scheduling setelah data masuk transport.

`https://www.rfc-editor.org/rfc/rfc9293.html`

### 25.3 Yang diperiksa saat menyusun file ini

Kesesuaian bagian planning dengan dokumen induk dan kebutuhan terbaru ditelaah. Kalkulator bandwidth referensi dijalankan ulang secara lokal; angka profil dan transaksi pada dokumen ini konsisten dengan model tersebut. Alokasi tinggi area Overview dijumlahkan menjadi 320 pixel. Struktur Markdown, referensi anchor daftar isi, YAML, dan JSON contoh diperiksa secara programatik.

Pemeriksaan tersebut tidak sama dengan implementasi aplikasi atau pengujian UI. Tidak ada browser kiosk yang dirender, tidak ada hardware yang diakses, tidak ada instalasi MQTT, perubahan service, write settings, start/stop, maupun reboot perangkat. Seluruh 56 skenario pada bagian pengujian adalah acceptance plan, bukan daftar test yang dinyatakan sudah lulus.

**Status akhir:** siap menjadi baseline planning implementasi; source/deployment aktual tetap memerlukan inventaris sebelum kode installer atau fungsi mutasi dibuat.
