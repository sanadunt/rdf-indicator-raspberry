# Rancangan Lengkap Telemetry RDF, Command Dua Arah, dan Dashboard Raspberry 5 Inci

**Project:** Telemetry RDF / SDR-DoA  
**Versi dokumen:** 2.0 - rancangan integrasi, bukan bukti deployment  
**Tanggal:** 29 September 2026, WIB  
**Platform:** Raspberry UAV + KrakenSDR + T900, Ubuntu Ground + T900  
**Hasil yang dituju:** Grafik angular 360 titik di Ground, kontrol konfigurasi dan lifecycle yang terverifikasi, serta indikator lokal di layar Raspberry 5 inci.

> **Keputusan utama:** Sistem dapat membawa grafik 360 titik, data DoA, health, dan command pada link yang terbatas, tetapi tidak semua stream boleh berjalan cepat sekaligus. Profil awal yang dihitung dalam dokumen ini memakai DoA 1 Hz, health 1 Hz, dan grafik 360 titik setiap 4 detik. Profil yang lebih cepat harus lulus pengukuran aplikasi nyata. Bridge tetap bekerja tanpa browser, tanpa layar, dan ketika proses RDF dihentikan.

> **Batas dokumen:** Ini spesifikasi yang diusulkan untuk implementasi berikutnya. Dokumen tidak memasang broker, tidak mengubah konfigurasi, tidak menjalankan command pada Raspberry/Ubuntu, dan tidak membuktikan bahwa DAQ saat ini sehat. Paket sumber yang tersedia di percakapan adalah 11 dokumen Markdown, bukan checkout lengkap source code aplikasi.

## Daftar Isi

