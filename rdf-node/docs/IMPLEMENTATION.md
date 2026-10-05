# Implementasi RDF Node 1.0.0

## Arsitektur yang benar-benar dibuat

```text
Native SDR _share
   -> Source parser + provenance/freshness gates
   -> Agent cached snapshot
       -> HTTP loopback + static 480x320 panel
       -> Scheduler -> MQTT control / MQTT bulk -> T900 PPP

Ground MQTT consumer
   -> validate normal telemetry session/config/health
   -> decode authoritative DoA + assemble angular chunks
   -> keep diagnostic DoA on a separate UNVERIFIED path
   -> local API + preview graph + diagnostic display
   -> receipt only for accepted health/DoA/angular sequences

Authenticated command
   -> CommandManager -> SQLite journal -> single worker
   -> restricted Unix helper -> fixed approved settings/unit action
   -> verify evidence -> result/report
```

### Isolation

Panel dan MQTT tidak menjalankan receiver SDR kedua. Browser tidak membaca file native,
tidak menerima password tersimpan dari API, dan tidak memiliki hak root. Edge tetap hidup bila
SDR, helper, PPP, atau Ground gagal. Engine GUI lama tidak dihentikan: pada deployment tertentu
proses itu juga menjalankan signal processor.
`Data > Atur` hanya menerima perubahan melalui session admin+CSRF. Broker settings disimpan di
`/var/lib/rdf-node/mqtt-ui-settings.json`; credential CTRL/BULK disimpan terpisah pada
`/var/lib/rdf-node/mqtt-ui-control.json` dan `mqtt-ui-bulk.json`, mode 0600.
MQTT mendukung TCP/TLS, TCP biasa, WSS, dan WebSocket biasa. TLS aktif secara default serta
memverifikasi rantai sertifikat dan hostname; tanpa CA khusus, trust store OS digunakan.
Tanpa kredensial terkonfigurasi, client memakai anonymous CONNECT; pasangan kolom kosong mempertahankan kredensial per-channel yang sudah tersimpan.
Transport tanpa TLS mengirim credential dan payload tanpa enkripsi; gunakan hanya pada link privat
yang disetujui dan tepercaya.
`Data > Atur` menyediakan pilihan transport/TLS, host, port, path WebSocket, serta username dan
password CTRL/BULK terpisah. Keyboard sentuh melayani kolom teks; selector native tetap dapat
dioperasikan dengan keyboard. Save mengganti kedua client tanpa restart service, menghapus bukti
receipt/sequence lama, lalu menunggu bukti Ground baru sebelum bulk berjalan kembali.

Runtime menggunakan `/usr/bin/python3`, bukan Conda `base` atau environment SDR.
Local HTTP server stdlib memiliki client/body caps, Host/Origin checks, cookie admin,
CSRF, no-store dan CSP. Tidak boleh dibind ke LAN melalui perubahan tidak terkontrol.
Admin PIN session memakai idle timeout rolling (default 600 detik). Request dengan session valid dan ping panel saat ada interaksi baru memperpanjang sesi; polling snapshot publik tidak.

### Status error dan pemulihan UI

Edge panel dan Ground companion memisahkan API offline dari data yang hanya stale. Keduanya
mempertahankan snapshot terakhir yang sudah dirender dengan penanda `STALE`; tombol `Coba lagi`
memulai fetch segera, dan polling periodik tetap aktif dengan satu request in-flight. Ground
membedakan kegagalan snapshot dari kegagalan API grafik angular. Tidak ada restart service,
SDR, reboot, atau shutdown otomatis sebagai respons error.

### Shutdown OS

`system.shutdown.prepare/execute` adalah capability terpisah yang mati secara default pada
config dan helper. Jalurnya memakai command manager/jurnal yang sama dengan Ground, tetapi
helper hanya menjalankan aksi systemd tetap setelah maintenance lease dan challenge lokal.
Helper menyimpan intent shutdown durable dan hanya menjadwalkan aksi systemd tetap. Intent
current-boot menolak prepare/execute berikutnya sehingga tidak ada jadwal poweroff ganda;
pemeriksaan helper setelah boot baru menghapus intent boot terdahulu. Hasil schedule tetap
bukan bukti poweroff. Timeout/ambiguous response atau sesi baru menurunkan status menjadi
`OUTCOME_UNKNOWN`, yang tetap terlihat lintas sesi lewat ringkasan jurnal tanpa batas latest-20.
Kedua panel memuat ringkasan ini secara berkala dan sebelum execute; lost response Ground
dihubungkan memakai operation ID yang dibuat klien. Tidak ada retry otomatis.
`sudo rdf-node controls shutdown-reconcile` adalah jalur lokal root untuk intent yang
ditinggalkan: helper memeriksa unit timer/service yang tersimpan melalui `systemctl show` dan
hanya membersihkan saat keduanya tidak aktif; unit aktif atau status tidak diketahui tetap
memblokir. Tes menggunakan mock systemd dan tidak menjalankan poweroff nyata.

