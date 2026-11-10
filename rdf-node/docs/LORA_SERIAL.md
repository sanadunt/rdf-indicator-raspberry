# MQTT lewat LoRa serial (RLS1)

Jembatan `run.py lora-bridge` membawa topik MQTT RDF Node lewat modul LoRa UART transparan, tanpa PPP
dan tanpa TCP/IP. Satu program yang sama berjalan di kedua sisi:

```
rdf-edge -> mosquitto Pi (127.0.0.1:1883) -> lora-bridge --role edge  ~~LoRa~~  lora-bridge --role ground -> mosquitto Ground -> Ground Console / rdf-ground
```

Topik, payload (byte demi byte), dan flag retain di broker Ground sama dengan yang dipublish Edge, jadi
consumer MQTT tidak perlu diubah. Command Ground (`cmd/...`) berjalan ke arah sebaliknya.

## Kenapa bukan PPP

PPP di LoRa membungkus MQTT dalam TCP/IP: header IP/TCP dan ACK TCP ikut lewat radio half-duplex,
retransmisi TCP salah membaca latensi radio sebagai paket hilang, dan LCP/keepalive memakai airtime terus.
RLS1 hanya mengirim isi pesan, terkompresi, dengan jatah byte sesuai kemampuan radio.

## Link yang terukur (2026-10-31/11-01)

Modul CH340 USB (1a86:7523) di kedua sisi, UART transparan 115200 8N1, 920–923 MHz. RTT 51–91 ms.
Frame 160 B tiap 120 ms sampai utuh (~1.2–1.5 KB/s); burst 2 KB kehilangan sekitar separuh, jadi buffer
modul ~1 KB tanpa flow control. Default jembatan: 1000 B/s, frame maks 200 B, burst maks 400 B.

## Format frame

Setiap frame: `header(9) | chunk | CRC-16 (2)`, lalu di-COBS-encode dan diakhiri `0x00`.

| Byte | Isi |
|---|---|
| 0 | magic `0xA1` |
| 1 | tipe: `1` DATA, `2` HEARTBEAT, `4` TIME (sinkron jam, lihat bawah); `3` dipakai frame TEST `lora-test` |
| 2–3 | nomor urut frame pengirim (uint16 BE), naik 1 per frame yang benar-benar dikirim |
| 4 | id pesan (uint8); fragmen satu pesan dan salinannya memakai id yang sama |
| 5 | id topik (tabel di bawah) |
| 6 | flag: bit0 retain, bit1 QoS 1, bit2 zlib, bit3 TURN (giliran bicara) |
| 7 | indeks fragmen |
| 8 | jumlah fragmen (1–255) |
| … | potongan payload |
| akhir | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) atas header + potongan, big-endian |

Payload dikompres raw deflate (`wbits -15`, level 9) dengan kamus bersama `ZDICT` di
`src/rdf_node/lora.py` bila hasilnya lebih kecil (flag zlib). Kamus itu bagian dari protokol: ubah hanya
bersama versi protokol baru.

HEARTBEAT (tiap 5 s dari kedua sisi) berisi `uptime_s` (u32), frame diterima (u32), error CRC (u16),
frame hilang (u16), dan jam dinding pengirim dalam ms (u64). Dari situ tiap sisi tahu kualitas link
arah sebaliknya dan selisih jam (`peer.clock_offset_ms`, termasuk latensi satu arah ~0.1 s).

Id pesan memakai satu penghitung untuk semua topik dan mulai dari nilai acak setiap jembatan start.
Penerima membuang pesan dengan (id, topik, jumlah fragmen) yang sudah utuh dalam 30 s terakhir (salinan).
Bila `uptime_s` heartbeat lawan turun, jembatan lawan baru restart: penerima melupakan daftar itu
(`rx.peer_restarts` naik), supaya pesan baru dengan id yang terpakai ulang tidak dianggap salinan. Begitu
juga bila `uptime_s` sama tetapi jam dinding lawan di heartbeat sudah maju ≥1 s: jembatan yang hidup kurang
dari satu periode heartbeat (mis. crash loop) mengirim `uptime_s` 0 tiap kali start. Bila heartbeat
pertama setelah restart hilang, id awal yang acak yang mencegah sebagian besar bentrokan.

### Id topik (tidak boleh diubah, hanya ditambah)