- [1. Dasar, bukti, dan perubahan kebutuhan](#bagian-1)
- [2. Sasaran sistem dan batas ruang lingkup](#bagian-2)
- [3. Arsitektur proses dan aliran data](#bagian-3)
- [4. Data native, normalisasi, dan arti full DoA](#bagian-4)
- [5. Kontrak data, identitas, dan versi](#bagian-5)
- [6. Full angular binary dan pengiriman potongan](#bagian-6)
- [7. MQTT, koneksi, QoS, dan delivery semantics](#bagian-7)
- [8. Scheduler, prioritas, dan perlindungan latency](#bagian-8)
- [9. Perkiraan bandwidth yang dapat direproduksi](#bagian-9)
- [10. Health, freshness, dan arti indikator status](#bagian-10)
- [11. Command protocol dan state machine operasi](#bagian-11)
- [12. Settings, sinkronisasi, dan proof perubahan](#bagian-12)
- [13. Start, stop, dan restart RDF tanpa kehilangan bridge](#bagian-13)
- [14. Reboot Raspberry yang terkontrol](#bagian-14)
- [15. Dashboard Raspberry 5 inci](#bagian-15)
- [16. Integrasi Ground Dashboard](#bagian-16)
- [17. Service, boot, dan recovery](#bagian-17)
- [18. Security, privilege, dan audit trail](#bagian-18)
- [19. Log, settings penuh, dan diagnosis on-demand](#bagian-19)
- [20. Struktur aplikasi dan konfigurasi usulan](#bagian-20)
- [21. Tahapan implementasi dan definition of done](#bagian-21)
- [22. Matriks pengujian minimum](#bagian-22)
- [23. Runbook operasi dan diagnosis](#bagian-23)
- [24. Risiko, keputusan terbuka, dan batas validasi](#bagian-24)
- [25. Ringkasan keputusan implementasi](#bagian-25)
- [Lampiran A. Codec Q16 referensi yang sudah diuji secara lokal](#bagian-lampiran-a)
- [Lampiran B. Kalkulator bandwidth referensi](#bagian-lampiran-b)
- [Lampiran C. Kode error dan aturan antarmuka](#bagian-lampiran-c)
- [Lampiran D. Brief implementasi untuk pengembang/AI coding](#bagian-lampiran-d)
- [Lampiran E. Sumber dan provenance](#bagian-lampiran-e)

<a id="bagian-1"></a>
## 1. Dasar, bukti, dan perubahan kebutuhan

### 1.1 Cara membaca status informasi

Dokumen membedakan empat jenis informasi:

| Label | Arti |
|---|---|
| **Sumber project [Sxx]** | Pernyataan yang tercatat pada dokumen project; dapat berupa hasil uji lama atau rancangan lama |
| **Laporan pengguna [C1]** | Hasil yang pengguna laporkan pada percakapan 29 September 2026; tidak diuji ulang oleh penyusun dokumen |
| **Rujukan teknis [Exx]** | Dokumentasi primer protokol, library, dan service manager yang diperiksa untuk rancangan ini |
| **Usulan desain / estimasi** | Pilihan implementasi, angka model, threshold, atau kontrak baru; belum menjadi hasil pengujian perangkat |

Setiap istilah seperti "default", "wajib", "gate", dan "target" dalam bagian rancangan berlaku untuk **implementasi yang akan dibuat**, bukan klaim bahwa fitur itu sudah ada.

### 1.2 Baseline yang dapat digunakan

Menurut laporan pengguna, tes `picocom` dua arah dan koneksi IP PPP Raspberry-Ground telah berhasil. Pengguna juga telah mengikuti penyiapan service otomatis. Cold boot, USB hotplug, RF reconnect berkepanjangan, dan pengiriman MQTT nyata belum memiliki hasil uji baru yang dilampirkan. [C1]

| Komponen | Baseline |
|---|---|
| Raspberry / UAV | IP PPP `10.90.0.2`, alias serial `/dev/t900` |
| Ubuntu / Ground | IP PPP `10.90.0.1`, alias serial `/dev/t900-ground` |
| Serial T900 | Baseline 57.600 bit/s, transparent serial |
| Raspberry USB identity | `1a86:7523`, `ID_PATH=platform-xhci-hcd.0-usb-0:2:1.0` |
| Ubuntu USB identity | `1a86:7523`, `ID_PATH=pci-0000:00:14.0-usb-0:3:1.0` |
| Service PPP Raspberry | `t900-ppp.service` |
| Service PPP Ubuntu | `t900-ground-ppp.service` |
| Username yang terlihat sekarang | `rdf` di kedua host |
| Akun/path pada arsip lama | `doasdr`, `/home/doasdr/doasdr`; jangan dianggap otomatis masih cocok |

Identitas USB di atas berasal dari output pengguna. `ID_SERIAL=1a86_USB_Serial` adalah label generik pada output tersebut, bukan bukti identitas unit unik. Kombinasi VID/PID dan physical path mengunci **kelas perangkat pada port itu**, bukan membuktikan bahwa unit tersebut selalu T900 yang sama jika perangkat CH340 lain dipasang pada port yang sama. [C1; S04, bagian 3-4]

PPP adalah jaringan IP point-to-point, bukan Ethernet bridge. Dokumen ini tidak menambahkan DHCP, NAT, IP forwarding, atau perubahan default route. Akses dashboard Ground ke broker lokal tidak memerlukan Raspberry masuk ke LAN Ethernet yang sama. [S03, bagian 6-7]

### 1.3 Status historis bukan status perangkat hari ini

Dokumen pemeriksaan Data Out terdahulu mencatat `daq_ok=false`, dropped frames meningkat, dan snapshot DoA tidak berubah. Informasi tersebut harus tetap dipertahankan sebagai temuan historis, bukan dihapus karena koneksi IP sekarang berhasil. Sebaliknya, dokumen ini juga tidak menyimpulkan bahwa DAQ hari ini masih gagal. Status aktual perlu dibaca kembali. [S06, bagian 5-8]

**Koneksi IP berhasil tidak membuktikan DoA valid.** Gate DAQ, freshness, konvensi sudut, dan kalibrasi tetap harus diuji.

### 1.4 Revisi eksplisit terhadap rancangan sebelumnya

| Area | Rancangan/dokumentasi sebelumnya | Permintaan atau keputusan baru dalam dokumen ini |
|---|---|---|
| Angular 360 | Lokal atau on-demand; tidak masuk stream normal | Masuk ruang lingkup telemetry, dengan rate rendah, binary encoding, dan budget khusus |
| Command | Fokus config get/patch; reboot tidak aktif pada prototype | Tambahkan start/stop RDF, restart stack, dan reboot Raspberry sebagai fase tersendiri dengan otorisasi dan verifikasi |
| Dashboard Raspberry | Belum ditetapkan | Aplikasi indikator lokal untuk layar 5 inci, terpisah dari engine dan bridge |
| Rate DoA awal | Sering diusulkan 2 Hz | Default konservatif 1 Hz ketika grafik, health, dan overhead dua arah dihitung bersama |
| Confidence | Kontrak lama memberi contoh `0..1` | Native metric tetap ditampilkan sebagai PAPR-like dB sampai pemetaan kualitas disetujui |
| Namespace | Draft `sdr/v1/...` | Usulan `sdr/v2/...` karena semantik quality dan schema berubah; jangan mengganti v1 diam-diam |
| Perhitungan ukuran | Beberapa contoh bersifat target/asumsi | Payload contoh di bagian anggaran benar-benar diserialisasi dan dihitung |

Perubahan ini adalah **usulan versi baru**, bukan pernyataan bahwa dokumen lama sudah memuat fitur-fitur tersebut. Sumber lama untuk batas command dan frontend tetap dicatat. [S10, bagian 9.6-9.7; S08]

### 1.5 Yang belum diketahui dan tidak diasumsikan sebagai fakta

Model Raspberry, RAM tersedia saat DSP aktif, versi OS deployment sekarang, resolusi layar, orientasi layar, dukungan touch, path runtime SDR, versi broker/client, MTU PPP, kondisi RF, sumber GPS/heading, serta kemampuan kontrol runtime yang benar-benar dapat dipanggil belum dikonfirmasi lengkap.

Desain layar memakai **800 x 480 landscape sebagai kanvas awal**, bukan klaim spesifikasi layar pengguna. Wajib diuji ulang pada resolusi nyata. Tidak ada port, file executable, atau endpoint baru pada dokumen ini yang dianggap sudah terpasang.

<a id="bagian-2"></a>
## 2. Sasaran sistem dan batas ruang lingkup

### 2.1 Sasaran fungsional

Sistem yang selesai harus memungkinkan operator Ground:

1. Melihat DoA terbaru, frekuensi, kualitas native, power, dan umur data.
2. Melihat grafik angular **seluruh 360 titik**, bukan kurva yang dibuat dari satu nilai DoA.
3. Mengetahui perbedaan link terhubung, broker terhubung, backend menerima, DAQ sehat, dan DoA fresh.
4. Membaca konfigurasi operasional yang aman, mengubah subset yang diizinkan, dan melihat hasil sebenarnya.
5. Meminta RDF start/stop tanpa mematikan jalur command dan status.
6. Meminta restart stack RDF serta, pada mode maintenance, reboot Raspberry.
7. Melihat status operasi seperti accepted, applying, verifying, completed, rejected, atau outcome unknown.
8. Mengakses diagnosis yang dibatasi tanpa membanjiri T900.

Raspberry harus menyediakan indikator lokal yang tetap informatif ketika Ground offline. Indikator itu membantu menjawab: "RDF berjalan?", "T900 terpasang?", "IP link naik?", "Ground benar-benar menerima?", "setting sinkron?", dan "ada command yang sedang diproses?".

### 2.2 Bukan sasaran

Tidak ada pengiriman rutin raw IQ, audio, spectrum frekuensi, waterfall, video layar, atau file rekaman besar. Tidak ada kontrol penerbangan, motor, navigasi otomatis UAV, ataupun integrasi autopilot yang diasumsikan sudah tersedia. Reboot Raspberry dalam dokumen ini berarti reboot komputer RDF, bukan perintah pada flight controller.

### 2.3 Empat prinsip yang tidak boleh dikompromikan

**Data benar sebelum data cepat.** Record stale tidak boleh dipoles menjadi LIVE dengan mengganti timestamp.

**Hasil command harus dibuktikan.** Keberhasilan publish atau ACK transport bukan bukti frekuensi sudah berubah.

**Status harus tetap hidup ketika RDF stop.** Bridge dan host monitor bukan child process dari aplikasi RDF.

**Aplikasi layar bukan infrastruktur komunikasi.** Menutup browser atau mematikan panel tidak boleh memutus MQTT maupun PPP.

<a id="bagian-3"></a>
## 3. Arsitektur proses dan aliran data

### 3.1 Peta keseluruhan

```text
RASPBERRY / UAV

KrakenSDR -> Heimdall DAQ -> SDR-DoA/DSP
                                |
                                +--> status.json
                                +--> DOA_value.html
                                +--> doa.xml
                                +--> settings.json
                                |
                         [Source Adapter]
                                |
                  [Health/Freshness/Authority Gate]
                                |
                     [Canonical State Store]
                       /                  \
                      /                    \
       [Local API + static assets]    [MQTT Scheduler]
                  |                   /             \
          Browser kiosk 5 inci   control client    bulk client
                  |                   \             /
          loopback saja                PPP / T900
                                            |
UBUNTU / GROUND                             |
                                            v
                                  [Mosquitto Broker]
                                            |
                                [Ground Backend]
                             /        |         \
                         State     Command     Local log
                           |       Controller
                           +----------+
                                      |
                              Ground Dashboard

JALUR COMMAND RASPBERRY:
MQTT control -> Validator -> Journal -> Worker -> Restricted helper
                                                 |
                            settings manager / RDF lifecycle / reboot
                                                 |
                              read-back -> operation result -> Ground
```

"Bridge" di sini berarti **adapter aplikasi yang menghubungkan SDR dan MQTT**, bukan Linux bridge Ethernet dan bukan keharusan memakai dua broker Mosquitto.

### 3.2 Satu broker, dua kelas trafik

Broker awal tetap berada di Ubuntu Ground. Raspberry menjalankan satu agent dengan satu koneksi control dan, untuk isolasi trafik besar, satu koneksi bulk yang berbeda. Dua koneksi client bukan berarti dua broker. [S10, bagian 2; usulan perluasan]

| Jalur | Fungsi | Kebijakan |
|---|---|---|
| Control client | DoA ringkas, health, state, config, command, ACK | Selalu diprioritaskan; paket dibatasi |
| Bulk client | Angular chunks dan snapshot diagnostik terpilih | Dapat dipause, dibuang, atau reconnect sendiri |
| Local API | Dashboard Raspberry 5 inci | Loopback; tidak melewati T900 |
| Ground API | Frontend Ground | Loopback/same-origin sebagaimana workflow sekarang |

Pemisahan koneksi bulk mengurangi risiko array besar menghalangi ACK pada stream TCP yang sama. Ini **bukan jaminan prioritas radio**: keduanya tetap memakai PPP, UART, dan link RF yang sama. Scheduling, ukuran paket, dan pembatasan queue tetap diperlukan. TCP sendiri menyediakan byte stream berurutan dan retransmisi; mengganti QoS MQTT tidak menghilangkan perilaku itu. [E05]

### 3.3 Batas tanggung jawab komponen

| Komponen | Tanggung jawab | Tidak boleh melakukan |
|---|---|---|
| SDR engine | DAQ, DSP, output native | Bergantung pada browser baru atau broker Ground |
| Source adapter | Membaca file lokal, parse, korelasi, provenance | Mengubah data native, memalsukan fix atau quality |
| State store | Snapshot internal yang konsisten | Menganggap nilai terakhir selalu fresh |
| Scheduler | Rate, prioritas, drop, budget | Menimbun history telemetry offline |
| Command manager | Validasi, journal, dedup, hasil | Menjalankan string shell dari payload |
| Restricted helper | Operasi lokal yang membutuhkan privilege | Membuka API root umum ke jaringan |
| Node display | Menampilkan status dan alasan | Menjadi satu-satunya pengirim heartbeat |
| Ground backend | Decode, store, command authority, API | Menganggap broker ACK sebagai tindakan berhasil |

### 3.4 Jangan menghapus GUI engine lama secara sembarangan

Pada arsitektur upstream yang dikaji di project, `WebInterface` membuat receiver dan menjalankan `SignalProcessor`. Karena itu "GUI lama tidak dipakai" tidak otomatis berarti proses `_ui/_web_interface/app.py` aman dimatikan. Browser kiosk baru hanya mengganti **tampilan operator**, bukan otomatis mengganti proses engine yang saat ini terikat dengan WebInterface. Headless refactor memerlukan audit source terpisah. [S11, bagian 2.1; S07, bagian 11]

<a id="bagian-4"></a>
## 4. Data native, normalisasi, dan arti full DoA

### 4.1 Sumber data yang diketahui

| Sumber | Isi | Pemakaian rancangan |
|---|---|---|
| `DOA_value.html` | CSV 377 field per record; metadata dan angular 360 | Sumber utama kandidat untuk snapshot grafik |
| `status.json` | Timestamp, DAQ, sync jika tersedia, drop, uptime, GPS | Health source, bukan satu-satunya bukti process health |
| `settings.json` | Konfigurasi runtime dan kemungkinan field sensitif | Dibaca terkontrol; hanya allowlist yang dilaporkan |
| `doa.xml` | Alternatif DoA, SNR, processing time, transformasi berbeda | Cross-check atau pelengkap hanya jika korelasi terbukti |
| Process/system metrics | CPU, suhu, RAM, disk, PPP, service | Dikumpulkan agent/helper terpisah dari SDR output |

Ukuran arsip `DOA_value.html` 2.286 byte adalah **ukuran snapshot**, bukan batas tetap semua runtime/VFO. File dapat ditulis non-atomic dan dapat tetap menyimpan hasil lama ketika tidak ada detection baru. [S06, bagian 3, 6-8; S07, bagian 4]

### 4.2 Full angular tidak sama dengan file asli byte-for-byte

Ada tiga tingkat kebutuhan:

| Tingkat | Hasil di Ground | Trade-off |
|---|---|---|
| Full angular Q16 | 360 sampel tetap ada, nilai dibulatkan ke resolusi 0,01 satuan native | Presisi cukup terkontrol untuk grafik; bukan lossless file |
| Full angular U8 | 360 sampel tetap ada, skala min-max dikirim | Lebih kecil; resolusi amplitudo berubah per frame |
| Native snapshot | Salinan record CSV asli beserta seluruh metadata/reserved | Lossless terhadap file, tetapi lebih besar dan hanya on-demand |

Keputusan default dokumen ini adalah **Q16 untuk grafik**. Tidak ada pengurangan jumlah sudut dari 360 menjadi 90. Pengurangan dilakukan pada ukuran representasi amplitudo dan frekuensi update, bukan jumlah arah.

Sebagian metadata yang jarang berubah, seperti station ID dan geometri, dikirim pada state/config. Navigasi dikirim terpisah ketika sumbernya valid. Karena itu stream grafik default bukan salinan tekstual seluruh 377 kolom dalam setiap pesan. Ground yang perlu audit persis seluruh record dapat meminta native snapshot terpisah.

### 4.3 Sudut dan quality tidak boleh ditafsirkan sembarangan

Studi project mencatat `csv_doa = 360 - theta_0`, sedangkan XML memakai `theta_0`. Vector angular mempunyai index dan konvensi sendiri. Pemetaan panah dan kurva harus diuji dengan sumber RF terkontrol; jangan hanya membalik salah satu nilai sampai tampilannya terlihat cocok. [S07, bagian 6-7; S11, bagian 7.2]

Native confidence dideskripsikan sebagai PAPR-like dB pada beberapa bagian sumber. Ada bagian historis yang memakai istilah percent, sehingga kontrak harus menganggap pemetaan kualitas **belum tuntas**. Rancangan ini memakai `confidence_native_db`; UI tidak menulis "92% akurat" dari angka native. Angka `0.92` pada contoh chat terdahulu bukan hasil kalibrasi yang dapat langsung dipakai. [S06, bagian 7; S07, bagian 7.3; S11, bagian 4.3 dan 7.3]

Vector CSV yang didokumentasikan telah berupa nilai relatif hasil transformasi source. Jangan menjalankan `abs()` atau `log10()` kedua untuk menciptakan data baru. Renderer boleh menggeser radius visual agar non-negatif, tetapi nilai asli tetap disimpan dan label plot menjelaskan transformasi visual tersebut. [S06, bagian 6]

### 4.4 Aturan source adapter

Adapter membaca snapshot utuh dengan batas ukuran, memvalidasi jumlah field, nilai finite, timestamp, frekuensi, VFO, dan array. Bila parsing gagal karena file sedang ditulis, lakukan retry terbatas pada tick berikutnya; jangan block worker command.

Satu snapshot yang diterima mendapatkan `sid` dan `q` dari agent. Keduanya harus dipakai bersama timestamp sumber, VFO, frekuensi, dan config revision. `q` adalah nomor snapshot adapter, **bukan klaim nomor frame asli DAQ** jika sumber tidak menyediakannya.

Tidak boleh menggabungkan SNR XML dengan array CSV hanya karena keduanya tersedia. Pelengkap hanya valid jika timestamp, channel, unit, dan aturan korelasi sudah lulus. Bila tidak, kirim `null` atau hilangkan field opsional tersebut, bukan angka tebakan.

MVP memakai satu output VFO. Mendukung 16 slot settings tidak berarti 16 VFO boleh sekaligus mengirim grafik pada budget ini. Bila sumber berisi beberapa baris, parse setiap record dengan identitas yang jelas, pilih output yang disepakati, dan tolak pemilihan ambigu.

### 4.5 Gate LIVE dan gate diagnostic

**Gate LIVE:** source valid, timestamp dapat dipercaya, DAQ sehat, arah/quality punya definisi, dan data masih dalam jendela freshness.

**Gate diagnostic:** file boleh ditampilkan meskipun belum authoritative, tetapi wajib diberi label `UNVERIFIED`, `STALE`, atau `DAQ DEGRADED`. Diagnostic tidak boleh masuk topic LIVE yang sama tanpa penanda kualitas yang jelas.

RDF stop harus menghasilkan status `STOPPED`, bukan mempublikasikan ulang CSV terakhir sebagai pengukuran baru. DAQ sehat tanpa sinyal di atas squelch boleh menghasilkan `NO_DETECTION`; itu berbeda dari engine crash.

<a id="bagian-5"></a>
## 5. Kontrak data, identitas, dan versi

### 5.1 Namespace yang diusulkan

```text
sdr/v2/uav-01/telemetry/doa
sdr/v2/uav-01/telemetry/health
sdr/v2/uav-01/telemetry/health/detail
sdr/v2/uav-01/telemetry/nav
sdr/v2/uav-01/telemetry/angular
sdr/v2/uav-01/state
sdr/v2/uav-01/availability
sdr/v2/uav-01/config/reported
sdr/v2/uav-01/capabilities
sdr/v2/uav-01/ground/receipt
sdr/v2/uav-01/cmd/config/get
sdr/v2/uav-01/cmd/config/patch
sdr/v2/uav-01/cmd/processing/set
sdr/v2/uav-01/cmd/service/restart
sdr/v2/uav-01/cmd/system/reboot/prepare
sdr/v2/uav-01/cmd/system/reboot/execute
sdr/v2/uav-01/cmd/operation/get
sdr/v2/uav-01/cmd/stream/set
sdr/v2/uav-01/cmd/diagnostic/get
sdr/v2/uav-01/ack/config
sdr/v2/uav-01/ack/operation
sdr/v2/uav-01/diagnostic/result
```

`uav-01` adalah logical node ID usulan dari namespace project, bukan hardware serial. Inventory final harus menetapkan ID stabil dan tidak memakai hostname sebagai satu-satunya identitas.

Frontend lama dan monitor v1 tidak boleh otomatis menganggap payload v2 kompatibel. Ground backend dapat memiliki decoder v1 dan v2 terpisah; satu stream node tidak boleh diterima dua kali sebagai data independen.

### 5.2 Identitas dan korelasi

| Field | Makna |
|---|---|
| `boot_id` | ID boot OS lengkap; berbeda setelah reboot OS, bukan setelah refresh UI |
| `sid` | Alias session stream 32-bit, delapan digit hex pada JSON; berubah ketika agent membuat stream session baru |
| `agent_instance_id` | ID lengkap instance agent, dilaporkan di capabilities/state detail |
| `q` | Sequence snapshot 32-bit dalam session; wrap harus ditangani atau session dirotasi |
| `t` | Timestamp sumber dalam epoch millisecond; bukan waktu saat Ground menerima |
| `rev` | Config revision efektif/terverifikasi untuk record, atau unknown |
| `id` | Command ID unik yang tetap dipakai saat memeriksa ulang hasil |

`sid` ringkas bukan token keamanan dan bukan UUID yang dijamin bebas tabrakan. Ground memetakan `sid` ke `boot_id` dan instance lengkap saat handshake. Jika alias bertabrakan atau belum dipetakan, telemetry ditahan sebagai `UNVERIFIED` dan dilakukan refresh state. Jangan menerima command hanya karena mengetahui `sid`.

### 5.3 Bentuk ringkas di radio, bentuk jelas di API

Payload radio memakai key singkat untuk hemat byte. Ground backend dan local API mengekspos nama yang mudah dibaca. Pemendekan key tidak boleh menghilangkan definisi unit.

| Wire | Canonical API | Catatan |
|---|---|---|
| `a` | `relative_doa_deg` | Hanya authoritative setelah angle gate; convention ada di capabilities |
| `c` | `confidence_native_db` | Bukan probability |
| `p` | `power_native_db` | Bukan otomatis calibrated dBm atau RSSI link T900 |
| `f` | `frequency_hz` | Frekuensi VFO yang menghasilkan record |
| `ok` | `doa_valid` | 1 valid, 0 invalid pada schema ini |
| `run` | `processing_state` | 0 stopped, 1 running, 2 starting, 3 stopping, 4 error, 5 restarting, 255 unknown |
| `daq` | `daq_health` | 0 unhealthy, 1 healthy, 2 unknown |
| `age` | `doa_age_at_sample_ms` | Umur pada Raspberry; bukan total latency ke Ground |
| `clk` | `clock_state` | 0 untrusted, 1 synced, 2 holdover |

Contoh ukuran pasti untuk budget tersedia di bagian 9. Saat menambahkan field, angka bandwidth harus dihitung ulang. Field optional seperti SNR boleh ditambahkan setelah sumbernya tervalidasi, bukan diwajibkan dengan nilai palsu.

<a id="bagian-6"></a>
## 6. Full angular binary dan pengiriman potongan

### 6.1 Format frame Q16

Usulan frame terdiri dari **header 48 byte + 360 x int16 = 768 byte**. Semua multi-byte menggunakan little-endian. Format packing header Python adalah:

```python
HEADER_FORMAT = '<4sBBHIIQIIBBHffHh'  # 48 bytes
```

| Offset | Byte | Field | Makna |
|---:|---:|---|---|
| 0 | 4 | magic | ASCII `RDF2` |
| 4 | 1 | version | 2 |
| 5 | 1 | encoding | 1 Q16, 2 U8, 3 float32 |
| 6 | 2 | flags | Validity dan provenance bitmask versi ini |
| 8 | 4 | sid | Session alias uint32 |
| 12 | 4 | q | Sequence snapshot uint32 |
| 16 | 8 | t | Timestamp sumber uint64 |
| 24 | 4 | f | Frekuensi Hz uint32 |
| 28 | 4 | rev | Config revision uint32; `0xffffffff` unknown |
| 32 | 1 | vfo | Output VFO index |
| 33 | 1 | convention | ID konvensi sumbu yang terdokumentasi |
| 34 | 2 | count | Harus 360 pada MVP |
| 36 | 4 | scale | Float32 langkah dekode amplitudo |
| 40 | 4 | offset | Float32 offset amplitudo |
| 44 | 2 | doa_raw_cdeg | DoA native CSV x 100; 65535 jika unavailable |
| 46 | 2 | confidence_centi | Confidence native x 100; -32768 unavailable |
| 48 | 720 | samples | 360 signed int16 |

`flags` yang diusulkan: bit 0 source parsed, bit 1 source fresh, bit 2 DAQ healthy, bit 3 convention verified, bit 4 config attribution verified; bit lain reserved. Frame LIVE mensyaratkan flag yang sesuai. Semua flag tidak mengubah kewajiban Ground memeriksa umur frame setelah tiba.

Convention 0 berarti unknown dan hanya diagnostic; convention 1 berarti index native CSV yang dipertahankan; ID canonical terpisah baru diaktifkan setelah uji orientasi. Jangan memberikan label true-north hanya karena renderer memakai 0 di bagian atas.

### 6.2 Dekode dan error kuantisasi

Untuk Q16:

```text
scale = 0.01
value[i] = offset + scale * sample[i]
offset = 0 pada profil Q16 awal
```

Pembulatan terdekat menghasilkan error amplitudo ideal sekitar maksimum 0,005 satuan native, ditambah error sangat kecil representasi float pada scale. Nilai int16 `-32768` dicadangkan sebagai invalid. Rentang Q16 yang valid dengan offset nol adalah -327,67 sampai 327,67. Nilai di luar rentang harus ditolak atau memakai encoding lain melalui capability negotiation; **jangan clip diam-diam**.

Kode referensi dan self-test tersedia pada lampiran. Kuantisasi amplitudo tidak menambah akurasi RDF dan tidak mengubah resolusi sumbu sudut: tetap satu sampel per derajat sesuai native array.

### 6.3 Alternatif U8

U8 mempertahankan 360 arah, tetapi amplitudo dipetakan dengan:

```text
scale = (max_value - min_value) / 255
offset = min_value
q[i] = round((value[i] - offset) / scale)
reconstructed[i] = offset + scale * q[i]
```

Bila max=min, gunakan scale=0 dan seluruh sample=0; decoder menghasilkan offset untuk semua titik. Contoh rentang 40 dB memberi resolusi 40/255 = sekitar 0,157 dB dan error pembulatan ideal sekitar 0,078 dB. Ini **estimasi matematika**, bukan validasi bahwa U8 selalu memadai untuk setiap kurva native.

Ukuran U8: 48+360=408 byte. U8 sesuai opsi grafik lebih sering dengan amplitudo lebih kasar, setelah pengguna membandingkan grafik terhadap sumber lokal.

Float32 memberi 48+1440=1488 byte. Ia mempertahankan semua arah tetapi bukan salinan tekstual CSV yang byte-identik, dan mungkin perlu segmentasi TCP. Gzip/zstd pada CSV dapat membantu pada sebagian data, tetapi rasio kompresi tidak dianggap tetap tanpa corpus aktual.

### 6.4 Mengapa binary asli, bukan JSON array atau Base64

MQTT membawa payload bytes; tidak perlu memaksa semua pesan menjadi JSON. [E01; E02] Base64 pada 720 byte sample saja menjadi 960 karakter sebelum metadata, sehingga ada overhead sekitar 33%. Menulis int16 sebagai angka-angka JSON juga mengembalikan sebagian pemborosan representasi ASCII.

Ground Python mendekode binary menjadi struktur biasa untuk frontend. Dengan demikian React tetap bisa menerima array numerik melalui API lokal; hanya jalur T900 yang memakai format hemat.

### 6.5 Pemotongan frame untuk menjaga respons command

Profil default Q16 membagi frame 768 byte menjadi **2 potongan x 384 byte**. Setiap potongan mempunyai header 12 byte:

```python
CHUNK_FORMAT = '<IIBBH'  # sid, q, index, count, total_frame_bytes
```

Ukuran publish payload per potongan: 384+12=396 byte. Total application payload satu frame: 792 byte. Potongan pertama memuat awal header frame RDF2, potongan kedua melanjutkan bytes; Ground baru merender setelah keduanya lengkap.

Gunakan topic `telemetry/angular` untuk envelope chunk yang selalu sama. Q16 default memiliki 2 chunk; U8 memiliki 1 chunk berisi frame 408 byte, sehingga payload MQTT-nya 420 byte setelah envelope. Dengan format ini, receiver tidak perlu menebak apakah payload adalah raw frame atau chunk. Encoding dibaca dari header frame setelah assembly, dan harus diizinkan oleh capability session. Perubahan mode/rate tetap dilaporkan lewat state.

Aturan assembly: key `(node, boot_id, sid, q)`, count dan total panjang harus cocok, maksimum dua frame belum lengkap, timeout assembly awal 3 detik, dan duplikat chunk hanya diterima jika bytes sama. Frame baru tidak boleh dicampur dengan chunk frame lama. Setelah agent/bulk reconnect, reset assembly yang tidak lengkap.

Potongan bukan cara menambah bandwidth. Dua potongan menambah header dan ACK dibanding satu publish. Manfaatnya adalah menyediakan kesempatan scheduler untuk mengirim status/ACK di antara potongan. Bytes yang sudah masuk buffer TCP/radio tidak dapat ditarik kembali.

### 6.6 Kebijakan visual di Ground

Panah DoA terbaru dan kurva angular mempunyai timestamp terpisah. Jika panah berubah lebih cepat, jangan menggambarkannya seolah-olah berasal dari kurva terakhir. Tampilkan misalnya `DoA age: 0.7 s` dan `Curve age: 3.4 s`.

Peak kurva dihitung dari frame angular yang sama; marker DoA native frame itu tersedia di header. Current DoA stream cepat dapat menjadi marker kedua yang jelas berbeda. Bila config/frequency berubah, grafik lama ditandai sebagai previous configuration atau disembunyikan sampai frame baru tersedia.

Frontend boleh menggambar ulang dengan frame rate lokal yang halus, tetapi tidak menciptakan pengukuran baru. Interpolasi visual harus diberi arti animasi, bukan peningkatan sample rate telemetry.

<a id="bagian-7"></a>
## 7. MQTT, koneksi, QoS, dan delivery semantics

### 7.1 Versi dan perilaku yang dipilih

Usulan utama adalah MQTT 5 jika versi Mosquitto dan library yang benar-benar terpasang mendukungnya. MQTT 5 menyediakan message expiry, session expiry, dan properti yang membantu protokol aplikasi. Namun expiry broker tidak menggantikan pengecekan deadline oleh command manager. Kompatibilitas MQTT 3.1.1 boleh dipertahankan dengan expiry pada payload dan validasi aplikasi. [E01; E02]

QoS 0 dipilih untuk telemetry yang cepat berubah; QoS 1 untuk command, hasil, dan state terpilih. QoS 1 dapat menghasilkan pengiriman ulang, sehingga command harus idempotent/deduplicated. PUBACK berarti penerimaan pada tingkat MQTT antar-peer, bukan bukti perangkat telah menerapkan tindakan. [E02]

### 7.2 Matriks trafik

| Kelompok | Arah melewati RF | QoS | Retain | Rate awal / trigger |
|---|---|---:|---|---|
| DoA ringkas | UAV -> Ground | 0 | Tidak | 1 Hz default, hanya snapshot baru valid |
| Health ringkas | UAV -> Ground | 0 | Tidak | 1 Hz, tetap hidup saat RDF stop |
| Health detail | UAV -> Ground | 0 | Tidak | 0,1 Hz; alarm penting event-driven |
| Angular chunks | UAV -> Ground | 0 | Tidak | 1 frame/4 detik, 2 chunk per frame |
| Nav | UAV -> Ground | 0 | Tidak | OFF sampai sumber valid; profil bergerak dihitung terpisah |
| State | UAV -> Ground | 1 | Ya | Boot, perubahan, reconnect; refresh 60 detik |
| Config reported | UAV -> Ground | 1 | Ya | Perubahan, permintaan, reconnect |
| Availability | UAV -> Ground/broker | 1 | Ya | Connect, planned disconnect, LWT |
| Capabilities | UAV -> Ground | 1 | Ya | Boot atau perubahan versi/capability |
| Ground receipt | Ground -> UAV | 0 | Tidak | 1 per 5 detik, rangkum sequence yang benar-benar diterima backend |
| Config/lifecycle command | Ground -> UAV | 1 | Tidak | On-demand; satu operasi mutating aktif |
| ACK/result aplikasi | UAV -> Ground | 1 | Tidak | Accepted dan terminal; progress dibatasi |
| Diagnostic snapshot | UAV -> Ground | Sesuai request | Tidak | Manual, size cap, dipace, bukan history otomatis |

Retained state adalah **last known state**, bukan heartbeat. Timestamp dan session tetap harus diperiksa. Tidak ada retained command dan tidak ada retained perintah reboot. [E01; usulan policy]

### 7.3 Session dan reconnect

Profil control awal menggunakan session expiry nol dan subscribe ulang setelah reconnect. Hal ini menghindari desain yang bergantung pada broker menyimpan perintah selama node offline. Journal hasil command berada di aplikasi, sehingga Ground dapat meminta status berdasarkan ID setelah reconnect.

Client bulk juga tidak menyimpan backlog offline. Pada reconnect, kirim state/capability/config ringkas dahulu, kemudian health, baru DoA, dan paling akhir grafik. Setiap komponen mempunyai reconnect backoff dengan jitter; jangan semua client membuat koneksi TLS baru berulang-ulang tiap beberapa ratus millisecond.

Library client dapat memiliki outgoing queue sendiri. Mengatur queue agent saja tidak cukup. Paho mendokumentasikan pembatas queued/inflight messages dan perilaku reconnect; wrapper harus menguji bahwa frame lama tidak dikirim kembali sebagai LIVE. [E04]

Untuk command QoS 1 yang outcome-nya belum diketahui, jangan menghapus journal hanya karena koneksi putus. Untuk bulk yang macet, koneksi bulk boleh dibuang dan dibuat ulang setelah backoff, tanpa memutus control client. Frame yang belum lengkap dibuang di Ground.

### 7.4 Keepalive, LWT, dan aplikasi Ground

Keepalive awal control/bulk diusulkan 15 detik. Ini bukan frekuensi update health dan bukan janji deteksi offline tepat 15 detik. LWT dipakai untuk memberi sinyal kehilangan koneksi broker, sementara Ground memakai timer lokal untuk menandai health terlambat lebih cepat.

Ketika RDF stop, control MQTT tetap mengirim health. Ketika Raspberry reboot, ada interval di mana tidak mungkin mengirim health; UI harus menampilkan `REBOOTING / WAITING FOR RETURN`, bukan membuat heartbeat palsu.

Ground receipt mengkonfirmasi bahwa **backend Ground** menerima/mendekode data, bukan bahwa browser operator sudah menggambarnya. Local display menuliskan `GROUND BACKEND RX`, bukan klaim "operator melihat". Browser health dapat dimonitor lokal di Ubuntu tanpa tambahan trafik RF.

### 7.5 Broker dan keamanan transport

Broker harus memakai authentication dan ACL dengan arah publish/subscribe yang spesifik. Terapkan TLS dengan verifikasi peer; jangan menonaktifkan certificate validation untuk membuat koneksi tampak berhasil. IP PPP dan pairing radio bukan otorisasi aplikasi. [E03; E09]

Listener tidak boleh dibuka ke internet atau semua LAN tanpa kebutuhan. Ground API tetap loopback. Listener radio harus dapat start dengan benar ketika IP PPP belum ada; binding langsung ke `10.90.0.1` memerlukan strategi lifecycle yang diuji, bukan hanya berharap alamat sudah tersedia saat boot. Alternatif binding lebih luas hanya boleh digunakan dengan firewall yang ketat.

Konfigurasi Mosquitto harus mengikuti versi terpasang. Dokumentasi upstream menyebut perubahan model pengaturan per-listener pada versi 2.1; karena versi deployment belum diketahui, dokumen ini tidak memberikan satu konfigurasi password/ACL yang diklaim universal untuk semua versi. [E10]

<a id="bagian-8"></a>
## 8. Scheduler, prioritas, dan perlindungan latency

### 8.1 Prioritas kirim

| Prioritas | Isi | Perilaku |
|---:|---|---|
| P0 | Accepted/result command, alarm critical, perubahan lifecycle | Dikirim secepat budget memungkinkan; rate-limit anti-spam |
| P1 | Health ringkas, perubahan state, config result | Tidak boleh kelaparan oleh grafik |
| P2 | DoA ringkas, navigation valid | Latest-value, turunkan rate jika perlu |
| P3 | Angular 360 | Pause saat command/transisi atau link menurun |
| P4 | Detail tambahan, logs/snapshot | Manual dan paling mudah dibatalkan |

Ini adalah prioritas **aplikasi** yang harus diimplementasikan. MQTT broker tidak otomatis menjadikan sebuah topic lebih prioritas hanya karena namanya `cmd` atau QoS-nya 1.

### 8.2 Queue policy

DoA, health, dan nav memakai slot latest-value per jenis, bukan FIFO sejarah. Angular dibatasi satu frame aktif dan satu calon frame terbaru. Ketika frame baru menggantikan frame menunggu, tambah counter local drop. Jangan menyebut local intentional drop sebagai packet loss radio.

Command mutating: maksimal satu operation aktif per node. Command baru yang konflik mendapat `BUSY` beserta operation ID aktif. Refresh/status dapat dijadwalkan tanpa menambah operasi mutating. Queue ACK/result dibatasi ukuran dan waktu, tetapi hasil terminal persisten di journal sehingga dapat di-query ulang.

Jangan mengirim semua chunks sekaligus ke `publish()` lalu mengklaim sudah diprioritaskan. Pacer hanya boleh menyerahkan potongan berikutnya jika control queue tidak pending dan backlog socket masih dalam batas. Completion QoS 0 di library bukan bukti data sudah diterima Ground; ground receipt membantu observasi end-to-end.

### 8.3 Pacing dan budget

Usulan scheduler memakai token bucket untuk jumlah byte yang diestimasi setelah MQTT framing, ditambah biaya model transport. Bucket bulk maksimal satu chunk, bukan beberapa detik bandwidth. Cadangan control tidak boleh dipakai penuh oleh bulk meskipun tidak ada command saat itu.

Baseline aggregate equivalent yang dibidik sekitar 8-10 kbit/s pada model bagian 9, tetapi batas produksi harus berasal dari hasil pengukuran. Batas dua arah dihitung konservatif secara agregat karena throughput simultan dua arah link pengguna belum diuji. Radio dapat mempunyai perilaku duplex/airtime yang berbeda dari model; jangan mengklaim 15 kbit/s tersedia independen di setiap arah.

Saat command masuk, berhenti menjadwalkan chunk baru. Paket yang sudah berada di buffer tetap dapat menambah delay. Sesudah operasi selesai, health kembali stabil, dan queue sudah drain, baru grafik dilanjutkan.

### 8.4 Adaptasi rate yang diusulkan

| Mode | DoA | Health | Angular | Pemicu |
|---|---:|---:|---:|---|
| STARTUP | 0 sampai gate lulus | 1 Hz | OFF | Boot/reconnect/config verify |
| BALANCED | 1 Hz | 1 Hz | Q16 0,25 Hz | Default awal |
| FAST | 2 Hz | 1 Hz | Q16 0,25 Hz | Hanya setelah uji margin dan latency |
| GRAPH | 1 Hz | 1 Hz | U8 0,5 Hz atau Q16 0,5 Hz | Profil dipilih dan diuji; bukan default otomatis |
| CONTROL | 1 Hz jika valid | 1 Hz | OFF | Command/transisi/link menurun |
| DEGRADED | 0,5-1 Hz jika valid | 1 Hz awal | OFF | Backlog/age/receipt memburuk |
| NO LINK | Tidak dikirim | Tidak dapat dikirim | OFF | Local processing dan panel tetap berjalan |

Contoh threshold awal, bukan hasil uji: hentikan bulk jika backlog control diperkirakan >500 ms, receipt Ground hilang >10 detik, atau timer health Ground melewati 3 detik. Pemulihan rate harus memakai hysteresis, misalnya 20 detik keadaan stabil sebelum naik satu tingkat, agar tidak bolak-balik setiap detik.

Pada kualitas RF sangat rendah, health 1 Hz pun mungkin tidak dapat dipertahankan. Prioritas berarti usaha terbaik, bukan jaminan komunikasi pada link yang tidak mempunyai kapasitas. UI Ground harus menampilkan kehilangan link secara jujur.

### 8.5 Perbedaan rata-rata dan blocking sesaat

File 2,3 KB setiap 5 detik mungkin terlihat kecil pada rata-rata, tetapi satu kiriman besar tetap mengisi link selama suatu interval. Demikian juga config penuh dan log. Karena itu budget B/s saja tidak cukup; ukur waktu tunggu ACK, queue depth, dan umur data ketika burst terjadi.

Sebagai ilustrasi matematika, 768 byte membutuhkan 0,410 detik pada service rate efektif 15.000 bit/s **sebelum overhead**, sedangkan 396 byte membutuhkan 0,211 detik. Angka itu bukan latency aktual T900; processing, buffer, ACK, dan retransmission menambah waktu.

<a id="bagian-9"></a>
## 9. Perkiraan bandwidth yang dapat direproduksi

### 9.1 Bukti awal dan satuan

Dokumen benchmark mencatat tes UDP reverse 256 byte selama 30 detik per rate: 18 kbit/s tanpa loss pada kondisi uji, dan 20 kbit/s mulai 11% loss. Target continuous project adalah 8-10 kbit/s, bukan penggunaan penuh clean ceiling. [S05, bagian 3-7]

Angka 18 kbit/s tersebut adalah **UDP application payload throughput yang dilaporkan pengujian**, bukan jumlah semua bit di kabel UART atau guarantee untuk MQTT/TLS. Tidak benar membandingkan hasil itu dengan model on-wire tanpa menyebut layer pengukurannya.

```text
15 kbit/s = 15.000 bit/s = 1.875 byte/s
bukan 15 kilobyte/s

57.600 baud dengan framing 8N1:
57.600 / 10 = 5.760 byte/s, sebelum overhead protokol lainnya
```

Istilah aggregate equivalent pada tabel berikut berarti jumlah bytes model dua arah dikali 8. Untuk physical UART dengan start/stop 8N1, line bits memerlukan faktor 10/8 atas bytes serial yang benar-benar dikirim. Jangan mengalikan 10/8 lagi pada angka benchmark UDP dan lalu menyebutnya throughput aplikasi.

### 9.2 Asumsi model

Model berikut sengaja eksplisit agar tidak memberikan kesan presisi pengukuran palsu:

- Payload contoh JSON diserialisasi compact UTF-8/ASCII, tanpa indentasi.
- MQTT 5 dengan topic `sdr/v2/uav-01/...`, tanpa topic alias, tanpa user property tambahan.
- PUBLISH telemetry mempunyai Message Expiry property 5 byte ditambah byte property length. State/config pada contoh transaction tidak memakai expiry MQTT.
- IPv4+TCP diasumsikan 52 byte per segment, termasuk asumsi TCP timestamps 12 byte.
- PPP framing allowance 8 byte per packet; nilai aktual berubah dengan negosiasi dan framing.
- TLS steady-state diasumsikan satu record per publish, tambahan 22 byte: 5-byte record header, 1-byte inner content type, dan 16-byte tag, tanpa padding. Ini model cipher/record tertentu, bukan semua TLS deployment. [E08]
- Model mengenakan satu TCP ACK terpisah per segment. Tidak mengambil keuntungan delayed ACK, piggyback, atau coalescing.
- QoS 1 mempunyai PUBACK transport dan TCP ACK terkait yang juga dimodelkan; itu berbeda dari ACK aplikasi.
- MSS model 1.448 byte. Pesan yang lebih besar menghitung tambahan segment dan ACK.
- Allowance PPP control dua arah adalah 24 byte/s total untuk baseline echo yang digunakan project; bukan hasil capture baru.
- Kolom perencanaan menambahkan 15% ilustratif untuk escaping/retry/variasi kecil. Faktor ini **bukan upper bound**, terutama jika RF buruk atau ACCM/escaping besar. [E06]
- TLS handshake, reconnect burst, log download, full capability besar, dan command burst tidak masuk steady-state kecuali dinyatakan.

Default ACCM/framing, jenis payload, TLS ciphertext, dan radio internal dapat membuat overhead berbeda. Karena itu tabel bukan bukti bahwa link lapangan mampu mempertahankan semua angka di dalamnya.

### 9.3 Ukuran payload contoh yang benar-benar dihitung

| Payload sintetis | Byte JSON compact |
|---|---:|
| DoA ringkas | 109 |
| Health ringkas | 113 |
| Health detail | 197 |
| Navigation opsional | 98 |
| Ground receipt | 60 |
| State | 158 |
| Command config | 250 |
| ACK accepted | 89 |
| ACK applied | 119 |
| Config reported | 214 |

Contoh DoA 109 byte memakai key pendek. Contoh yang memakai `relative_doa_deg`, `frequency_hz`, `processing_ms`, dan nama panjang lain akan lebih besar; jangan tetap menyebutnya 109 atau 150 byte tanpa menghitung ulang.

**DoA ringkas - 109 byte compact:**

```json
{"v":2,"sid":"7a8b9c0d","q":1245,"t":1790668800123,"f":433920000,"a":137.4,"c":8.27,"p":-54.2,"rev":7,"ok":1}
```

**Health ringkas - 113 byte compact:**

```json
{"v":2,"sid":"7a8b9c0d","q":86,"t":1790668800123,"run":1,"daq":1,"drop":12,"age":280,"temp":61.4,"clk":1,"rev":7}
```

**Health detail - 197 byte compact:**

```json
{"v":2,"sid":"7a8b9c0d","t":1790668800123,"usb":5,"sync":[1,1,1],"ovr":0,"cpu":38.2,"mem":54.1,"disk":67.0,"throt":0,"uv":0,"tx":1012,"rx":219,"qd":0,"adrop":0,"clock_err_ms":80,"gps":0,"err":null}
```

**Navigation opsional - 98 byte compact:**

```json
{"v":2,"sid":"7a8b9c0d","t":1790668800123,"lat":-6.9147,"lon":107.6098,"hdg":121.5,"src":1,"ok":1}
```

**Ground receipt - 60 byte compact:**

```json
{"v":2,"sid":"7a8b9c0d","dq":1245,"hq":86,"aq":1238,"rev":7}
```

**State - 158 byte compact:**

```json
{"v":2,"sid":"7a8b9c0d","boot":"10aa2345-6789-4abc-9def-1234567890ab","t":1790668800123,"run":"RUNNING","daq":true,"cfg":7,"control":"READY","clock":"SYNCED"}
```

Semua nilai di atas adalah **data sintetis untuk model**, bukan hasil pengukuran sekarang. `ok=1`, `daq=1`, koordinat, suhu, dan nilai quality pada contoh tidak menyatakan keadaan perangkat pengguna.

### 9.4 Profil steady-state

Semua profil di bawah sudah memasukkan health ringkas 1 Hz, detail 0,1 Hz, state 1/60 Hz, Ground receipt 0,2 Hz, dan allowance PPP control. Nav OFF kecuali ditulis eksplisit. Payload angular Q16 memakai 2 chunks kecuali profil lain menyatakan berbeda.

| Profil | DoA Hz | Nav Hz | Grafik Hz | Bentuk angular |
|---|---:|---:|---:|---|
| P0 CONTROL | 1 | 0 | 0 | OFF |
| P1 BALANCED - default | 1 | 0 | 0.25 | Q16, 2 chunk |
| P1 NAV 1Hz | 1 | 1 | 0.125 | Q16, 2 chunk |
| P2 FAST q16 | 2 | 0 | 0.25 | Q16, 2 chunk |
| P3 GRAPH q16 | 1 | 0 | 0.5 | Q16, 2 chunk |
| P4 GRAPH u8 | 1 | 0 | 0.5 | U8, 1 chunk |
| P5 FULL all | 2 | 1 | 0.5 | Q16, 2 chunk |
| P6 RAW legacy | 2 | 0 | 0.2 | CSV raw comparator |
| P7 FRAG4 | 1 | 0 | 0.25 | Q16, 4 chunk |

| Profil | Payload kbit/s | MQTT kbit/s | UAV ke Ground model | Ground ke UAV model | Total model | Total +15% |
|---|---:|---:|---:|---:|---:|---:|
| P0 CONTROL | 2.05 | 2.79 | 4.22 | 1.41 | 5.63 | 6.47 |
| P1 BALANCED - default | 3.63 | 4.54 | 6.30 | 1.65 | 7.95 | 9.14 |
| P1 NAV 1Hz | 3.63 | 4.75 | 7.00 | 2.01 | 9.01 | 10.36 |
| P2 FAST q16 | 4.51 | 5.71 | 8.13 | 2.13 | 10.26 | 11.80 |
| P3 GRAPH q16 | 5.22 | 6.29 | 8.38 | 1.89 | 10.27 | 11.81 |
| P4 GRAPH u8 | 3.73 | 4.63 | 6.39 | 1.65 | 8.04 | 9.25 |
| P5 FULL all | 6.87 | 8.55 | 11.95 | 2.85 | 14.80 | 17.03 |
| P6 RAW legacy | 6.58 | 7.69 | 10.00 | 2.08 | 12.08 | 13.90 |
| P7 FRAG4 | 3.68 | 4.75 | 6.84 | 1.89 | 8.73 | 10.04 |

Semua angka pada tabel kedua dalam kbit/s. Total +15% adalah planning allowance, bukan hasil capture atau batas atas. P6 adalah pembanding legacy berdasarkan ukuran raw; payload raw tersebut tidak boleh dimasukkan ke decoder binary v2 tanpa jalur diagnostic terpisah.

**Rekomendasi default: P1 BALANCED.** Perkiraan aggregate equivalent setelah allowance adalah sekitar **9,14 kbit/s**, belum termasuk command saat operator menekan tombol. Ini lebih konservatif daripada langsung memakai DoA 2 Hz, graph 0,5 Hz, dan nav 1 Hz bersamaan.

P4 U8 mempertahankan 360 arah dan memperbarui grafik tiap 2 detik dengan biaya sekitar 9,25 kbit/s pada model. Komprominya pada resolusi amplitudo, bukan jumlah arah. P5 sekitar 17,03 kbit/s sebelum command dan belum layak dijadikan default untuk asumsi link 15 kbit/s.

Angka P6 menunjukkan bahwa raw CSV tiap 5 detik pun tidak otomatis "aman", setelah semua stream dan overhead ditambahkan. Cara ini masih dapat digunakan untuk snapshot manual dengan rate lebih rendah dan pause bulk lainnya, bukan sebagai jalan pintas tanpa budget.

### 9.5 Biaya satu transaksi perubahan frekuensi

Model satu transaksi terdiri dari command, accepted ACK, applied ACK, reported config, dan state. Semua menggunakan QoS 1. Tidak ada progress spam.

| Pesan transaksi | Arah publish | Payload byte | MQTT byte | Total transport dua arah model, byte |
|---|---|---:|---:|---:|
| Command config | Ground -> UAV | 250 | 293 | 581 |
| ACK accepted | UAV -> Ground | 89 | 125 | 413 |
| ACK applied | UAV -> Ground | 119 | 156 | 444 |
| Config reported | UAV -> Ground | 214 | 251 | 539 |
| State | UAV -> Ground | 158 | 185 | 473 |
| **Total** | Dua arah | **830** | **1010** | **2450** |

Hasil model: **2.450 byte** aggregate dua arah sebelum allowance, atau sekitar **2.818 byte** dengan allowance 15%. Jika satu transaksi per menit, tambahan rata-rata sekitar **0,376 kbit/s**. Jika satu transaksi per 10 detik, sekitar **2,254 kbit/s**.

P1 dengan satu transaksi per menit menjadi kira-kira **9,52 kbit/s rata-rata** pada model. Tetapi command dikirim dalam burst, bukan diratakan selama satu menit. Itulah sebabnya bulk angular harus dipause ketika command diproses. Profile CONTROL membebaskan anggaran grafik untuk command dan verifikasi.

Pada burst asumsi di atas, 2.818 byte setara 22.544 bit aggregate. Dibagi anggaran agregat 15.000 bit/s memberi sekitar 1,50 detik service time matematis, **bukan SLA command**, karena processing, RTT, direction scheduling, dan retransmission belum tercermin sebagai latency nyata.

### 9.6 Biaya start, stop, restart, dan reboot

Start/stop biasanya membawa request kecil, accepted, beberapa state transition, dan result. Anggaran awal 2-4 KB total per operasi adalah allowance desain yang harus diganti hasil capture.

Restart RDF mempunyai payload request serupa tetapi dapat membutuhkan waktu processing lebih lama. Health 1 Hz tetap dikirim; progress maksimum 0,2 Hz hanya selama operasi. Ini menambah trafik yang tidak dimodelkan pada transaksi config sederhana.

Reboot mencakup prepare, challenge, execute, acknowledgement, disconnect, TLS/MQTT reconnect, capabilities/state, dan result setelah boot. Tidak ada angka handshake tetap yang dapat dipercaya tanpa certificate chain dan versi TLS aktual. Sebagai **skenario perencanaan**, jika reconnect menghabiskan 4-12 KB aggregate, service time pada anggaran 15 kbit/s saja sekitar 2,1-6,4 detik, di luar waktu boot OS/SDR. Angka 4-12 KB ini asumsi ilustratif, bukan hasil ukur maupun batas maksimum.

### 9.7 Multi-VFO dan perubahan payload

Biaya stream DoA dan angular bertambah untuk setiap VFO yang dikirim. Metadata tetap tidak menghapus biaya 360 sampel per VFO. MVP membatasi satu VFO output. Penambahan VFO kedua, SNR, navigation ber-rate tinggi, tanda tangan per-message, atau health detail lebih banyak harus menghasilkan perhitungan baru.

Menjalankan ping terus 1 Hz, `journalctl -f`, browser ke Web UI Raspberry, download settings, atau SSH transfer pada saat yang sama juga mengonsumsi link. Pengukuran harus merepresentasikan cara operator memakai sistem, bukan hanya publisher sendirian.

### 9.8 Cara mengubah estimasi menjadi hasil uji

Catat terpisah: payload bytes, MQTT bytes, IP tx/rx, ACK/retransmission, byte serial jika tersedia, dan application age. Counter `ip -s link show ppp0` tidak otomatis mencakup seluruh UART escaping, framing RF, atau start/stop bit.

Uji setiap profil dengan TLS dan client yang sama seperti produksi. Ukur minimal median/P95/P99 command accepted latency, apply latency, umur DoA, umur curve, queue depth, CPU DSP, dropped frame delta, reconnect time, dan persentase frame angular lengkap.

Jangan memvalidasi hanya dengan "grafik bergerak". Profil dinyatakan layak jika health dan command tetap memenuhi target saat grafik aktif, termasuk ketika command burst dan reconnect terjadi.

<a id="bagian-10"></a>
## 10. Health, freshness, dan arti indikator status

### 10.1 Jangan satukan semua menjadi ONLINE

State internal dibagi menjadi beberapa dimensi:

| Dimensi | Contoh state | Bukti yang dipakai |
|---|---|---|
| Host | BOOTING, READY, DEGRADED | Host monitor dan heartbeat agent |
| T900 USB | PRESENT, MISSING, UNKNOWN | Device alias dan identitas udev |
| PPP | NEGOTIATING, UP, DOWN | Interface, alamat peer, dan event pppd |
| MQTT | CONNECTING, CONNECTED, DISCONNECTED | Hasil koneksi MQTT, bukan port TCP saja |
| Ground backend | RECEIVING, LATE, LOST | Ground receipt dengan sequence yang cocok |
| Processing | STOPPED, STARTING, RUNNING, STOPPING, RESTARTING, ERROR | Intent, operation journal, dan observasi proses |
| DAQ | HEALTHY, DEGRADED, UNKNOWN | Frame/sync/drop/status yang fresh |
| Detection | VALID, NO_DETECTION, STALE, UNVERIFIED | Snapshot native dan publication gate |
| Curve | FRESH, LATE, STALE, INCOMPLETE | Angular timestamp dan chunk assembly |
| Config | SYNCED, PENDING, CONFLICT, UNVERIFIED | Revision, digest subset, runtime proof |
| Clock | SYNCED, HOLDOVER, UNTRUSTED | Clock observation dan uncertainty |

Link RSSI T900 tidak sama dengan power sinyal yang diukur Kraken. Bila metrik radio tidak tersedia dari perangkat dengan cara yang tervalidasi, tampilkan `Link RSSI: unavailable`. Jangan meminjam nilai power RDF untuk indikator kualitas RF telemetry.

### 10.2 Isi health ringkas dan detail

Health ringkas 1 Hz berisi processing state, DAQ summary, dropped frame total, umur DoA pada node, temperatur bila tersedia, clock state, dan config revision. Health ini dihasilkan oleh bridge/host monitor walaupun file `status.json` SDR berhenti berubah.

Health detail 0,1 Hz berisi jumlah USB SDR, frame sync/sample-delay/IQ sync, ADC overdrive, CPU/RAM/disk, throttle/undervoltage jika API lokal tersedia, traffic counters, queue/drop counters, clock uncertainty, GPS state, dan kode error. Perubahan critical dipromosikan menjadi event P0/P1 dengan rate limit.

Dua aturan penting: jumlah lima perangkat USB hanya membuktikan enumeration, bukan coherent sampling sehat; cumulative dropped frames yang besar tidak selalu berarti sedang gagal sekarang, sehingga perlu delta/rate dan frame index. [S09, bagian 4, 7, 11]

### 10.3 Threshold awal yang diusulkan

| Indikator | Fresh / normal | Warning | Stale / unavailable |
|---|---|---|---|
| Health 1 Hz di Ground | Age <=3 s | >3 s | >8 s: data status stale; >15 s: unreachable dari perspektif Ground |
| DoA 1 Hz | Source age <=2,5 s dan gate lulus | >2,5 s | >5 s atau gate gagal |
| Angular 0,25 Hz | Curve age <=6 s | >6 s | >10 s |
| Ground receipt 0,2 Hz di Raspberry | Diterima <=10 s | >10 s | >15 s |
| Local API snapshot | Diperbarui <=2 s | >2 s | >5 s: agent/API stale |
| Config sync | Rev/hash/proof cocok | Applying/pending | Konflik atau source attribution belum jelas |

Threshold adalah proposal UX awal. Tune berdasarkan processing latency sebenarnya dan rate profile. Angular yang sengaja dipause saat command harus tampil `PAUSED FOR CONTROL`, bukan error komunikasi baru. Grafik lama tetap menampilkan umur dan config asal.

UI boleh menyatakan `UNREACHABLE` setelah health hilang, tetapi tidak otomatis tahu apakah Raspberry mati, PPP putus, broker down, atau hanya aplikasi Ground gagal. Penyebab lebih spesifik hanya ditampilkan bila ada bukti.

### 10.4 Waktu lokal dan waktu antar-host

Gunakan monotonic clock untuk timeout, backoff, lease, dan age sejak paket diterima. Gunakan UTC epoch untuk correlation lintas host setelah kualitas jam tervalidasi.

`age_at_source + elapsed_since_receipt` adalah batas bawah perkiraan umur ketika transit tidak diketahui; jangan menamainya total end-to-end latency. Penghitungan `ground_now - source_timestamp` memerlukan jam yang sinkron dan pengakuan uncertainty. Bila clock untrusted, tampilkan age berbasis penerimaan dengan label jelas dan jangan mempublikasikan angka latency absolut palsu.

Command mutating dengan deadline absolut memerlukan jam yang dipercaya. Jika tidak, backend dapat melakukan handshake freshness berbasis challenge dan timeout monotonic pada node yang dirancang khusus; ini bukan alasan menerima command lama tanpa expiry. Pada MVP, disable mutating command bila clock gate belum tersedia.

### 10.5 Skenario yang harus tampil dengan benar

- PPP UP dan MQTT CONNECTED tetapi `daq_ok=false`: link sehat, RDF DEGRADED.
- Engine RUNNING, DAQ sehat, tidak ada signal melewati squelch: NO_DETECTION, bukan crash.
- Operator STOP: processing STOPPED, bridge ONLINE, health tetap baru, curve last-known.
- Ground backend crash tetapi broker hidup: MQTT connected, GROUND BACKEND RX lost.
- Display Raspberry mati: bridge dan processing tidak berubah.
- Raspberry reboot: planned outage, health tidak mungkin diperbarui sampai host kembali.
- Config berubah sementara source lama masih di file: old-config snapshot tidak boleh diberi revision baru.

<a id="bagian-11"></a>
## 11. Command protocol dan state machine operasi

### 11.1 Command yang termasuk ruang lingkup

| Operasi | Fungsi | Kelas risiko | Default |
|---|---|---|---|
| `config.get` | Snapshot subset konfigurasi aman | Read-only | Diaktifkan setelah MQTT aman |
| `config.patch` | Ubah field RF/DSP allowlist | Mutating | Fase setelah runtime proof tersedia |
| `processing.set` | Set desired state RUNNING/STOPPED | Mutating | Fase lifecycle; bukan toggle |
| `service.restart` | Restart stack RDF saja | Disruptive | Maintenance/otorisasi khusus |
| `system.reboot.prepare` | Validasi dan challenge reboot | Disruptive | Disabled sampai reboot gate diuji |
| `system.reboot.execute` | Eksekusi reboot dengan challenge valid | High impact | Maintenance-only |
| `operation.get` | Cari outcome command menurut ID | Read-only | Diaktifkan bersama command |
| `stream.set` | Ubah profil/rate dalam batas | Mutating ringan | Tidak boleh melampaui budget lokal |
| `diagnostic.get` | Snapshot/log terbatas | Sensitive read | Role maintenance, allowlist |

Tidak ada operasi `exec`, `shell`, `sudo`, `run_script`, reboot flight controller, atau arbitrary URL/file path pada protocol.

### 11.2 Envelope command

Contoh config command yang dipakai dalam perhitungan:

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

Indentasi di atas untuk keterbacaan. Versi compact-nya 250 byte pada contoh ini.

Field wajib: versi schema, command ID, target boot, target session, issued/deadline, nama operasi, dan parameter sesuai operasi. Patch juga memerlukan `base_rev`. Topic dan `op` harus konsisten; jika tidak, reject.

Identitas operator berasal dari login Ground backend dan otorisasi channel yang dipercaya, bukan semata-mata field `operator="admin"` yang dikirim client. Jika organisasi membutuhkan identitas operator diverifikasi end-to-end tanpa mempercayai broker, tambahkan signature dan model byte baru; tidak termasuk contoh 250 byte.

### 11.3 Lifecycle operasi

```text
Ground: DRAFT -> SENT -> AWAITING_ACCEPTANCE
                              |
Raspberry:              VALIDATE + JOURNAL
                              |
                         ACCEPTED
                              |
                          APPLYING
                              |
                          VERIFYING
                         /         \
                   COMPLETED       FAILED / CONFLICT

Alternatif terminal:
REJECTED, EXPIRED, ALREADY_SATISFIED, CANCELLED_BEFORE_START

Di Ground saat komunikasi hilang:
OUTCOME_UNKNOWN -> operation.get -> outcome yang benar
```

ACK wire memakai `stage=accepted`, `applying`, `verifying`, `applied`, `failed`, atau bentuk sesuai schema operasi. Ground boleh merangkum `applied` sebagai COMPLETED hanya jika proof yang diperlukan sudah terpenuhi.

Transport PUBACK, application accepted, dan applied adalah tiga tahap berbeda. Untuk operasi berbahaya, received-only tidak cukup untuk bertindak di UI; accepted baru dikirim setelah precondition dan journal intent tersimpan sesuai policy.

### 11.4 Urutan validasi

1. Authentication/ACL channel lolos; source adalah backend yang boleh mengirim operasi ini.
2. Payload size, JSON schema, tipe, field wajib, dan enum valid.
3. Node, boot, session, dan command topic sesuai.
4. Deadline belum lewat dan clock gate memenuhi syarat.
5. Command ID belum dipakai untuk payload lain. Hash request yang berbeda dengan ID sama ditolak.
6. State/precondition memungkinkan operasi; tidak ada operasi mutating lain yang konflik.
7. Config revision/expected state cocok.
8. Range/unit dan policy allowlist lolos.
9. Untuk destructive operation, maintenance dan confirmation gate lolos.
10. Simpan journal, publish accepted, jalankan worker di luar callback MQTT.

Deadline penerimaan berbeda dari timeout pelaksanaan. Operasi yang sudah dimulai tidak diputus secara kasar hanya karena deadline penerimaan lewat. Worker mempunyai execution timeout terpisah dan prosedur reconcile/rollback yang aman.

### 11.5 Deduplication dan crash recovery

Gunakan persistent journal untuk request hash, ID, target boot, requested change, status, timestamps, result proof, dan error code. Data aman yang diperlukan untuk recovery disimpan lokal, tidak semua settings.

Duplicate command ID dengan payload sama mengembalikan outcome yang sudah ada; jangan mengulang tuning/restart. Duplicate ID dengan payload berbeda mendapat `ID_REUSE_CONFLICT`.

Tidak ada klaim exactly-once mutasi perangkat hanya karena QoS 1/2 digunakan. Crash dapat terjadi antara perubahan fisik dan commit journal. Setelah restart, reconcile keadaan aktual sebelum melanjutkan atau menyatakan outcome. Jika bukti tidak cukup, status `OUTCOME_UNKNOWN` lebih benar daripada menjalankan ulang reboot.

### 11.6 Timeout, retry, dan pembatasan operator

Usulan awal: deadline penerimaan config 15 detik; target accepted pada kondisi bench normal P95 <=2 detik; timeout apply hot-setting 15 detik; start/restart stack dapat diberi operation window lebih panjang sesuai hasil cold/warm-start. Semua angka merupakan target pengujian, bukan kemampuan terjamin.

Ground tidak membuat command ID baru otomatis saat accepted/result terlambat. Langkah pertama adalah query ID lama. Mutating control di UI menggunakan tombol Apply dan debounce; slider tidak mengirim setiap frame drag. Satu perubahan baru boleh menggantikan draft, tetapi tidak menimpa operasi yang sudah accepted.

Progress maksimal 0,2 Hz selama operasi lama, ditambah perubahan tahap penting. Health 1 Hz tetap berjalan. Jangan mengirim progress, state, health, logs, dan config penuh tiap 100 ms untuk satu operasi.

<a id="bagian-12"></a>
## 12. Settings, sinkronisasi, dan proof perubahan

### 12.1 Tiga arti synchronize yang harus dipisahkan

**Sinkronisasi konfigurasi** berarti Ground mengetahui konfigurasi efektif Raspberry dan revision yang cocok.

**Sinkronisasi waktu** berarti timestamp lintas host dapat dibandingkan dengan uncertainty yang diketahui.

**Sinkronisasi DAQ** berarti receiver coherent/frame/sample-delay/IQ memenuhi syarat engine.

Tombol generik `Synchronize` tanpa cakupan berbahaya bagi diagnosis. Gunakan label `Refresh Config`, `Verify Config`, `Check Clock`, dan `DAQ Status` yang berbeda. Tidak ada tombol untuk memaksa DAQ sync menjadi true melalui perubahan UI.

### 12.2 Source of truth dan three-state configuration

Sumber otoritatif konfigurasi efektif berada pada Raspberry, karena hardware dan DSP berada di sana. Ground menyimpan mirror/cached snapshot dan draft operator.

Pisahkan:

```text
requested / desired    : yang diminta operator
persisted candidate    : yang berhasil ditulis ke konfigurasi lokal
observed effective     : yang telah terbukti dipakai runtime
```

File read-back hanya membuktikan persisted candidate. `applied` untuk frekuensi/gain/DSP perlu bukti runtime yang sesuai. Jika adapter belum bisa membaca atribut runtime, status menjadi `PERSISTED_UNVERIFIED`, bukan applied.

### 12.3 Mapping awal allowlist

| Field API | Field native kandidat | Validasi penting |
|---|---|---|
| `center_frequency_hz` | `center_freq` | Arsip memakai MHz untuk center; converter unit wajib diaudit |
| `gain_db` | `uniform_gain` | Range/step sesuai runtime/hardware; AGC mode dibedakan |
| `vfo0_frequency_hz` | `vfo_freq_0` | Dalam passband dan cocok dengan output VFO |
| `vfo0_bandwidth_hz` | `vfo_bw_0` | Valid untuk sample rate/filter yang aktif |
| `squelch` | `vfo_squelch_0` dan mode terkait | Jangan mengubah mode dan level secara ambigu |
| `doa_method` | `doa_method` | Enum yang benar-benar didukung deployment |
| `output_vfo` | `output_vfo` | Batas VFO aktif; satu VFO telemetry pada MVP |

Nama field native dari source bukan kontrak final sampai source deployment diperiksa. Khusus `ant_spacing_meters`, studi project mengingatkan bahwa maknanya untuk UCA dapat berupa radius, bukan sekadar jarak antar-antena. Perubahan geometry tidak menjadi hot-setting umum. [S11, bagian 4.4, 5]

Range frekuensi/gain tidak ditebak dari contoh dokumentasi. Capabilities runtime yang sudah divalidasi menyatakan range dan step. Tidak ada full settings replace, secret field, external endpoint, atau konfigurasi DAQ berisiko melalui patch rutin.

### 12.4 Transaksi perubahan frekuensi

```text
Ground membaca config_rev=7 yang masih fresh
    -> operator mengisi frekuensi target
    -> backend membuat patch id=X, base_rev=7, expiry
    -> Raspberry validasi dan menyimpan intent
    -> ACK accepted
    -> pause angular, set config state APPLYING
    -> merge allowlist patch, apply melalui adapter lokal
    -> tunggu watcher/reconfigure
    -> periksa runtime center/VFO yang relevan
    -> tunggu data baru dari konfigurasi baru bila tersedia
    -> commit effective rev=8
    -> ACK applied dengan proof
    -> reported config dan state rev=8
    -> lanjut angular setelah health stabil
```

Saat mengubah center frequency, tentukan kebijakan VFO secara eksplisit: mengikuti center atau tetap absolut di dalam passband. Jangan membiarkan center pindah tetapi VFO tertinggal di frekuensi lama lalu melaporkan "tuning sukses" tanpa informasi itu.

### 12.5 Level proof

| Proof | Yang terbukti | Layak menjadi applied? |
|---|---|---|
| `transport` | MQTT menerima | Tidak |
| `accepted` | Validasi/journal lolos | Tidak |
| `file` | File lokal berisi nilai baru | Hanya persisted, bukan runtime applied |
| `runtime` | Nilai efektif engine/hardware diamati | Ya untuk setting terkait, dengan definisi bukti |
| `runtime_and_output` | Runtime cocok dan output baru berkorelasi | Bukti lebih kuat bila tersedia |

Frekuensi pada record VFO saja belum tentu membuktikan RF center hardware benar; bila claim-nya center tuning, proof harus mengecek RF center dari header/control/runtime yang dapat diandalkan. Untuk gain, pembacaan file `uniform_gain` saja tidak membuktikan applied gain receiver. Adapter evidence tambahan adalah pekerjaan implementasi, bukan endpoint yang sudah diasumsikan tersedia.

### 12.6 Single-writer dan konflik GUI lama

Semua perubahan konfigurasi dari Ground dan local maintenance harus melewati manager yang sama. Atomic replace mencegah file parsial, tetapi **tidak mencegah lost update** jika GUI lama menulis bersamaan tanpa lock.

Pilih salah satu strategi yang benar-benar diterapkan: GUI lama menjadi read-only untuk config; semua writer menggunakan lock/protocol yang sama; atau adapter mendeteksi external mutation dan masuk conflict/read-only sampai reconcile. Mengunci hanya writer baru tidak cukup.

Jika file dikelola langsung, gunakan temp file pada filesystem yang sama, flush/fsync, atomic rename, dan fsync direktori sesuai kebutuhan durability. Pertahankan field internal yang tidak diubah; jangan menyalin settings penuh lewat radio. Revision kandidat dan revision efektif dipisahkan selama apply.

### 12.7 State retained dan reconnect

Retained `config/reported` menampilkan snapshot terakhir beserta `rev`, `observed_at`, `proof`, dan safe-subset digest. Setelah reconnect, Ground meminta reported config terbaru; jangan mendorong draft lama otomatis ke Raspberry.

Jika Ground rev 7 tetapi node sudah rev 8 karena local change, patch base_rev=7 ditolak sebagai conflict. UI menampilkan diff dan meminta operator meninjau perubahan. Digest dibuat dari subset aman canonical, bukan hash rahasia yang kemudian dipublikasikan tanpa analisis.

Jika source attribution terhadap revision belum terbukti, record memakai `rev=unknown` atau publication gate ditahan. Membaca config terbaru lalu menempelkannya pada file DoA lama adalah kesalahan integritas data.

<a id="bagian-13"></a>
## 13. Start, stop, dan restart RDF tanpa kehilangan bridge

### 13.1 Definisi tombol harus jelas

`Start RDF` berarti meminta stack RDF menghasilkan frame/pengukuran. `Stop RDF` berarti menghentikan processing atau stack RDF sesuai adapter yang tervalidasi. Keduanya **tidak** berarti stop MQTT agent, stop PPP, atau reboot OS.

`Restart RDF Stack` berarti siklus stop/start komponen RDF yang terdaftar. `Restart Raspberry` berarti reboot seluruh komputer dan pasti memutus komunikasi sementara. UI tidak boleh menamai keduanya sekadar `Restart`.

### 13.2 Adapter lifecycle yang realistis

Ada dua kemungkinan implementasi:

| Mode | Implementasi | Konsekuensi |
|---|---|---|
| Process-level control | Native engine mempunyai API start/stop yang bisa diaudit | GUI/Data Out mungkin tetap hidup; perlu proof processing benar-benar berhenti |
| Stack-level control | Helper mengelola service stack RDF yang sudah diverifikasi | GUI native dan Data Out mungkin ikut turun; bridge/host health wajib tetap hidup |

Dokumen tidak mengklaim API process-level yang aman sudah tersedia. Audit source menentukan mode. Capability `processing_control_mode` membuat Ground tahu tombol Stop sebenarnya menghentikan apa.

### 13.3 Stop adalah intent, bukan kegagalan

Urutan stop yang diusulkan:

1. Validasi command dan simpan desired state `STOPPED` secara durable.
2. Publish accepted dan status `STOPPING`.
3. Pause DoA/array publishing; jangan mengubah timestamp data terakhir.
4. Beri tahu supervisor/watchdog bahwa penghentian ini disengaja.
5. Hentikan komponen RDF yang sesuai melalui helper.
6. Verifikasi postcondition: stack/proses yang ditarget berhenti atau processing flag terbukti off.
7. Publish result dan state `STOPPED`; health node tetap 1 Hz.

Desired state persisten mencegah watchdog menganggap stop operator sebagai crash. Namun policy setelah boot harus disepakati: restore last desired state atau autostart RUNNING. Default rancangan ini **restore intent** untuk mencegah Stop berubah menjadi Start tanpa persetujuan.

### 13.4 Konflik watchdog lama wajib diselesaikan

Dokumen service lama memeriksa HTTP root `:8080` dan me-restart `sdr-doa.service` jika tidak merespons. Jika stack RDF sengaja dihentikan, watchdog tersebut dapat menghidupkannya kembali. [S04, bagian 19-22]

Sebelum remote Stop diaktifkan, watchdog harus membaca desired state/maintenance operation. Ketika desired state STOPPED, ia tidak memulai engine. Ketika STARTING/RESTARTING, berlaku grace window sesuai operation, bukan reboot loop. Ketika RUNNING dan health gagal, recovery dibatasi jumlah percobaan dan cooldown.

Jangan menonaktifkan semua watchdog untuk menyelesaikan masalah ini. PPP recovery, agent watchdog, dan engine watchdog mempunyai tanggung jawab berbeda. Kegagalan RF tidak boleh otomatis menyebabkan restart DAQ; hilangnya detection tidak boleh otomatis menyebabkan reboot OS.

### 13.5 Start dan verifikasi readiness

Start yang idempotent memakai `desired_state=RUNNING`, bukan toggle. Bila sudah running dan sehat, hasil boleh `ALREADY_SATISFIED` dengan state terbaru.

Setelah start, accepted tidak berarti DoA langsung siap. Postcondition dapat memerlukan USB available, proses engine hidup, frame index bergerak, frame/sample-delay/IQ sync valid, dan waktu stabilisasi. Bila tidak ada sinyal, readiness DAQ tetap dapat lulus dengan detection `NO_DETECTION`; jangan menunggu sinyal eksternal tanpa batas untuk mengakui engine ready.

JIT/DSP initialization pernah dicatat sebagai faktor startup pada dokumen project. Timeout final harus berasal dari pengukuran deployment sekarang, bukan satu angka pendek yang memaksa restart berulang. [S11, bagian 12]

### 13.6 Restart stack dan identitas generasi

Untuk restart stack, `boot_id` OS tetap sama. Agent mencatat `engine_generation` atau evidence proses/start time yang baru. Result restart tidak boleh diverifikasi hanya dengan melihat `boot_id` berbeda, karena itu indikator reboot OS, bukan restart engine.

Jika restart berhenti di tengah, bridge tetap melaporkan `ERROR` dan operation ID. Tidak ada auto-reboot Raspberry sebagai fallback tersembunyi. Escalation membutuhkan policy dan authorization terpisah.

<a id="bagian-14"></a>
## 14. Reboot Raspberry yang terkontrol

### 14.1 Fitur termasuk desain, tetapi bukan default prototype

Permintaan reboot Raspberry dari Ground dimasukkan sebagai kemampuan yang akan dibangun. Ini memperluas rancangan lama yang melarang reboot pada prototype. Fitur tetap disabled sampai authentication, journal, lifecycle, dan return verification lulus. [S10, bagian 9.7; usulan baru]

Remote reboot menyebabkan telemetry hilang selama OS/agent/PPP/SDR kembali. Tidak ada cara software di Raspberry untuk mengirim status ketika komputer itu sedang mati/reboot. Ground harus mengelola expected outage.

### 14.2 Precondition minimum

Reboot hanya diterima ketika operator berwenang, node sedang dalam maintenance window, tidak ada operasi config yang aktif, journal dapat ditulis, target boot/session fresh, dan interlock keselamatan terpenuhi.

Bila platform berada di UAV, status in-flight/armed **tidak boleh ditebak** dari GPS, kecepatan, atau nama node. Jika tidak ada sumber status penerbangan yang otoritatif, default yang aman adalah manual bench/ground maintenance enable pada node. Reboot tidak diaktifkan untuk operasi terbang yang belum mempunyai interlock terverifikasi.

### 14.3 Dua tahap prepare dan execute

```text
Ground -> reboot.prepare(id=A, target boot, expiry)
Raspberry -> validate -> challenge token + expires_in + impact summary
Ground -> operator mengkonfirmasi perangkat dan dampaknya
Ground -> reboot.execute(id=B, challenge, target boot, expiry)
Raspberry -> validasi ulang -> persist intent -> ACK scheduled
Raspberry -> hentikan RDF dengan tertib -> flush journal -> request reboot OS
Ground -> REBOOTING / WAITING FOR RETURN
Raspberry boot baru -> PPP -> MQTT -> fresh state + recovered operation
Ground -> cocokkan boot baru dan operation -> REBOOT COMPLETED
```

Challenge harus berumur pendek, sekali pakai, terikat pada operator/session/boot/action, dan tidak disimpan sebagai retained command. Proposal umur awal 30 detik. Lease memakai monotonic timer node agar perubahan wall clock tidak memperpanjang masa berlaku.

Eksekusi memakai graceful reboot melalui helper yang terbatas, bukan `reboot -f`, bukan payload command shell, dan bukan memutus daya. Ground memperoleh `scheduled`, bukan `applied`, sebelum komputer reboot.

### 14.4 Proof setelah kembali

Keberhasilan reboot memerlukan fresh authenticated session, `boot_id` OS baru, journal intent yang menunjukkan korelasi ke permintaan reboot, dan post-boot state yang dapat dibaca. Perubahan `sid` saja tidak cukup karena agent restart juga mengubah session.

Jika hanya terlihat boot baru tanpa journal yang cocok, UI dapat mengatakan "node telah boot ulang; korelasi command belum terverifikasi". Jika tidak kembali dalam operation window yang diukur, status `RETURN_TIMEOUT / OUTCOME_UNKNOWN`; jangan kirim reboot baru otomatis.

Penyelesaian reboot dan kesiapan RDF adalah dua hasil: OS/agent bisa kembali tetapi DAQ belum sehat. UI menunjukkan keduanya terpisah.

### 14.5 Batas yang disengaja

Shutdown/poweroff tidak masuk tombol normal, karena node yang mati tidak selalu dapat dinyalakan lagi lewat link yang sama. Mengubah PPP/udev/radio parameters dari remote command tidak termasuk fitur rutin. Reboot broker Ubuntu juga bukan bagian dari perintah reboot Raspberry.

<a id="bagian-15"></a>
## 15. Dashboard Raspberry 5 inci

### 15.1 Tujuan dan pemisahan aplikasi

Panel Raspberry memberi status penting yang mudah dibaca saat pemasangan atau troubleshooting. Ia bukan salinan seluruh Ground Dashboard dan tidak perlu map, waterfall, tabel settings panjang, atau file log penuh.

Usulan implementasi: frontend ringan, assets lokal, API loopback `127.0.0.1:8790` yang dapat dikonfigurasi. Browser kiosk berjalan sebagai user biasa. Port 8790 adalah **usulan baru**, bukan port yang sudah terkonfirmasi aktif. Ground tetap memakai arsitektur Python + static frontend sesuai dokumen frontend saat ini. [S08]

Tidak diperlukan broker lokal di Raspberry agar panel tetap bekerja. Panel membaca state lokal bridge/host monitor, sehingga Ground offline tidak membuat halaman kosong.

### 15.2 Wireframe utama, kanvas asumsi 800 x 480

```text
+------------------------------------------------------------------+
| RDF NODE                         LOCAL MODE       14:26 WIB       |
| [T900 OK] [PPP UP] [MQTT OK] [GROUND RX 3s] [CFG SYNCED]           |
+----------------------------------+-------------------------------+
| PROCESSING                       | CURRENT DIRECTION             |
| RUNNING                          |           137.4 deg           |
| DAQ SYNC OK   Data age 0.7s       |      RELATIVE / VERIFIED      |
| 5/5 USB present                  | Freq 433.920 MHz              |
+----------------------------------+-------------------------------+
| QUALITY                          | SYSTEM                        |
| PAPR 8.27 dB    Power -54.2 dB    | Temp 61 C     CPU 38%         |
| Detection VALID   Drop delta 0   | Clock SYNC    Storage OK      |
+------------------------------------------------------------------+
| LAST COMMAND: Tune frequency - VERIFIED - config rev 8            |
| [Overview]                  [Details]                 [About]     |
+------------------------------------------------------------------+
```

Angka pada wireframe adalah contoh, bukan status perangkat sekarang. Ukuran fisik 5 inci tidak menentukan pixel density; ukuran font dan touch harus diuji di layar asli, bukan hanya screenshot desktop.

### 15.3 Hierarki informasi

Prioritas pertama: processing, DAQ health, link, dan Ground receipt. Prioritas kedua: frekuensi dan arah dengan age. Prioritas ketiga: command/config state dan alarm sistem.

Jika layar lebih sempit, kurangi jumlah field halaman utama. Jangan mengecilkan semua label sampai sulit dibaca. Pada 480 x 320, gunakan halaman Overview berisi 4-6 status utama dan pindahkan CPU/RAM/storage ke Details.

Target desain awal pada 800 x 480: label sekitar 18-22 CSS pixel, status utama 26-32, arah sekitar 48-64, touch target sekitar 48 CSS pixel atau lebih. Angka ini pedoman layout usulan, bukan standar yang menjamin keterbacaan pada setiap panel.

### 15.4 State yang harus tampak di layar

| Keadaan | Pesan utama | Perilaku |
|---|---|---|
| Normal | RUNNING - DAQ SYNC OK | DoA dan age ditampilkan |
| Ground terputus | LOCAL RDF RUNNING - GROUND LOST | DoA lokal tetap dapat tampil; tidak mengaku terkirim |
| Stop operator | STOPPED BY OPERATOR | Last data diberi age/label atau diganti tanda unavailable |
| DAQ rusak | RDF DEGRADED - CHECK DAQ | Jangan menampilkan hijau hanya karena MQTT connect |
| Tidak ada detection | RUNNING - NO DETECTION | Frequency dan system health tetap tampil |
| Config apply | APPLYING CONFIG - OPERATION X | Nilai requested dan effective tidak dicampur |
| Reboot dijadwalkan | REBOOT SCHEDULED | Dampak dan alasan tampil sebelum layar ikut berhenti |
| Agent/API macet | STATUS SOURCE LOST | Browser memakai timeout lokal, bukan freeze status hijau |

Warna boleh mendukung makna, tetapi setiap indikator juga punya teks/icon. Hindari flashing berlebihan. Alarm critical mengalahkan angka arah besar jika nilai arah tidak lagi valid.

### 15.5 Halaman Details

Tampilkan ringkasan: boot/session, version, timestamp sumber, DAQ sync flags, drop total/delta, last error, MQTT connection, last Ground receipt, queue size, byte rate, config revision/proof, dan operation ID terakhir. Identifier sensitif/credential tidak ditampilkan.

Details lokal boleh lebih sering membaca data dari state store karena tidak lewat T900, tetapi tidak boleh memicu probe mahal setiap kali halaman di-render. Backend mengumpulkan metrik sekali dan membagikan snapshot.

### 15.6 Kontrol lokal

Default panel 5 inci read-only. Local start/stop opsional berada pada halaman maintenance dengan autentikasi/konfirmasi dan memakai **command manager yang sama** seperti Ground. Tidak ada jalur tulis settings paralel dari browser.

Tombol reboot tidak ada di halaman utama. Jika disediakan pada maintenance, tetap memerlukan precondition, challenge, dan pencatatan. Menahan tombol visual saja bukan authorization backend.

### 15.7 Update lokal bukan beban radio

State lokal dapat disegarkan sekitar 2 Hz dan UI dirender halus secara lokal. Data hardware yang tidak berubah cepat, seperti storage, tidak perlu diprobe 2 Hz. Semua assets disimpan di Raspberry; tidak ada CDN/font remote sebagai syarat panel bisa dibuka.

Pembaruan `localhost -> browser panel` tidak menambah trafik T900. Tetapi browser memakai CPU/RAM/GPU Raspberry yang sama dengan DSP, sehingga harus diukur pengaruhnya terhadap dropped frames dan temperatur.

### 15.8 Kiosk dan display failure

Kiosk harus auto-start dalam graphical session yang benar-benar tersedia pada OS pengguna. Dokumen tidak memaksakan X11 atau Wayland tanpa memeriksa deployment. Browser tidak berjalan sebagai root dan tidak menjalankan Node/Vite development server di produksi.

Display restart tidak boleh restart agent/PPP/DAQ. Agent restart tidak harus restart engine. Layar yang mati secara fisik tidak dapat menjadi satu-satunya bukti health; status tetap tersedia dari Ground atau local management.

<a id="bagian-16"></a>
## 16. Integrasi Ground Dashboard

### 16.1 Pertahankan boundary Python backend

Dokumentasi frontend sekarang menyebut React + TypeScript + Vite dan Python collector/API/monitor MQTT yang subscriber-only. Jadi jalur write command pada Ground adalah **fitur baru**, bukan fungsi yang boleh dianggap sudah tersedia. [S08]

Ground frontend tidak langsung memegang broker credentials. Backend menangani MQTT decode, state, command journal, authorization, dan API same-origin. Payload binary angular dikonversi di backend menjadi array numerik untuk frontend, sehingga frontend tidak perlu mengetahui serial/PPP.

### 16.2 Fitur UI Ground yang perlu ditambahkan

Overview: panah DoA terbaru, polar plot, age berbeda, frekuensi/VFO, dan banner state.

Health: status per-layer, DAQ sync, drop delta, actual rate, queue, clock, dan receipt.

Configuration: safe reported config, requested draft, diff, revision/proof, tombol Apply dan Refresh.

Operations: Start RDF, Stop RDF, Restart RDF Stack, serta Reboot Raspberry pada maintenance dengan state machine lengkap.

Command history: ID, requested action, operator terautentikasi, accepted time, final outcome, proof, error, dan status unknown yang dapat di-query.

### 16.3 API contoh, seluruhnya usulan baru

| Endpoint lokal | Fungsi |
|---|---|
| `GET /api/v2/nodes` | Daftar node dan status ringkas |
| `GET /api/v2/nodes/{id}/snapshot` | Snapshot canonical dengan age/provenance |
| `GET /api/v2/nodes/{id}/angular/latest` | Array terakhir, metadata, freshness |
| `GET /api/v2/nodes/{id}/config` | Safe reported/effective config |
| `POST /api/v2/nodes/{id}/commands` | Pengajuan command terautorisasi |
| `GET /api/v2/operations/{id}` | Status/outcome operasi |
| `GET /api/v2/events` | Event stream lokal/SSE bila dipilih |

Endpoint mutating memerlukan autentikasi, otorisasi, CSRF/origin protection sesuai model session, size limit, dan audit. Binding loopback sendiri bukan pengganti perlindungan terhadap request dari browser yang tidak dipercaya.

Tidak ada endpoint di tabel yang dianggap telah dibuat. Nama dan implementasinya harus disesuaikan dengan repo Ground yang sebenarnya setelah source code tersedia.

### 16.4 Receiver angular dan tampilan

Decoder harus menolak unknown version, ukuran salah, chunk count tidak masuk akal, nilai NaN/infinite, field di luar range, dan config mismatch. Assembly parsial tidak dirender sebagai kurva lengkap.

Saat gap frame, tampilkan curve terakhir dengan age dan status, bukan fill data menggunakan current DoA. Saat stream dipause untuk command, beri banner yang menjelaskan alasannya. Mode raw diagnostic dan mode LIVE harus dibedakan secara visual dan pada API.

<a id="bagian-17"></a>
## 17. Service, boot, dan recovery

### 17.1 Susunan service usulan

| Host | Unit | Fungsi | Hubungan penting |
|---|---|---|---|
| Raspberry | `t900-ppp.service` | Link PPP yang sudah disiapkan | Jangan digabung dengan lifecycle RDF |
| Raspberry | Service stack SDR yang diverifikasi | DAQ/DSP dan GUI engine existing | Nama `sdr-doa.service` masih perlu cek deployment |
| Raspberry | `rdf-edge.service` | Adapter, state store, scheduler, MQTT, local API | Tetap hidup ketika SDR/Ground mati |
| Raspberry | `rdf-control-helper.service` | Privileged action allowlist | Socket lokal; tidak punya listener jaringan |
| Raspberry | Kiosk/display user service | Browser 5 inci | Kegagalannya tidak memutus telemetry |
| Ubuntu | `t900-ground-ppp.service` | Link Ground | Tidak tergantung pada UI |
| Ubuntu | Mosquitto service | Broker | Listener/firewall siap pada PPP reconnect |
| Ubuntu | Ground backend service | Subscriber, command controller, state/API | Berjalan tanpa browser |
| Ubuntu | Browser Ground | UI operator | Bisa ditutup tanpa mematikan broker |

Tidak menggunakan `Requires=sdr-doa.service` atau `BindsTo=t900-ppp.service` untuk mematikan edge ketika engine/link down. Urutan `After=` bukan jaminan readiness aplikasi; agent tetap harus menangani source unavailable dan reconnect.

### 17.2 Boot sequence usulan

```text
OS boot
  +-> PPP service mulai/retry saat T900 tersedia
  +-> restricted helper siap
  +-> edge agent hidup -> local API/health tersedia
  +-> supervisor membaca persisted desired state
  |      +-> RUNNING: start RDF stack lalu verifikasi readiness
  |      +-> STOPPED: jangan start RDF diam-diam
  +-> graphical session -> browser kiosk
  +-> ketika PPP + broker tersedia: MQTT handshake/state/config
  +-> health lalu DoA; angular paling akhir
```

Local API tidak menunggu MQTT connected untuk menampilkan halaman. PPP tidak menunggu DAQ healthy untuk membuat jaringan. RDF tidak menunggu browser untuk memproses.

### 17.3 Service template edge, bukan script instalasi

Contoh ini berlaku **setelah** executable, system user, direktori, hak baca source, dan helper diimplementasikan. Jangan menjalankannya sebagai bukti bahwa software sudah tersedia.

```ini
[Unit]
Description=RDF Edge Agent and Local Status API
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=exec
User=rdf-edge
Group=rdf-edge
WorkingDirectory=/opt/rdf-edge
ExecStart=/opt/rdf-edge/venv/bin/python -m rdf_edge --config /etc/rdf-edge/config.yaml
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
StateDirectory=rdf-edge
RuntimeDirectory=rdf-edge
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
UMask=0077

[Install]
WantedBy=multi-user.target
```

Hak baca `_share` harus diberikan secara spesifik. Jangan `chmod -R 777`. Writer konfigurasi dan operasi root berada di helper dengan policy yang ketat. Browser dan MQTT parser tetap unprivileged.

`Type=exec` membantu service manager mendeteksi kegagalan memulai executable, tetapi tidak membuktikan source/MQTT/DAQ ready. `Type=notify` dan watchdog native dapat ditambahkan hanya setelah aplikasi benar-benar mengimplementasikan notifikasi yang sesuai. [E07]

### 17.4 Recovery policy

| Kegagalan | Tindakan |
|---|---|
| RF/PPP putus | PPP retry; agent tetap collecting; jangan restart SDR hanya karena link |
| Broker down | MQTT backoff; panel lokal tetap hidup |
| Bulk connection macet | Reset bulk/assembly, pertahankan control |
| Agent crash | Service manager restart agent; journal direconcile |
| Browser crash | Restart browser saja |
| SDR crash saat intent RUNNING | Recovery stack terbatas; publish error dan attempts |
| SDR stop karena operator | Tidak auto-restart |
| Recovery berulang gagal | Stop loop agresif; state ERROR dan perlu operator |
| Disk penuh | Batasi logs/bulk; tolak mutasi jika journal intent tidak durable |

Status `active (exited)` pada launcher lama tidak membuktikan child DSP/DAQ sehat. Probe harus menggabungkan service/process, frame, sync, dan freshness. [S04, bagian 17; S09, bagian 7]

<a id="bagian-18"></a>
## 18. Security, privilege, dan audit trail

### 18.1 Role dan arah akses

| Principal | Publish | Subscribe | Tidak boleh |
|---|---|---|---|
| UAV control agent | DoA, health, state, config reported, ACK milik node | Command node dan Ground receipt | Publish command untuk node lain |
| UAV bulk agent | Angular/diagnostic milik node | Kontrol stream internal yang dibatasi jika diperlukan | Mengambil hak reboot/control hanya karena bisa kirim grafik |
| Ground viewer | Tidak ada command RF | Data node yang diizinkan | Config patch, lifecycle, diagnostic sensitif |
| Ground operator backend | Config/get/patch dan lifecycle yang diizinkan | Telemetry/ACK/state | Operasi maintenance tanpa role |
| Ground maintainer backend | Operasi maintenance sesuai policy | Data/diagnostic yang diizinkan | Arbitrary shell atau file path |
| Kiosk Raspberry | Read-only local snapshot default | Local API | Broker secret, root shell, raw settings |

Mosquitto ACL harus menegakkan arah, bukan hanya mengandalkan tombol frontend disembunyikan. MQTT client ID adalah identitas sesi, bukan bukti authentication. Trust boundary paling sederhana adalah broker dan Ground backend terautentikasi yang dipercaya; threat model yang tidak mempercayai broker membutuhkan end-to-end authentication tambahan.

### 18.2 Helper lokal yang terbatas

Helper memiliki daftar operasi tetap: read runtime evidence, apply allowed config, set RDF desired state, restart stack yang telah terdaftar, dan execute reboot yang sudah digate. Ia mendengar Unix socket lokal dengan pemeriksaan peer identity/permission. Tidak ada bind TCP/HTTP pada root helper.

Perintah yang diterima berupa typed operation, bukan command line string. Helper memetakan operasi ke executable/argumen tetap dengan validasi sendiri, timeout, dan output limit. Jangan memakai `shell=True`, interpolasi shell, atau `sudo NOPASSWD: ALL` untuk mempermudah implementasi.

Executable helper, daftar allowed units, dan konfigurasi authorization dimiliki root dan tidak dapat diganti oleh user browser. Agent tidak diberikan akses umum ke semua service systemd.

### 18.3 Secret dan settings

Credential broker/TLS, private key, admin password, dan raw `.env` tidak masuk frontend assets, config reported, screenshots, raw diagnostics, atau log operasi. Ground secret tetap di backend, sesuai arah dokumentasi frontend project. [S08]

Safe settings snapshot memakai allowlist, bukan hanya menghapus field yang namanya mengandung `password`. Nama key yang tidak biasa masih dapat mengandung secret; unknown field tidak otomatis aman.

### 18.4 Bounds dan abuse resistance

Batas awal yang diusulkan: command JSON maksimal 2 KiB, config patch maksimal 16 field allowlist, satu mutation aktif, dua angular assemblies, umur operation request terbatas, diagnostic result maksimal 4 KiB sebelum chunking/approval khusus, dan rate limit command per user/node.

Ukuran yang besar atau rate berlebihan mendapat error terstruktur, bukan membuat agent allocate memory tanpa batas. Broker limits dan client queue limits dilengkapi bounds aplikasi. Jangan mengisi parameter maximum queue dengan nol tanpa memahami bahwa beberapa konfigurasi/library menafsirkan nol sebagai tanpa batas. [E03; E04]

### 18.5 Audit yang berguna

Catat operation ID, authenticated operator, request type, safe diff, expected/actual revision, acceptance time, execution start/end, proof, outcome, dan reason. Catat juga command yang ditolak/expired, bukan hanya sukses.

Audit intent untuk destructive command harus durable sebelum tindakan. High-rate telemetry tidak perlu difsync ke disk Raspberry setiap frame. Ground menyimpan telemetry yang diterima untuk riwayat, tetapi gap selama link hilang harus dicatat sebagai gap, bukan direkonstruksi seolah semua frame pernah diterima.

<a id="bagian-19"></a>
## 19. Log, settings penuh, dan diagnosis on-demand

### 19.1 Pemisahan tiga jenis informasi

**Health** adalah ringkasan operasional berkala yang selalu diprioritaskan.

**Event/audit** adalah perubahan penting dan hasil command; ringan, terstruktur, dan dapat disimpan.

**Raw log** adalah diagnosis detail yang tidak dikirim terus-menerus lewat T900. Log native dapat mengandung identifier atau secret dan harus diperiksa/redact. [S06, bagian 3-4; S09, bagian 1]

### 19.2 Request diagnostic yang aman

Gunakan enumerasi seperti `recent_sdr_errors`, `ppp_summary`, `safe_config_snapshot`, atau `native_doa_snapshot`. Jangan menerima `path=/etc/...`, `url=http://...`, atau command `journalctl` mentah dari payload Ground.

Respons mempunyai request ID, source, captured_at, source timestamp, size, redaction flag, truncation flag, dan checksum jika perlu. Transfer dipace dan boleh dibatalkan ketika health/command memerlukan kapasitas. Native snapshot tidak mengubah authority data LIVE.

### 19.3 SSH dan HTTP tetap tersedia dengan batas

SSH melalui IP PPP dapat dipakai operator berwenang untuk diagnosis singkat. Hindari `journalctl -f` tanpa batas, download log besar, package update, atau membuka GUI native secara terus-menerus melalui radio yang sama.

HTTP `:8081` tetap dapat menjadi alat read-only terkontrol, tetapi bukan alasan mengekspos semua `_share` secara bebas. Full settings inspection hanya melalui jalur terotorisasi dan tidak otomatis ditampilkan kepada semua viewer.

Jika butuh transfer file besar, gunakan management LAN yang nyata pada maintenance window, bukan menambah beban T900. Data melalui SSH/HTTP tetap memakai link yang sama; menyebutnya "manual" tidak membuatnya bebas bandwidth.

### 19.4 Retensi dan kesehatan storage

Gunakan rotasi log berbasis ukuran/waktu. Simpan command journal lebih kuat daripada log debug. Batasi jumlah export diagnostic dan jangan menumpuk snapshot gagal tanpa batas.

Proposal awal: telemetry history utama di Ground; Raspberry menyimpan ringkas operation journal dan rolling diagnostic buffer. Nilai retensi final harus menyesuaikan kapasitas media serta kebutuhan operasi, bukan hard-code asumsi SD card besar.

<a id="bagian-20"></a>
## 20. Struktur aplikasi dan konfigurasi usulan

### 20.1 Struktur source yang disarankan

```text
rdf-telemetry/
  README.md
  docs/
    architecture.md
    protocol-v2.md
    validation.md
  common/
    schemas/
    enums/
    fixtures/
  raspberry/
    rdf_edge/
      source_adapter.py
      normalization.py
      freshness.py
      state_store.py
      health_collector.py
      mqtt_control.py
      mqtt_bulk.py
      scheduler.py
      angular_codec.py
      command_manager.py
      config_manager.py
      lifecycle_adapter.py
      operation_journal.py
      local_api.py
    control_helper/
      policy.py
      actions.py
      server.py
    display/
      src/
      static/
  ground/
    integration/
      mqtt_consumer.py
      decoder_v2.py
      node_store.py
      operation_controller.py
      api_adapter.py
  tests/
    unit/
    integration/
    failure_modes/
    bandwidth/
  deploy/
    systemd/
    config-examples/
```

Ini **layout usulan**, bukan daftar file yang sudah ada. Jika repo existing menyediakan komponen setara, gunakan adapter/modul existing dan hindari duplikasi. Repo Ground sekarang perlu diaudit sebelum menentukan filename final.

### 20.2 Contoh konfigurasi deklaratif

```yaml
schema_version: 2
node_id: uav-01

source:
  share_dir: REPLACE_WITH_VERIFIED_SDR_SHARE_DIRECTORY
  doa_filename: DOA_value.html
  status_filename: status.json
  settings_filename: settings.json
  output_vfo: 0
  max_record_bytes: 8192
  require_authority_verified: true

telemetry:
  profile: balanced
  doa_hz: 1.0
  health_hz: 1.0
  health_detail_hz: 0.1
  angular_hz: 0.25
  angular_encoding: q16
  angular_chunk_bytes: 384
  angular_max_incomplete_frames: 2
  nav_enabled: false
  ground_receipt_seconds: 5
  state_refresh_seconds: 60

mqtt:
  host: 10.90.0.1
  port: 8883
  protocol: 5
  topic_prefix: sdr/v2/uav-01
  keepalive_seconds: 15
  clean_start: true
  session_expiry_seconds: 0
  tls_required: true
  verify_peer: true
  credentials_source: protected_backend_configuration
  control_client_id: uav-01-control
  bulk_client_id: uav-01-bulk

control:
  enabled: false
  one_mutation_at_a_time: true
  require_trusted_clock: true
  config_patch_enabled: false
  processing_control_enabled: false
  service_restart_enabled: false
  system_reboot_enabled: false
  reboot_requires_local_maintenance: true
  restore_processing_intent_after_boot: true

local_api:
  bind: 127.0.0.1
  port: 8790
  read_only_default: true

display:
  target_inches: 5
  assumed_width: 800
  assumed_height: 480
  orientation: landscape
  local_refresh_hz: 2
```

Flags command sengaja false sebelum capability, auth, dan acceptance test selesai. Mengubah YAML menjadi true saja tidak menciptakan fitur yang belum diimplementasikan. TLS certificate/identity tidak ditaruh dalam contoh.

### 20.3 Capabilities handshake

Pada connect, node melaporkan versi protocol/codec, supported operation, allowed fields/ranges yang sudah diuji, processing control mode, source authority status, angle convention, version/provenance, boot/agent identity, dan current profile.

Ground menampilkan hanya controls yang supported dan authorized. Capability tidak boleh berbunyi `reboot=true` hanya karena OS memiliki perintah reboot; flag itu berarti gate implementasi dan policy sudah lulus.

Capabilities dan settings lengkap bisa cukup besar. Publikasikan saat perlu, bukan 1 Hz, dan pertimbangkan ringkasan awal plus detail yang diambil bertahap. Bootstrap besar dihitung sebagai reconnect burst, bukan disembunyikan dalam steady-state.

<a id="bagian-21"></a>
## 21. Tahapan implementasi dan definition of done

### Fase A - Inventaris dan baseline read-only

Verifikasi source path, versi, service names, process topology, actual PPP route, MQTT availability, display resolution, dan health native. Simpan safe snapshot valid/stale/malformed untuk fixture. Jangan mengubah DAQ, port, atau algoritma hanya untuk mulai membuat dashboard.

**Selesai bila:** facts/assumptions terpisah, source CSV parser dapat diuji, serta tidak ada asumsi path `doasdr` yang keliru pada akun `rdf`.

### Fase B - Bridge read-only dan local display

Implementasikan adapter, state store, local health, dan panel 5 inci tanpa network publishing. Uji node tanpa Ground, tanpa RDF, dan tanpa browser. Ketika source gagal, panel harus menunjukkan sebab yang relevan.

**Selesai bila:** panel tidak mempengaruhi DAQ secara material pada pengukuran, field unknown tidak dipalsukan, dan bridge tidak bergantung pada browser.

### Fase C - MQTT synthetic dan budget

Pasang broker/auth/TLS secara terkontrol, buat synthetic publisher dengan label yang jelas, decoder Ground, receipt, dan scheduler. Uji arah UAV->Ground dan Ground->UAV bersamaan. Jangan menampilkan synthetic sebagai data RF asli.

**Selesai bila:** profile budget tercatat, reconnect tidak menumpuk history, dan QoS/application ACK dipisahkan.

### Fase D - DoA nyata dan angular 360

Aktifkan telemetry nyata hanya setelah authority/freshness/unit gate lulus. Tambahkan Q16/chunking, graph assembly, age label, config/source correlation, dan perbandingan grafik terhadap native local.

**Selesai bila:** 360 titik benar, error kuantisasi sesuai, stale/mixed frame ditolak, dan profile P1 lulus pengukuran latency.

### Fase E - Config get/patch dan sinkronisasi

Tambahkan persistent journal, authorization, revision conflict, safe diff, file/runtime distinction, dan single-writer. Mulai dari subset kecil frequency/gain/VFO yang proof-nya tersedia.

**Selesai bila:** duplicate/expired/conflict/malformed cases tidak menyebabkan perubahan tidak sah; applied hanya setelah proof.

### Fase F - Start/stop/restart stack

Implementasikan intent persisten dan koordinasi watchdog. Uji stop sengaja lebih lama dari interval watchdog. Jaga PPP, agent, dan local API tetap hidup.

**Selesai bila:** Stop tidak di-auto-start ulang, Start bukan toggle, restart stack tidak menjadi reboot OS, dan operasi bisa di-query setelah reconnect.

### Fase G - Reboot Raspberry

Implementasikan prepare/execute, interlock maintenance, recovery journal, expected outage, dan boot proof. Uji pada bench dengan akses lokal/management yang aman.

**Selesai bila:** command lama tidak bisa reboot setelah reconnect, double-submit tidak reboot dua kali, dan UI tidak menyatakan berhasil sebelum node benar-benar kembali dengan bukti.

### Fase H - Hardening dan soak

Uji beban gabungan, cold boot, USB hotplug T900, broker restart, RF interruption, display crash, disk pressure, dan running/stop policy. Simpan hasil pengukuran untuk memilih profile produksi.

**Selesai bila:** batas rate/queue/timeout berbasis data aktual, tidak ada restart loop, dan rollback/config backup tersedia. Tidak ada flight readiness otomatis dari lulus bench test.

<a id="bagian-22"></a>
## 22. Matriks pengujian minimum

| ID | Kasus | Hasil yang diharapkan |
|---|---|---|
| T01 | CSV 377 field valid | Parser menghasilkan metadata dan tepat 360 sampel |
| T02 | CSV parsial/non-atomic | Ditolak/retry terbatas; proses tidak crash |
| T03 | Timestamp lama, status baru | DoA STALE, health source tidak menipu LIVE |
| T04 | Timestamp future/clock skew | UNTRUSTED atau gate gagal; tidak clamp sembarangan |
| T05 | CSV/XML sudut berbeda | Normalisasi mengikuti convention, tidak merge asal |
| T06 | Confidence >1 atau negatif native | Tetap native metric, bukan auto-persentase |
| T07 | Array ada nilai negatif | Sign dipertahankan; tidak abs/log kedua |
| T08 | Q16 round-trip | Maks error <=0,00501 pada fixture dalam range |
| T09 | Q16 overflow/NaN/inf | Reject atau encoding change tervalidasi, bukan clip |
| T10 | U8 constant vector | Tidak divide-by-zero; hasil sesuai offset |
| T11 | Chunk hilang/duplikat/urutan terbalik | Assemble hanya frame lengkap; conflicting duplicate ditolak |
| T12 | Session/boot berubah saat assembly | Frame lama dibuang, tidak bercampur |
| T13 | Grafik aktif + command masuk | Bulk dipause; health/ACK mendapat prioritas |
| T14 | Broker offline | Local processing/panel tetap berjalan |
| T15 | Ground backend mati, broker hidup | Receipt hilang, indikator membedakan keduanya |
| T16 | Kiosk ditutup | PPP/agent/engine tetap hidup |
| T17 | Agent mati, browser masih terbuka | Browser menandai status source lost |
| T18 | Record native tidak berubah | Tidak republish sebagai snapshot baru |
| T19 | Duplicate command ID/payload sama | Hasil lama dikembalikan, action tidak diulang |
| T20 | Duplicate ID/payload berbeda | ID_REUSE_CONFLICT |
| T21 | Expired command | Ditolak tanpa perubahan |
| T22 | Command target boot lama | Ditolak walaupun broker mengirim ulang |
| T23 | Config base_rev lama | Conflict dan reported state, bukan overwrite |
| T24 | File berubah tetapi runtime tidak | PERSISTED_UNVERIFIED, bukan applied |
| T25 | GUI native menulis bersamaan | Conflict terdeteksi atau writer dicegah |
| T26 | Center berubah, VFO di luar passband | Ditolak atau kebijakan retune eksplisit |
| T27 | Stop RDF saat watchdog aktif | STOPPED bertahan; bridge/health tetap hidup |
| T28 | Start tanpa sinyal RF | DAQ ready + NO_DETECTION, tidak wajib detection palsu |
| T29 | Stack restart | Boot OS sama; engine generation baru |
| T30 | Reboot tanpa maintenance/confirmation | Ditolak |
| T31 | Execute memakai challenge expired | Ditolak |
| T32 | Reboot berhasil tetapi DAQ gagal setelah boot | Reboot selesai; RDF DEGRADED terpisah |
| T33 | Reboot result hilang/duplicate | Reconcile journal, tidak auto-reboot lagi |
| T34 | Viewer mencoba command | Backend/ACL menolak |
| T35 | Malicious path/shell/unknown op | Ditolak schema/helper |
| T36 | TLS cert invalid/node mismatch | Tidak fallback insecure |
| T37 | Broker restart dengan retained lama | State di-cache sebagai last-known, fresh handshake wajib |
| T38 | RF putus lalu kembali | Tidak burst history; state/health mendahului graph |
| T39 | Payload atau file melebihi cap | Ditolak/truncated secara jelas tanpa memory exhaustion |
| T40 | Disk penuh saat destructive intent | Mutasi ditolak bila journal tidak durable |
| T41 | USB T900 pindah port | Alias mismatch terlihat; tidak memilih random ttyUSB |
| T42 | CH340 lain di dedicated port | Tidak dianggap autentik node hanya berdasarkan VID/PID |
| T43 | 2 VFO diminta tanpa budget | Ditolak atau profile baru, tidak overload diam-diam |
| T44 | Renderer config baru memakai curve lama | Label previous config, tidak digabung sebagai satu snapshot |
| T45 | Display resolusi lebih kecil | Layout tetap terbaca, tidak memotong alarm penting |
| T46 | Nav disabled atau zero/zero placeholder | Tidak membuat posisi/bearing global palsu |
| T47 | Akumulasi retransmission | Rate turun; actual link degradation dilaporkan |
| T48 | Stop intent lalu cold boot | Restore intent sesuai policy, tidak start tak terduga |

### 22.1 Target performa yang diusulkan

Pada profil produksi terpilih, tetapkan target bench awal: P95 accepted latency <=2 detik; health tidak melewati 3 detik pada link normal; curve P1 complete age umumnya <=6 detik; tidak ada backlog yang terus tumbuh; dan dropped-frame behavior DSP tidak memburuk secara material saat display/bridge aktif.

Target apply setting/start/restart berbeda per operasi dan harus diukur terpisah. Jangan menyembunyikan kegagalan P95 dengan hanya menampilkan rata-rata. Bila target tidak lulus, turunkan rate bulk atau perbaiki bottleneck sebelum menambah fitur.

### 22.2 Prosedur pengukuran

Jalankan profil tanpa command, lalu dengan satu command per menit, lalu burst tuning terkontrol. Lakukan juga tes noise/loss/reconnect yang representatif dengan prosedur aman. Soak 30 menit lalu durasi lebih panjang sesuai kebutuhan adalah **rencana uji perangkat**, bukan klaim pengujian sudah dilakukan pada dokumen ini.

Simpan tabel hasil: profile, rate aktual, arah tx/rx, total byte, lost/incomplete angular frames, source drops, ACK P50/P95/P99, operation time, health/DoA/curve ages, CPU/RAM/temp, versi build, dan kondisi link. Lampirkan failed case, tidak hanya screenshot yang berhasil.

<a id="bagian-23"></a>
## 23. Runbook operasi dan diagnosis

### 23.1 Pemeriksaan tanpa mengganti konfigurasi

Contoh command read-only berikut dijalankan pada host yang sesuai. Ini tidak perlu dijalankan melalui T900 bila sudah ada akses lokal/management.

```bash
# Raspberry
systemctl is-enabled t900-ppp.service
systemctl is-active t900-ppp.service
readlink -f /dev/t900
ip -br addr
ip route get 10.90.0.1

# Ubuntu
systemctl is-enabled t900-ground-ppp.service
systemctl is-active t900-ground-ppp.service
readlink -f /dev/t900-ground
ip -br addr
ip route get 10.90.0.2
```

`enabled` dan `active` menjelaskan service manager, bukan otomatis semua data path sehat. Nama interface dapat berbeda jika host mempunyai link PPP lain; gunakan actual route/peer, jangan hanya menganggap selalu `ppp0`.

### 23.2 Sebelum mengaktifkan write command

Buktikan serial/PPP hanya dipegang proses yang benar, MQTT credentials/ACL bekerja, clock terpercaya, source parser/gate lulus, config allowlist/proof tersedia, dan watchdog memahami stop intent. Jangan mengaktifkan reboot hanya karena pub/sub contoh sudah berhasil.

### 23.3 Saat Ground tidak menerima

Urutan diagnosis: USB/alias -> PPP route/peer -> MQTT session/auth -> Ground backend receipt -> publication gate -> source DAQ/detection. Jangan memulai dari reboot Raspberry bila masalahnya ternyata backend Ground tidak subscribe topic versi yang benar.

Lihat local panel: jika MQTT CONNECTED tetapi GROUND RX LOST, periksa backend Ground dan receipt. Jika Ground menerima health tetapi curve stale, periksa gate, profile pause, chunks, source timestamp, dan revision; tidak perlu langsung mengubah radio.

### 23.4 Saat command tidak jelas hasilnya

Cari operation ID yang sama. Periksa stage terakhir, waktu, base/current revision, target boot, dan proof. Query journal setelah reconnect. Jika status unknown, tampilkan unknown dan reconcile; jangan menekan tombol restart berkali-kali sebagai retry buta.

### 23.5 Hal yang tidak dilakukan saat troubleshooting biasa

Jangan menjalankan `rtl_test` bersamaan dengan DAQ aktif, jangan kill semua `pppd`, jangan reset USB Kraken sembarangan, dan jangan restart service RDF melalui sesi PPP tanpa memahami dampaknya. Dokumen diagnosis project membedakan read-only diagnosis dari recovery yang memerlukan tindakan terkontrol. [S09]

<a id="bagian-24"></a>
## 24. Risiko, keputusan terbuka, dan batas validasi

| Risiko / ketidakpastian | Dampak | Mitigasi/keputusan awal |
|---|---|---|
| Kapasitas radio simultan belum diukur | Budget per arah bisa salah | Gunakan model agregat konservatif lalu ukur dua arah |
| P95 command terganggu array | Operator mengira tombol gagal | Chunking, separate bulk, pause, operation ID |
| Field confidence belum disepakati | Quality salah ditafsirkan | Native metric, tidak auto-probability |
| Source stale walau HTTP hidup | Data palsu LIVE | Timestamp+DAQ+authority gate |
| File/runtime settings berbeda | ACK sukses palsu | Proof levels dan runtime exporter/adaptor |
| GUI lama menulis config | Lost update | Single writer atau conflict detection yang nyata |
| Watchdog melawan Stop | RDF hidup lagi tanpa izin | Persistent desired state dan coordinated watchdog |
| Reboot saat link bermasalah | Reboot berulang/tidak jelas | Prepare/execute, TTL, boot binding, durable journal |
| Navigation belum ada | Bearing global tidak valid | Relative bearing saja; no fabricated GPS |
| Browser panel memakan resource | DSP drops/thermal | Frontend ringan dan pengukuran beban |
| UDP benchmark dianggap jaminan MQTT | Rate terlalu agresif | Layer pengukuran dijelaskan; TLS/app test wajib |
| Repo/code deployment belum tersedia lengkap | Endpoint/proof belum pasti | Inventaris dan audit source sebelum implementasi writes |
| Username/path arsip berbeda | Service gagal/menulis lokasi salah | Discover actual SDR_ROOT, jangan mass-rename |
| Sertifikat/koneksi TLS berulang | Burst reconnect memenuhi link | Backoff, session reuse bila didukung, bootstrap bertahap |

Keputusan yang dapat dipakai sekarang: satu broker Ground; independent edge agent; local 5-inch display; JSON ringkas untuk control/health; binary full360 untuk grafik; operasi mutating terserialisasi; remote reboot gated; profil P1 sebagai starting point. Resolusi layar, path source, range setting, dan bukti runtime tetap perlu ditemukan pada fase inventaris, bukan ditebak.

<a id="bagian-25"></a>
## 25. Ringkasan keputusan implementasi

**Komunikasi:** Pertahankan PPP yang sudah berhasil. MQTT membawa hasil olahan dan command, bukan raw IQ. Gunakan satu broker Ground dan pisahkan control/bulk dalam agent.

**Grafik:** Kirim semua 360 titik. Q16 mempertahankan jumlah arah dengan kuantisasi amplitudo 0,01; U8 tersedia sebagai trade-off untuk grafik lebih sering. Native CSV exact hanya on-demand.

**Rate awal:** DoA 1 Hz, health 1 Hz, detail 0,1 Hz, Q16 graph tiap 4 detik, Ground receipt tiap 5 detik, state event/60 detik. Rate yang lebih tinggi adalah profil opsional setelah pengujian.

**Control:** Config, start/stop, restart RDF, dan reboot Raspberry mempunyai command ID, deadline, boot binding, authorization, journal, dan bukti hasil. Reboot tidak diaktifkan pada tahap pertama.

**Sinkronisasi:** Raspberry adalah otoritas effective configuration. Ground menyimpan mirror dan draft. Requested, persisted, dan effective dipisahkan.

**Status:** Pisahkan host, link, broker, Ground receiver, engine, DAQ, detection, curve, clock, dan config. Tidak semua kondisi dapat direduksi menjadi satu lampu ONLINE.

**Layar 5 inci:** Read-only local status sebagai default, ringan, assets offline, dan tidak menambah trafik T900. Bridge tetap berjalan jika layar/browser mati.

**Validasi:** Estimasi ini bukan hasil uji MQTT perangkat. Pilihan final ditentukan oleh wire/app measurements dan kegagalan yang diuji, bukan sekadar grafik yang sudah muncul.

<a id="bagian-lampiran-a"></a>
## Lampiran A. Codec Q16 referensi yang sudah diuji secara lokal

Kode ini hanya encode/decode dan assembly fixture. Ia tidak menghubungi Raspberry, tidak publish MQTT, dan tidak mengganti parser/source gate/authorization. Production assembler masih harus menambahkan session/boot mapping, timeout, memory bound global, serta pemeriksaan freshness.

Hasil self-test pada environment penyusunan dokumen: header 48 byte; frame 768 byte; dua chunk masing-masing 396 byte; round-trip mempertahankan signed samples; error maksimum fixture sekitar 0,00494874; malformed frame, frame parsial, NaN, infinity, dan overflow ditolak. Ini **uji kode sintetis**, bukan uji radio atau akurasi RDF.

```python
"""Q16 reference codec for the proposed RDF v2 design; no network or device I/O.
Python 3.9+. This is a codec fixture, not a complete edge agent.
"""
import math
import struct
from typing import Iterable, Optional, Sequence

HEADER = struct.Struct('<4sBBHIIQIIBBHffHh')
CHUNK = struct.Struct('<IIBBH')
N = 360
FRAME_SIZE = HEADER.size + N * 2
INVALID_I16 = -32768


def _uint(value: int, bits: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(field + ' must be an integer')
    if not 0 <= value < (1 << bits):
        raise ValueError(field + ' outside unsigned range')
    return value


def _fixed(value: float, scale: float) -> int:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('non-finite value')
    q = round(value / scale)  # nearest, ties-to-even in this reference
    if not -32767 <= q <= 32767:
        raise ValueError('Q16 overflow; do not clip silently')
    return q


def encode_q16(values: Sequence[float], *, sid: int, seq: int,
               timestamp_ms: int, frequency_hz: int,
               config_rev: Optional[int], vfo: int = 0,
               convention: int = 1, flags: int = 0,
               doa_raw_deg: Optional[float] = None,
               confidence_native_db: Optional[float] = None) -> bytes:
    if len(values) != N:
        raise ValueError('exactly 360 angular samples required')
    sid = _uint(sid, 32, 'sid')
    seq = _uint(seq, 32, 'seq')
    timestamp_ms = _uint(timestamp_ms, 64, 'timestamp_ms')
    frequency_hz = _uint(frequency_hz, 32, 'frequency_hz')
    if frequency_hz == 0:
        raise ValueError('frequency_hz must be positive')
    rev = 0xffffffff if config_rev is None else _uint(config_rev, 32, 'config_rev')
    if config_rev == 0xffffffff:
        raise ValueError('0xffffffff is reserved for unknown revision')
    vfo = _uint(vfo, 8, 'vfo')
    if vfo > 15:
        raise ValueError('VFO must be in 0..15')
    convention = _uint(convention, 8, 'convention')
    flags = _uint(flags, 16, 'flags')
    if flags & ~0x1f:
        raise ValueError('undefined flags for this version')
    raw_a = 65535
    if doa_raw_deg is not None:
        a = float(doa_raw_deg)
        if not math.isfinite(a) or not 0 <= a <= 360:
            raise ValueError('native angle must be in 0..360')
        raw_a = round((a % 360) * 100) % 36000
    c = INVALID_I16 if confidence_native_db is None else _fixed(confidence_native_db, 0.01)
    q = [_fixed(x, 0.01) for x in values]
    header = HEADER.pack(b'RDF2', 2, 1, flags, sid, seq, timestamp_ms,
                         frequency_hz, rev, vfo, convention, N,
                         0.01, 0.0, raw_a, c)
    return header + struct.pack('<360h', *q)


def decode_q16(frame: bytes) -> dict:
    if len(frame) != FRAME_SIZE:
        raise ValueError('wrong Q16 frame length')
    (magic, version, encoding, flags, sid, seq, ts, freq, rev, vfo,
     convention, count, scale, offset, angle, confidence) = HEADER.unpack_from(frame)
    if (magic, version, encoding, count) != (b'RDF2', 2, 1, N):
        raise ValueError('unsupported magic/version/encoding/count')
    if flags & ~0x1f or vfo > 15 or freq == 0:
        raise ValueError('invalid metadata')
    if not math.isclose(scale, 0.01, rel_tol=1e-6) or offset != 0.0:
        raise ValueError('unexpected scale/offset for initial Q16 profile')
    if angle != 65535 and angle >= 36000:
        raise ValueError('invalid angle metadata')
    samples = struct.unpack_from('<360h', frame, HEADER.size)
    if INVALID_I16 in samples:
        raise ValueError('invalid sample sentinel in LIVE Q16 frame')
    return {
        'sid': sid, 'seq': seq, 'timestamp_ms': ts,
        'frequency_hz': freq, 'config_rev': None if rev == 0xffffffff else rev,
        'vfo': vfo, 'convention': convention, 'flags': flags,
        'doa_raw_deg': None if angle == 65535 else angle / 100,
        'confidence_native_db': None if confidence == INVALID_I16 else confidence / 100,
        'values': [offset + scale * q for q in samples],
    }


def split_frame(frame: bytes, data_bytes: int = 384) -> list:
    meta = decode_q16(frame)
    if not isinstance(data_bytes, int) or data_bytes <= 0:
        raise ValueError('positive chunk size required')
    count = math.ceil(len(frame) / data_bytes)
    if not 1 <= count <= 4:
        raise ValueError('reference supports at most four chunks')
    return [CHUNK.pack(meta['sid'], meta['seq'], i, count, len(frame))
            + frame[i * data_bytes:(i + 1) * data_bytes]
            for i in range(count)]


def join_chunks(chunks: Iterable[bytes]) -> bytes:
    """Fixture assembler. Production must additionally enforce boot/session and timeout."""
    key = None
    parts = {}
    seen = 0
    for raw in chunks:
        seen += 1
        if seen > 8 or len(raw) <= CHUNK.size or len(raw) > CHUNK.size + FRAME_SIZE:
            raise ValueError('chunk bounds exceeded')
        sid, seq, idx, count, total = CHUNK.unpack_from(raw)
        candidate = (sid, seq, count, total)
        if not 1 <= count <= 4 or not 0 <= idx < count or total != FRAME_SIZE:
            raise ValueError('invalid chunk header')
        if key is None:
            key = candidate
        if candidate != key:
            raise ValueError('mixed frame/session')
        data = raw[CHUNK.size:]
        if idx in parts and parts[idx] != data:
            raise ValueError('conflicting duplicate chunk')
        parts[idx] = data
    if key is None or set(parts) != set(range(key[2])):
        raise ValueError('incomplete frame')
    frame = b''.join(parts[i] for i in range(key[2]))
    meta = decode_q16(frame)
    if (meta['sid'], meta['seq']) != key[:2]:
        raise ValueError('inner frame identity mismatch')
    return frame


def self_test() -> None:
    values = [-24.123 + 8.0 * math.cos(math.radians(i - 137)) for i in range(N)]
    frame = encode_q16(values, sid=0x7a8b9c0d, seq=1245,
                       timestamp_ms=1790668800123, frequency_hz=433920000,
                       config_rev=7, flags=0x1f, doa_raw_deg=137.4,
                       confidence_native_db=8.27)
    assert HEADER.size == 48 and CHUNK.size == 12 and len(frame) == 768
    chunks = split_frame(frame)
    assert [len(x) for x in chunks] == [396, 396]
    assert join_chunks(reversed(chunks)) == frame
    assert join_chunks([chunks[0], chunks[0], chunks[1]]) == frame
    error = max(abs(a-b) for a, b in zip(values, decode_q16(frame)['values']))
    assert error <= 0.00501, error
    bad_cases = [frame[:-1], b'BAD!' + frame[4:]]
    for bad in bad_cases:
        try:
            decode_q16(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('malformed frame accepted')
    try:
        join_chunks(chunks[:1])
    except ValueError:
        pass
    else:
        raise AssertionError('incomplete frame accepted')
    for bad_value in (float('nan'), float('inf'), 1000.0):
        try:
            encode_q16([bad_value] * N, sid=1, seq=1, timestamp_ms=1,
                       frequency_hz=1, config_rev=None)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid samples accepted')
    print('PASS: 48-byte header, Q16 768 bytes, two 396-byte chunks')
    print('PASS: signed values, round-trip, duplicates, partial and malformed rejection')
    print('Maximum absolute quantization error:', round(error, 8))


if __name__ == '__main__':
    self_test()
```

<a id="bagian-lampiran-b"></a>
## Lampiran B. Kalkulator bandwidth referensi

Script berikut mereproduksi ukuran payload, profil steady-state, serta transaksi config pada bagian 9. Ia tidak memakai akses jaringan atau hardware. Ubah payload/rate/properties/overhead jika implementasi aktual berbeda.

Kolom `down` berarti UAV ke Ground dan `up` berarti Ground ke UAV pada script ini. Arah TCP ACK dihitung berlawanan dengan publish yang bersangkutan. Hop broker ke subscriber lokal Ubuntu tidak dihitung sebagai trafik radio kedua.

```python
"""Budget estimator only; no network, system configuration, or device I/O."""
import json, math
D={
 'doa': {'v':2,'sid':'7a8b9c0d','q':1245,'t':1790668800123,'f':433920000,'a':137.4,'c':8.27,'p':-54.2,'rev':7,'ok':1},
 'health': {'v':2,'sid':'7a8b9c0d','q':86,'t':1790668800123,'run':1,'daq':1,'drop':12,'age':280,'temp':61.4,'clk':1,'rev':7},
 'detail': {'v':2,'sid':'7a8b9c0d','t':1790668800123,'usb':5,'sync':[1,1,1],'ovr':0,'cpu':38.2,'mem':54.1,'disk':67.0,'throt':0,'uv':0,'tx':1012,'rx':219,'qd':0,'adrop':0,'clock_err_ms':80,'gps':0,'err':None},
 'nav': {'v':2,'sid':'7a8b9c0d','t':1790668800123,'lat':-6.9147,'lon':107.6098,'hdg':121.5,'src':1,'ok':1},
 'receipt': {'v':2,'sid':'7a8b9c0d','dq':1245,'hq':86,'aq':1238,'rev':7},
 'state': {'v':2,'sid':'7a8b9c0d','boot':'10aa2345-6789-4abc-9def-1234567890ab','t':1790668800123,'run':'RUNNING','daq':True,'cfg':7,'control':'READY','clock':'SYNCED'},
 'command': {'v':2,'id':'g01-00001234','sid':'7a8b9c0d','boot':'10aa2345-6789-4abc-9def-1234567890ab','issued_ms':1790668800123,'expires_ms':1790668815123,'base_rev':7,'op':'config.patch','changes':{'center_frequency_hz':433920000,'vfo0_frequency_hz':433920000}},
 'accepted': {'v':2,'id':'g01-00001234','sid':'7a8b9c0d','stage':'accepted','t':1790668800250,'rev':7},
 'applied': {'v':2,'id':'g01-00001234','sid':'7a8b9c0d','stage':'applied','t':1790668801800,'rev':8,'proof':'runtime','error':None},
 'config': {'v':2,'sid':'7a8b9c0d','rev':8,'t':1790668801800,'proof':'runtime','effective':{'center_frequency_hz':433920000,'gain_db':24,'vfo0_frequency_hz':433920000,'vfo0_bandwidth_hz':12500,'array':'UCA','method':'MUSIC'}},
}
TOP={'detail':'telemetry/health/detail','doa':'telemetry/doa','health':'telemetry/health','nav':'telemetry/nav','receipt':'ground/receipt','state':'state','command':'cmd/config/patch','accepted':'ack/config','applied':'ack/config','config':'config/reported','angular':'telemetry/angular'}
prefix='sdr/v2/uav-01/'
B={k:len(json.dumps(v,separators=(',',':'),ensure_ascii=True).encode()) for k,v in D.items()}
def vb(n):
 c=1
 while n>=128:n//=128;c+=1
 return c
def mqtt(n,k,qos=0,expiry=True):
 r=2+len((prefix+TOP[k]).encode())+(2 if qos else 0)+1+(5 if expiry else 0)+n
 return 1+vb(r)+r
# Steady connection, IPv4/TCP including 12 bytes timestamps, 8-byte PPP frame allowance,
# a TLS 1.3 record model of 22 bytes (AEAD 16-byte tag; no padding), no coalescing credit.
# 1 TCP ACK per PUBLISH data segment, q1 PUBACK and its ACK are separate model packets.
def cost(n,k,qos=0,tls=True,expiry=True):
 m=mqtt(n,k,qos,expiry)
 segments=math.ceil((m+(22 if tls else 0))/1448)
 forward=m+segments*(52+8)+(22 if tls else 0)
 reverse=segments*(52+8)
 if qos:
  reverse+=4+52+8+(22 if tls else 0)
  forward+=52+8
 return forward,reverse,m
print('EXACT EXAMPLES',B)
for k in ['doa','health','nav','receipt']:
 print(k,B[k],cost(B[k],k))

def profile(name,doa_hz=2,health_hz=1,nav_hz=0,angular_hz=.25,encoding='q16',chunks=1,tls=True):
 rows=[('detail',B['detail'],.1,0,'down'),('state',B['state'],1/60,1,'down'),('doa',B['doa'],doa_hz,0,'down'),('health',B['health'],health_hz,0,'down'),('nav',B['nav'],nav_hz,0,'down'),('receipt',B['receipt'],.2,0,'up')]
 arrn={'q16':768,'u8':408,'f32':1488,'raw':2286}[encoding]
 if angular_hz:
  part=arrn if encoding=='raw' else math.ceil(arrn/chunks)+12
  rows.append(('angular',part,angular_hz*chunks,0,'down'))
 down=up=payload=mq=0
 for k,n,hz,q,direction in rows:
  f,r,m=cost(n,k,q,tls=tls,expiry=k!='state')
  down+=hz*(f if direction=='down' else r)
  up+=hz*(r if direction=='down' else f)
  payload+=n*hz;mq+=m*hz
 # 60 bytes PPP LCP request/response pair per 5 s x2 endpoints = 24 B/s total;
 # allocate 12 B/s each direction as round planning allowance (not measured).
 down+=12;up+=12
 return {'name':name,'doa_hz':doa_hz,'health_hz':health_hz,'nav_hz':nav_hz,'angular_hz':angular_hz,'encoding':encoding,'chunks':chunks,'payload_kbps':payload*8/1000,'mqtt_kbps':mq*8/1000,'down_model_kbps':down*8/1000,'up_model_kbps':up*8/1000,'sum_model_kbps':(down+up)*8/1000,'sum_15pct_kbps':(down+up)*8/1000*1.15}
P=[
 profile('P0 CONTROL',doa_hz=1,angular_hz=0),
 profile('P1 BALANCED - default',doa_hz=1,chunks=2),
 profile('P1 NAV 1Hz',doa_hz=1,nav_hz=1,chunks=2,angular_hz=.125),
 profile('P2 FAST q16',doa_hz=2,chunks=2,angular_hz=.25),
 profile('P3 GRAPH q16',doa_hz=1,chunks=2,angular_hz=.5),
 profile('P4 GRAPH u8',doa_hz=1,chunks=1,encoding='u8',angular_hz=.5),
 profile('P5 FULL all',doa_hz=2,nav_hz=1,chunks=2,angular_hz=.5),
 profile('P6 RAW legacy',doa_hz=2,encoding='raw',angular_hz=.2),
 profile('P7 FRAG4',doa_hz=1,chunks=4),
]
for p in P:print(json.dumps(p))
# One command Ground -> UAV; application ACKs/config/state UAV -> Ground, QoS 1.
tr={};down=up=0
for k,d in [('command','up'),('accepted','down'),('applied','down'),('config','down'),('state','down')]:
 n=B[k];f,r,m=cost(n,k,1,expiry=(k not in ('state','config')))
 down+=f if d=='down' else r;up+=r if d=='down' else f
 tr[k]={'json':n,'mqtt':m,'fwd':f,'rev':r}
tr['total']={'down_bytes':down,'up_bytes':up,'total_bytes':down+up,'with_15pct_bytes':(down+up)*1.15,'per_min_kbps':(down+up)*1.15*8/60/1000,'per_10s_kbps':(down+up)*1.15*8/10/1000}
print('TRANSACTION',json.dumps(tr))
```

<a id="bagian-lampiran-c"></a>
## Lampiran C. Kode error dan aturan antarmuka

| Kode | Makna | Tindakan UI |
|---|---|---|
| `AUTH_REQUIRED` | Session login/authorization tidak valid | Jangan kirim ulang diam-diam |
| `FORBIDDEN_OPERATION` | Role tidak boleh menjalankan operasi | Tampilkan alasan, tidak menawarkan bypass |
| `INVALID_SCHEMA` | Payload/field invalid | Perbaiki request |
| `UNKNOWN_CAPABILITY` | Node tidak mendukung operasi/codec | Refresh capabilities |
| `CLOCK_UNTRUSTED` | Deadline tidak dapat divalidasi aman | Block mutation, tampilkan clock status |
| `EXPIRED` | Request terlalu lama | Tinjau ulang tindakan sebelum ID baru |
| `STALE_BOOT` | Command untuk boot sebelumnya | Refresh node state |
| `STALE_SESSION` | Target stream/control session tidak sesuai | Refresh handshake |
| `ID_REUSE_CONFLICT` | ID sama digunakan untuk isi lain | Error aplikasi, jangan execute |
| `REVISION_CONFLICT` | Config sudah berubah | Tampilkan diff |
| `BUSY` | Operasi mutating lain aktif | Tampilkan operation ID aktif |
| `OUT_OF_RANGE` | Nilai melanggar capability/range | Tampilkan batas yang valid |
| `SOURCE_UNVERIFIED` | Sumber/angle/unit belum authoritative | Jangan tampilkan LIVE |
| `RUNTIME_PROOF_MISSING` | File berubah tetapi runtime belum terbukti | PERSISTED_UNVERIFIED |
| `INTERLOCK_REQUIRED` | Maintenance/safety gate belum terpenuhi | Disable destructive command |
| `CHALLENGE_EXPIRED` | Confirmation challenge tidak valid lagi | Mulai prepare baru setelah operator meninjau |
| `JOURNAL_UNAVAILABLE` | Intent tidak dapat dicatat durable | Tolak destructive operation |
| `APPLY_FAILED` | Perubahan gagal menurut worker/evidence | Tampilkan reported actual state |
| `RETURN_TIMEOUT` | Node belum kembali setelah reboot window | OUTCOME_UNKNOWN, jangan auto-reboot lagi |
| `BUDGET_EXCEEDED` | Rate/transfer melampaui policy | Turunkan rate atau gunakan maintenance LAN |

Tidak semua error menjadi alert merah. Contoh BUSY dan REVISION_CONFLICT adalah kondisi operasi yang dapat dijelaskan. Source STALE tidak sama dengan permission error, dan keduanya tidak boleh disembunyikan sebagai disconnected generik.

<a id="bagian-lampiran-d"></a>
## Lampiran D. Brief implementasi untuk pengembang/AI coding

```text
Tujuan:
Bangun RDF edge agent independen di Raspberry, telemetry MQTT dua arah melalui
PPP/T900, decoder/controller Ground, serta local status dashboard 5 inci.
Gunakan dokumen ini sebagai design proposal, bukan bukti file/endpoint sudah ada.

Sebelum mengubah kode:
1. Inventaris repo, branch, dependency, path runtime, dan service existing.
2. Bedakan data/source aktual dari dokumentasi historis dan contoh sintetis.
3. Jangan ubah PPP/udev yang sudah berhasil tanpa alasan dan test terpisah.
4. Jangan mengganti algoritma DSP atau menghentikan GUI engine hanya untuk UI baru.

Urutan:
- Buat fixture, parser, canonical state, dan freshness/authority tests.
- Bangun local read-only status API dan kiosk ringan.
- Bangun MQTT synthetic publisher/consumer dengan auth/TLS dan rate budget.
- Tambahkan codec full360 Q16, chunk assembly, dan profile P1.
- Hubungkan data nyata setelah gate lulus.
- Tambahkan config read/report, lalu write dengan revision dan runtime proof.
- Tambahkan lifecycle dengan desired state yang dipahami watchdog.
- Reboot paling akhir: prepare/execute, interlock, persistent journal, boot proof.

Invariants:
- Bridge dan health tetap berjalan ketika RDF STOPPED.
- Browser/kiosk bukan pengendali kehidupan service.
- MQTT PUBACK bukan acknowledgement hardware applied.
- Command tidak retained, tidak arbitrary shell, tidak blind replay.
- Confidence native bukan probability sampai mapping disetujui.
- Grafik memakai 360 samples asli yang diencode, bukan dibuat dari current DoA.
- Full settings/secret/log tidak dipublish mentah.
- Field unavailable tetap unknown/null; tidak membuat fix GPS atau radio RSSI palsu.
- Tidak menempelkan revision baru pada source snapshot lama.
- Tidak mengeksekusi remote write/restart/reboot pada perangkat hanya untuk test UI.

Output tiap fase:
- Daftar file berubah dan alasannya.
- Test yang dijalankan, hasil nyata, dan keterbatasan environment.
- Ukuran payload/rate aktual serta perubahan terhadap budget.
- Hasil failure-mode tests yang relevan.
- Migration dan rollback yang tidak merusak pipeline existing.

Jangan menyatakan selesai hanya karena build lolos atau dashboard terlihat normal.
Definition of done mencakup data integrity, operation proof, bounded queues,
resource impact, dan reconnect behavior.
```

<a id="bagian-lampiran-e"></a>
## Lampiran E. Sumber dan provenance

### E.1 Dokumen project

Referensi section/heading di bawah menunjuk ke dokumen sumber yang tersedia pada project. Dokumen tersebut tidak ditulis ulang atau diganti oleh rancangan ini. Metadata SHA-256 disertakan untuk mencocokkan snapshot bahan kajian, bukan membuktikan versi source code runtime di Raspberry.

**[S01] `Dokumentasi_Implementasi_T900_PPP_KrakenSDR_v2.md`**  
Ukuran snapshot: 19,722 byte. SHA-256: `d8e496ca7bc3668ccced2b3a043e466457f3619d136f245f11272a66a42b723f`.

**[S02] `Dokumentasi_Instalasi_KrakenSDR_V1_Raspberry_Debian13.md`**  
Ukuran snapshot: 21,936 byte. SHA-256: `9e2d22342f9005ece536c98eeefa7336d9efa028e09274c0b8ac6e4283507dc4`.

**[S03] `Dokumentasi_KrakenSDR_T900_PPP_MQTT(1).md`**  
Ukuran snapshot: 38,380 byte. SHA-256: `756a8a71734c63d12d527677c7f3bddba7d09c634caf7b4d642dee202d1497a6`.

**[S04] `Dokumentasi_Service_SDR_T900_PPP_Autostart.md`**  
Ukuran snapshot: 17,044 byte. SHA-256: `c5b8a5ba0ff5768f3125bdd8aae0677bca5208aa9020fda8df92ddda4b691b31`.

**[S05] `Dokumentasi_Throughput_T900_PPP_dan_Payload_SDR.md`**  
Ukuran snapshot: 12,724 byte. SHA-256: `666c034a37d45b9f543a12e66d3c155e22567429f6eb02ba79629455efa76003`.

**[S06] `SDR_DOA_8081_DATA_REFERENCE.md`**  
Ukuran snapshot: 13,899 byte. SHA-256: `545f2e102ea9739b629e36005d6580318e965d9e09d41a83de1be8d33646457f`.

**[S07] `SDR_DOA_DATA_ACCESS_AND_GUI_CUSTOMIZATION.md`**  
Ukuran snapshot: 26,270 byte. SHA-256: `d404a661d4f9d3d3032fca68c23cae85bcaffd6a1815d97dfb218b4b6ff8a8b3`.

**[S08] `SDR_DOA_FRONTEND_WORKFLOW.md`**  
Ukuran snapshot: 3,805 byte. SHA-256: `e1fb0c2c8301d5ef225139cc022cdc5726901ce8d353aa2d3adc063312f318bd`.

**[S09] `SDR_DOA_RASPBERRY_READONLY_DIAGNOSTICS.md`**  
Ukuran snapshot: 24,313 byte. SHA-256: `df509eaa398f20ff8726698bbdf41a04edeb6870b51677f5124a76e8d5cc7b01`.

**[S10] `SDR_DOA_TELEMETRY_ARCHITECTURE_AND_SETTINGS_CONTROL.md`**  
Ukuran snapshot: 24,849 byte. SHA-256: `265025f9b3ebae86c8831495fda23853c90d4ec9184c9111570dadbf88c60997`.

**[S11] `SDR_DOA_UPSTREAM_ARCHITECTURE.md`**  
Ukuran snapshot: 32,919 byte. SHA-256: `b98dbf28a1abf7131dab764d62b48b44bd1dfb77b0ee45858b05c1a784dfa491`.

Pemetaan bagian sumber utama:

- [S01] Bagian 20-28: PPP, ping, dan SSH historis; bukan hasil pengujian MQTT sekarang.
- [S03] Bagian 6-7: PPP/IP point-to-point dan perbedaannya dengan LAN Ethernet.
- [S04] Bagian 3-7: alias USB dan PPP service; bagian 17 dan 19-22: launcher/watchdog.
- [S05] Bagian 3-7 dan 26-28: metode UDP benchmark, budget, dan batas klaim pengujian.
- [S06] Bagian 3-8: ukuran snapshot, CSV 377 field, angular array, health, dan perbedaan format.
- [S07] Bagian 4-9 dan 11-13: pembacaan file, unit/orientasi, middleware, dan batas perubahan GUI.
- [S08] Seluruh dokumen: Ground frontend Python/React dan monitor subscriber-only.
- [S09] Bagian 1, 4, 7, dan 11: read-only diagnostics, enumeration versus DAQ health, service versus frame.
- [S10] Bagian 2-11: architecture, rate, config transaction, security, dan command yang belum diimplementasikan.
- [S11] Bagian 2, 4, 5, 7, dan 12: coupling engine/UI, DSP, settings watcher, output convention, dan gap runtime.

[C1] adalah percakapan pengguna tanggal 29 September 2026: output udev kedua host, keberhasilan picocom dua arah, laporan IP PPP terkoneksi, penyiapan service, kebutuhan grafik 360 titik, command, dan layar 5 inci. Tidak ada transcript capture MQTT baru atau cold-boot test baru yang menjadi dasar dokumen ini.

### E.2 Rujukan primer eksternal

Diperiksa pada 29 September 2026. Rujukan ini mendukung fakta protokol/library, bukan membuktikan versi paket yang terpasang pada host pengguna. URL ditulis sebagai teks kode agar mudah disalin dan tetap berguna dalam file Markdown mandiri.

**[E01] OASIS MQTT Version 5.0, OASIS Standard.** Acuan message/session expiry, PUBLISH framing, dan payload. Policy aplikasi dalam dokumen ini tetap merupakan usulan tersendiri.

```text
https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html
```

**[E02] OASIS MQTT Version 3.1.1.** Acuan semantics QoS dan perbedaan delivery protocol dengan hasil tindakan aplikasi.

```text
https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html
```

**[E03] Eclipse Mosquitto, mosquitto.conf manual.** Acuan queue, listener, dan parameter broker; nilai final harus mengikuti versi deployment.

```text
https://mosquitto.org/man/mosquitto-conf-5.html
```

**[E04] Eclipse Paho MQTT Python, client documentation.** Acuan client loop, publish lifecycle, queue limits, dan reconnect behavior.

```text
https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
```

**[E05] IETF RFC 9293, TCP.** Acuan stream berurutan dan retransmission; dasar mengapa QoS 0 tetap dapat mengalami backlog TCP.

```text
https://www.rfc-editor.org/rfc/rfc9293.html
```

**[E06] IETF RFC 1662, PPP in HDLC-like Framing.** Acuan frame/escaping; ukuran model bukan pengganti capture aktual.

```text
https://www.rfc-editor.org/rfc/rfc1662.html
```

**[E07] systemd, source manual systemd.service.** Acuan type/service lifecycle. Halaman web manual utama tidak dapat diambil saat pemeriksaan; source XML resmi repository systemd dipakai.

```text
https://raw.githubusercontent.com/systemd/systemd/main/man/systemd.service.xml
```

**[E08] IETF RFC 8446, TLS 1.3.** Acuan record layer; contoh overhead 22 byte adalah model dengan tag 16 byte dan tanpa padding, bukan nilai universal semua cipher/versi.

```text
https://www.rfc-editor.org/rfc/rfc8446.html
```

**[E09] Eclipse Mosquitto, Authentication Methods.** Acuan autentikasi broker dan pilihan mekanismenya.

```text
https://mosquitto.org/documentation/authentication-methods/
```

**[E10] Eclipse Mosquitto, Replacing per_listener_settings.** Acuan perbedaan konfigurasi antar-versi; tidak dijadikan alasan mengasumsikan versi 2.1 terpasang.

```text
https://www.mosquitto.org/documentation/listeners/per-listener-settings/
```

### E.3 Apa yang diuji ketika menyusun dokumen

Payload contoh dihitung dengan serializer Python. Model budget dijalankan dan tabel dihasilkan dari output model yang sama. Codec Q16/chunking diuji dengan fixture sintetis dan sejumlah invalid cases. Struktur Markdown, code fences, JSON contoh, YAML contoh, dan tautan daftar isi diperiksa secara lokal.

Yang **tidak** diuji di sini: perangkat Raspberry/Ubuntu pengguna, link RF/T900, broker pengguna, akurasi pengukuran RDF, range/latency lapangan, cold boot host, remote write settings, start/stop nyata, maupun reboot nyata. Tidak ada klaim semua source code aplikasi telah diaudit, karena yang tersedia adalah dokumen referensi project.

---

**Akhir dokumen.** Rancangan ini dapat menjadi dasar implementasi bertahap dan kontrak antara agent Raspberry, backend Ground, serta dua dashboard, dengan keputusan teknis yang masih perlu diverifikasi tetap ditandai secara eksplisit.