## Perbedaan implementasi terhadap dokumen planning

Perubahan berikut eksplisit, bukan klaim bahwa semuanya identik dengan contoh planning:

1. **MQTT:** client project (`mqtt.py`) menghindari pip untuk instalasi offline. Ia mendukung TLS
   terverifikasi pada TCP atau WebSocket, serta TCP/WebSocket tanpa TLS bila `tls: false` dipilih.
   Plaintext mengirim credential dan payload tanpa enkripsi; gunakan hanya pada link privat tepercaya.
   TLS memakai minimum 1.2, verifikasi rantai sertifikat dan hostname, trust store OS secara default,
   atau `ca_file` untuk CA privat. WebSocket memakai RFC 6455, subprotocol `mqtt`, binary frames,
   penanganan fragment/control frame, dan batas input. Ini BUKAN implementasi MQTT universal
   tersertifikasi; tidak ada QoS 2, enhanced auth, persistent offline session, topic alias, atau
   fallback MQTT 3. Fixture independen menguji protokol; interoperabilitas Mosquitto tetap acceptance
   gate perangkat. Untuk penggunaan kritis, audit client atau ganti dengan library terpelihara setelah
   dependency tersebut dapat diprovisioning dan diuji.
2. **API/UI:** stdlib HTTP + HTML/CSS/JavaScript lokal, bukan FastAPI/TypeScript build.
   Ini alternatif library setara yang dibolehkan rencana. Tidak ada development server.
3. **In-flight:** satu publish aktif per koneksi, lebih konservatif dari plafon rencana 4.
   Queue aplikasi sampai 16 pesan control atau 2 bulk dan 16 KiB, latest-value keyed.
4. **Snapshot lokal:** collector membentuk snapshot setiap 250 ms; browser mengambilnya
   setiap 500 ms. Status native dibaca 500 ms dan config 1 detik.
5. **Capabilities/report:** field lebih lengkap daripada contoh budget planning, termasuk
   instance/boot, bukti config, dan dukungan command. Ukuran pesan saat ini harus dihitung
   aktual; angka historis 9.14 kbit/s bukan hasil ukur software ini.
6. **Clock:** provider MVP menggunakan status time sync OS. Tidak ada provider GPS time,
   model uncertainty/holdover, atau time sync RF otomatis. Unknown menutup write/live gate.
7. **Lifecycle:** kontrol yang disediakan ialah unit stack SDR terpilih, bukan API DSP-only
   yang belum terbukti tersedia. Helper menangani stop intent dan startup reconciliation,
   tetapi tidak menambahkan watchdog DSP otomatis baru. Recovery crash engine tetap
   bergantung pada unit yang diaudit; kegagalan tampil sebagai health/error.
8. **Settings:** hanya lima field allowlist di bawah. Algoritma/geometri/DAQ calibration
   tidak diubah oleh command generic. Permintaan lain ditolak sebagai unsupported.
9. **Display:** theme dan blank timeout didukung. Formulir admin MQTT menyediakan keyboard
   layar sentuh untuk host, port, client ID, Path WSS, dan credential. Brightness hardware,
   rotasi driver, touch calibration, compositor installation dan autologin OS tidak
   diimplementasikan karena hardware belum diketahui. Panel tetap dapat dioperasikan dengan
   mouse.
10. **Ground preview:** ditambahkan agar codec/receipt dapat segera diuji. Integrasi ke
    aplikasi Ground lama tetap memakai API/decoder; source lama tidak tersedia di ZIP.

## Source validity

CSV: 377 field, opsional trailing delimiter kosong. Exact 360 nilai finite; satu output VFO
harus tidak ambigu. Source timestamp, frequency dan revision-at-read dilacak. Re-reading
file tidak membuat q baru. Timestamp regresi ditolak sampai sumber/agent direkonsiliasi.

`doa.xml` diparse terpisah hanya untuk tampilan/relay diagnostik. `TIME` adalah Unix
milliseconds, `FREQUENCY` dalam MHz, dan `DOA` dipertahankan sebagai nilai raw. Diagnostik
tidak mengisi `detection`, mengubah gate angle/provenance, atau memenuhi syarat telemetry,
receipt, maupun control normal. XML hilang/rusak tidak dapat membuat source valid.

DAQ sehat memerlukan status fresh, daq_ok boolean, frame/sample-delay/IQ sync true, dan
kemajuan frame. Observasi frame pertama belum membuktikan progress. Counter turun/reset
memerlukan progress berikutnya. Missing field dianggap unknown, tidak dibuat hijau.

LIVE membutuhkan authority dan angle approval, clock, config attribution, DAQ dan freshness.
Tidak ada cara membuktikan NO_DETECTION dari file stale saja; panel menyebut NO_FRESH_DOA.
Array tetap native; PAPR tetap dB native; power bukan link RSSI dan bukan calibrated dBm.
GPS/altitude/SNR tidak diisi dengan angka dummy. Mode nav OFF.