| Id | Topik (`sdr/v2/<node>/…`) | Arah | Prioritas | Umur maks | Kebijakan |
|---|---|---|---|---|---|
| 1 | `telemetry/health` | Edge→Ground | 2 | 6 s | terbaru menang, min. tiap 2 s (`--health-every`) |
| 2 | `telemetry/health/detail` | Edge→Ground | 4 | 15 s | terbaru menang, min. tiap 30 s |
| 3 | `telemetry/doa` | Edge→Ground | 1 | 3 s | terbaru menang, **dikirim 2×** (prioritas data tertinggi) |
| 4 | `telemetry/angular` | Edge→Ground | 5 | 8 s | terbaru menang, min. tiap 3.5 s (`--angular-every`) |
| 5 | `telemetry/diagnostic/doa` | Edge→Ground | 2 | 3 s | terbaru menang |
| 6 | `telemetry/diagnostic/angular` | Edge→Ground | 5 | 8 s | terbaru menang, min. tiap 3.5 s (`--angular-every`) |
| 7 | `state` | Edge→Ground | 0 | – | retained, terbaru menang |
| 8 | `capabilities` | Edge→Ground | 3 | – | retained, terbaru menang |
| 9 | `availability` | Edge→Ground | 0 | – | retained, terbaru menang |
| 10 | `settings/reported` | Edge→Ground | 2 | 30 s | antre, dikirim 2× |
| 11 | `ack/config` | Edge→Ground | 0 | 30 s | antre, dikirim 2× |
| 12 | `ack/operation` | Edge→Ground | 0 | 30 s | antre, dikirim 2× |
| 64–74 | `cmd/config/get`, `cmd/config/patch`, `cmd/processing/set`, `cmd/service/restart`, `cmd/service/ppp/restart`, `cmd/system/reboot/prepare`, `cmd/system/reboot/execute`, `cmd/system/shutdown/prepare`, `cmd/system/shutdown/execute`, `cmd/operation/get`, `cmd/stream/set` | Ground→Edge | 0 | 10 s | antre, dikirim 3× |

Umur maks menjaga data tetap di dalam jendela freshness Ground (health 8 s, DoA 5 s, Angular 10 s) dan
command di dalam deadline 15 s (`expires_ms`). Tidak ada retransmisi: telemetry digantikan yang lebih
baru, salinan command/ACK dibuang penerima (id pesan sama), dan `state`, `capabilities`,
`availability` dikirim ulang oleh jembatan Edge setiap kali link kembali naik dan tiap 120 s.
`availability` `STOPPED` dari `rdf-edge` yang berhenti dibawa jembatan bila jembatan tetap berjalan; saat
shutdown/reboot sistem jembatan berhenti tepat sesudahnya tanpa menguras antrean, jadi sampainya ke Ground
hanya best effort.
Salinan ACK berangkat 0.5 s setelah yang pertama. Command dikirim tiga kali: salinan kedua 0.5 s setelah
yang pertama, salinan ketiga 1 s setelah yang kedua, jadi jatuh di giliran Ground yang lain. Arah
Ground→Edge kehilangan lebih banyak frame (~13%), dan command yang hilang baru terlihat sebagai tidak ada
ACK 20 s kemudian; tiga kali kirim menurunkan kehilangan command satu frame dari ~1,7% ke ~0,2%.

## Giliran bicara (half-duplex)

Radio LoRa tidak bisa kirim dan terima bersamaan. Kira-kira sekali per detik Edge menandai satu frame
dengan flag `TURN` (bit3), lalu diam `0.45 s`. Jeda itu dihitung dari perkiraan saat frame `TURN` dan
semua frame yang ditulis ke modul sebelumnya selesai di udara (0.8 ms/byte, laju frame beruntun;
0.5 ms/byte dari frame tunggal terlalu optimis), bukan dari saat ditulis.
Ground hanya memancar dalam `0.08 s` setelah menerima frame `TURN` (maks. 250 B per giliran), atau bebas
bila Edge diam lebih dari 3 s. Frame TIME diberi timestamp saat benar-benar dikirim, jadi menunggu giliran
tidak merusak ukuran jam. Dengan kurva Angular tiap ~4.5 s dan perkiraan 0.5 ms/byte, ~92% frame
Ground→Edge hilang; dengan 0.8 ms/byte ~3% (2026-11-10). Terukur dengan DoA dan Angular berjalan (1.10.1): hilang ~4% Edge→Ground dan
~13% Ground→Edge (sebelumnya, dengan jeda 0.3 s dari saat tulis, ~62% Ground→Edge).

## Status jembatan

Setiap sisi mempublish status retained ke broker lokalnya: `sdr/v2/<node>/lora/edge` dan
`sdr/v2/<node>/lora/ground` (tiap 5 s), dan mencetaknya ke journal tiap 30 s:

```json
{"v":1,"role":"ground","t":1793500000000,"link":"UP","last_rx_ms":420,"port":"OPEN","port_error":null,
 "mqtt":"CONNECTED","rx":{"frames":812,"crc_errors":1,"bad_frames":0,"lost_frames":3,"messages":640,
 "duplicates":12,"incomplete":0,"heartbeats":160,"peer_restarts":0},"tx":{"queued":20,"superseded":0,"expired":0,
 "sent_messages":20,"sent_frames":200,"sent_bytes":2400,"too_large":0},"queue":0,
 "peer":{"uptime_s":812,"rx_frames":198,"crc_errors":0,"lost_frames":2,"clock_offset_ms":-35},
 "mqtt_dropped":0,"ignored":0,"link_changes":1}
```

`link` `DOWN` bila tidak ada frame selama 15 s. Jembatan tidak membuat `availability` palsu untuk Edge;
Ground melihat link putus dari topik ini dan dari health yang berhenti.

## Anggaran

Dengan kompresi: `health` lengkap (kompas + GPS) 232 B JSON → 126 B di udara; kurva Angular 2078 B →
861 B (5 frame). Perkiraan rata-rata: health ~65 B/s (tiap 2 s), DoA ~100 B/s, diagnostic DoA ~40 B/s,
detail ~8 B/s, Angular ~215 B/s (tiap 4 s) → sekitar 450 B/s dari 1000 B/s. Arah Ground→Edge hanya
command dan heartbeat, supaya tabrakan half-duplex tetap jarang.

Jembatan meneruskan tiap kurva Angular yang dibuat Edge (`--angular-every` 3.5 s, sedikit di bawah
`angular_s` 4 s profil `balanced`, supaya kurva yang datang sedikit lebih awal tidak tertahan). Edge
sendiri menjarakkan kurva `max(angular_s, ukuran kurva / telemetry.bulk_budget_bytes_s)`: dengan kurva
~2.1 KB dan `bulk_budget_bytes_s` 250 jaraknya ~9 s, dengan 500 ~4.5 s.

## Pemasangan

Hentikan PPP lebih dulu: port LoRa hanya boleh dibuka satu program.

**Raspberry (Edge)**

1. `sudo systemctl disable --now t900-ppp.service`, lalu hapus drop-in
   `/etc/systemd/system/t900-ppp.service.d/20-lora.conf` dan `sudo systemctl daemon-reload`.
2. Panel Edge, **Data > Atur**: host `127.0.0.1`, port `1883`, transport `tcp`, TLS mati (mosquitto lokal
   Pi). Kosongkan akun bila broker lokal anonim.
3. Pasang unit [deploy/examples/lora/rdf-lora-edge.service](../deploy/examples/lora/rdf-lora-edge.service)
   ke `/etc/systemd/system/`, lalu `sudo systemctl enable --now rdf-lora-edge.service`. Biarkan
   `ExecStart` menunjuk `/opt/rdf-node/current/run.py`: `install.sh` tidak menyentuh unit ini, jadi
   jembatan dari salinan lain (mis. `/opt/rdf-lora`) tidak ikut ter-upgrade. `install.sh` mencetak
   `NOTICE` bila unit `rdf-lora-edge`/`rdf-lora-ground` menjalankan jembatan di luar
   `/opt/rdf-node/current`. Unit yang benar pun tidak di-restart oleh `install.sh`: jembatan yang sedang
   jalan memakai rilis sebelumnya sampai `sudo systemctl restart rdf-lora-edge.service` (atau
   `rdf-lora-ground.service`), dan `install.sh` mencetak `NOTICE` dengan perintah itu.
4. Periksa: `journalctl -u rdf-lora-edge -f` dan `mosquitto_sub -h 127.0.0.1 -t 'sdr/v2/uav-01/lora/edge' -C 1`.

**Ground**

1. `sudo systemctl disable --now lora-ground-ppp.service`.
2. Salin paket RDF Node (cukup folder `rdf-node`, tanpa install) dan pasang
   [deploy/examples/lora/rdf-lora-ground.service](../deploy/examples/lora/rdf-lora-ground.service);
   sesuaikan `--device` dan path paket, lalu `sudo systemctl enable --now rdf-lora-ground.service`.
3. Periksa: `mosquitto_sub -h 127.0.0.1 -v -t 'sdr/v2/uav-01/#'` menampilkan health tiap detik dan
   `sdr/v2/uav-01/lora/ground` dengan `"link":"UP"`.

Kembali ke PPP: hentikan unit jembatan di kedua sisi, lalu nyalakan lagi PPP LoRa seperti sebelumnya.