## Konfigurasi dan bukti

`rev` mengikuti perubahan safe settings yang diamati. Ini revision mirror, bukan janji
bahwa setiap parameter runtime sudah diverifikasi. Data dikorelasikan terhadap safe digest
pada saat dibaca dan timestamp setelah config berubah. UI membedakan:

- unverified;
- source_correlated: output VFO baru cocok, belum semua parameter runtime;
- persisted_unverified: write sudah tersimpan, evidence belum cukup;
- runtime: field perubahan telah diverifikasi dari evidence yang diperlukan.

`CFG SYNCED` memerlukan runtime proof dan receipt fresh yang revision-nya cocok.
`REPORTED_SAME` bukan sinonim runtime applied. Single-writer approval tetap diperlukan;
atomic rename tidak menyelesaikan konflik semua writer otomatis.

## Indikator dan cadangan kapasitas

PPP UP diperoleh dari interface/address peer; USB present bukan bukti RF sehat.

Monitor host mengirim satu echo ICMP ke peer terkonfigurasi sekitar tiap 5 detik melalui
interface PPP yang terdeteksi. Tanpa interface yang cocok, ping tidak dikirim. `REPLY`,
`NO_REPLY`, `NO_INTERFACE`, dan `ERROR` terpisah dari `link.ppp`: balasan membuktikan
peer terjangkau; tanpa balasan ICMP bukan bukti PPP mati.

Sandbox systemd RDF edge mengizinkan `AF_NETLINK` agar `ip -j addr` dapat membaca interface.


MQTT CONNECTED/READY dibedakan dari Ground receipt. Receipt hq harus pernah terkirim dan
bertambah; receipt berulang tidak menyegarkan progress. Setelah 10 detik terlambat,
setelah 15 detik lost, belum pernah receipt berarti unconfirmed.

Panel `DATA / MQTT` menampilkan `link.mqtt_topic_delivery`, keyed by suffix topic keluar.
Entry menyimpan `state` (`PENDING`, `SENT`, `ERROR`), QoS, `confirmation`, `updated_ms`,
`sent_ms` terakhir berhasil, dan kode `error` yang aman. Entry yang belum ada berarti
belum ada percobaan publish sejak start atau reconfigure MQTT. Untuk QoS 0, `SENT` berarti
frame PUBLISH selesai ditulis ke socket lokal (`SOCKET_WRITE`); QoS 1 berarti broker
mengembalikan PUBACK. Keduanya bukan bukti Ground memproses data; receipt aplikasi tetap
terpisah di `link.ground`. Error seperti `MQTT_DISCONNECTED`, `OUTBOX_FULL`, dan
`PUBLISH_REJECTED_0X87` ditampilkan per topic; `sent_ms` tetap menunjuk sukses terakhir
jika percobaan terbaru gagal.

`telemetry/diagnostic/doa` memakai client Control, QoS 0, expiry 3 s, tanpa retention.
Saat `doa.xml` tersedia, Edge mengirim satu sampel setiap 3 s, termasuk saat isi sampel
tidak berubah dan terlepas dari hasil gate DoA normal. Cadence ini menjaga beban telemetri
periodik Control di bawah budget default bersama health dan DoA normal. Sequence `q`
bertambah tiap publish; timestamp sumber dan observasi tetap menunjuk pembacaan file yang
sama. Ground menyimpan dan menampilkan sampel terpisah sebagai `UNVERIFIED`; ini tidak
menambah `dq` atau menjadi detection normal.

Panel utama Edge membaca `diagnostic_doa` dari snapshot lokal dan polling API tiap 500 ms.
Snapshot agent mengikuti `source.poll_ms` (default 250 ms). Saat detection normal invalid,
panel menampilkan sudut raw dan frekuensi XML dengan label `UNVERIFIED`, umur sumber, dan
validation reasons. Detection valid tetap memakai sudut relatif normal.


Grafik dipause karena source invalid, command, receipt, token/backlog, atau profile CONTROL.
Token bucket adalah estimasi biaya aplikasi+allowance transport, bukan shaping seluruh
socket/kernel/radio. Shell SSH, download log, ping terus-menerus juga berbagi link.
QoS 0 tetap berjalan di TCP; data yang sudah masuk TCP tidak bisa ditarik kembali.

## Struktur source

`source.py`: parser/collector. `codec.py`: angular. `mqtt.py`: wire/network/outbox.
`agent.py`: orchestration/scheduling. `monitor.py`: bounded host probes.
`control.py`: validation/journal/workers. `helper.py`: privileged allowlist.
`api.py`: HTTP/auth. `ground.py`: receiver. `cli.py`: setup/doctor/demo/approval.
`web/`: panel dan preview. `deploy/`: unit/kiosk. `tests/`: self-contained acceptance fixtures.