## Sinkron jam (LoRa + GPS lewat chrony)

Freshness diukur dengan jam Ground terhadap timestamp Edge, jadi jam Pi harus mengikuti Ground; tanpa
IP, Pi tidak punya NTP. Dua sumber memberi sampel ke **chrony** lewat `refclock SOCK` (chrony yang menyetel
jam; jembatan tidak butuh root): LORA mengarahkan jam, GPS hanya dipantau (`noselect`, lihat di bawah):

- **LoRa (Ground sebagai acuan).** Jembatan Edge mengirim frame TIME (tipe 4) tiap 16 s berisi t1; Ground
  menjawab t1, t2 (terima), t3 (kirim); Edge mencatat t4. offset = ((t2−t1)+(t3−t4))/2,
  delay = (t4−t1)−(t3−t2), dalam mikrodetik. Dari 8 sampel terakhir dipakai yang delay-nya terkecil, dan
  dikirim ke `--chrony-sock` bila delay < 1 s. Status Edge: `time` {offset_ms, delay_ms, samples,
  last_age_s, requests, chrony}.
- **GPS.** `nav.gps.chrony_sock` (mis. `/run/chrony.gps.sock`): pembaca GPS mengirim waktu RMC dari fix
  valid dikurangi jam lokal saat kalimat tiba. u-blox mengirim RMC ~0.1–0.5 s setelah epoch; koreksi
  dengan `offset` di baris refclock.

Contoh `/etc/chrony/conf.d/rdf.conf` di Pi (chrony perlu dipasang, menggantikan systemd-timesyncd):

```
refclock SOCK /run/chrony.lora.sock refid LORA delay 0.2 precision 1e-2 poll 4 prefer trust
refclock SOCK /run/chrony.gps.sock refid GPS offset 0.2 delay 0.3 precision 1e-1 poll 4 noselect
makestep 1 -1
```

LORA adalah acuan jam: `prefer trust` membuat chrony mengikutinya, juga bila GPS tidak setuju. GPS
`noselect`: chrony tetap mengukur dan menampilkannya (`chronyc sources`, `chronyc sourcestats`), tetapi
tidak pernah menyetel jam dengannya. Alasannya, Ground menilai freshness dengan jamnya sendiri:
timestamp Edge yang lebih dari ~1 s di depan jam Ground ditolak (`SOURCE_TIME_FUTURE`), dan yang tertinggal
lebih dari ~4.4 s (DoA) atau ~7.4 s (health) dianggap basi, tanpa koreksi offset. Jadi Pi harus mengikuti
jam Ground, bukan UTC yang benar. Dengan dua sumber setara yang tidak sepakat chrony tidak punya
mayoritas dan berhenti mengikuti keduanya; jam Ground yang tidak di-NTP lalu menjauh dari jam Pi dan
data Edge ditolak. Ground sebaiknya tetap punya NTP atau GPS/PPS sendiri, tetapi itu hanya untuk label
waktu UTC yang benar, bukan untuk freshness.

Lalu jembatan Edge dijalankan dengan `--chrony-sock /run/chrony.lora.sock`. Socket dibuat chrony;
pastikan user `rdf-edge` boleh menulis ke socket itu.

## Uji kecepatan dan keandalan

`run.py lora-test` mengirim frame TEST RLS1 (COBS + CRC) dan mencetak JSON per langkah:

```bash
# penerima (satu sisi)
python3 run.py lora-test --role rx --device <port> --idle 25
# pengirim (sisi lain): sapu laju x ukuran frame, 15 s per langkah
python3 run.py lora-test --role tx --device <port> --rates 500,1000,1500,2000,3000 --sizes 64,200,240
# RTT: satu sisi echo, sisi lain tx --expect-echo
python3 run.py lora-test --role echo --device <port> --idle 40
python3 run.py lora-test --role tx --device <port> --rates 200,500,1000 --sizes 64,200 --expect-echo
```

Hasil rx: `frames`, `expected`, `loss_pct`, `bytes_per_s` (yang benar-benar sampai), `delay_ms_p50/p95`
(hanya bermakna bila jam kedua sisi sinkron). Port tidak boleh dipakai program lain selama uji.

## Yang hilang tanpa IP
- **Data Out KrakenSDR (10.90.0.2:8081) dan SSH lewat radio** tidak ada. Posisi node tersedia lewat
  field nav di `telemetry/health` (1.6, `nav.gps.publish_position`).
- Batas payload consumer 8192 B tetap berlaku; jembatan memakai batas yang sama.
