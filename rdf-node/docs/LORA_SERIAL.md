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
| 1 | tipe: `1` DATA, `2` HEARTBEAT |
| 2–3 | nomor urut frame pengirim (uint16 BE), naik 1 per frame yang benar-benar dikirim |
| 4 | id pesan (uint8); fragmen satu pesan dan salinannya memakai id yang sama |
| 5 | id topik (tabel di bawah) |
| 6 | flag: bit0 retain, bit1 QoS 1, bit2 zlib |
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

### Id topik (tidak boleh diubah, hanya ditambah)

| Id | Topik (`sdr/v2/<node>/…`) | Arah | Prioritas | Umur maks | Kebijakan |
|---|---|---|---|---|---|
| 1 | `telemetry/health` | Edge→Ground | 1 | 5 s | terbaru menang |
| 2 | `telemetry/health/detail` | Edge→Ground | 4 | 15 s | terbaru menang, min. tiap 30 s |
| 3 | `telemetry/doa` | Edge→Ground | 1 | 3 s | terbaru menang |
| 4 | `telemetry/angular` | Edge→Ground | 5 | 8 s | terbaru menang, min. tiap 20 s |
| 5 | `telemetry/diagnostic/doa` | Edge→Ground | 2 | 3 s | terbaru menang |
| 6 | `telemetry/diagnostic/angular` | Edge→Ground | 5 | 8 s | terbaru menang, min. tiap 20 s |
| 7 | `state` | Edge→Ground | 0 | – | retained, terbaru menang |
| 8 | `capabilities` | Edge→Ground | 3 | – | retained, terbaru menang |
| 9 | `availability` | Edge→Ground | 0 | – | retained, terbaru menang |
| 10 | `settings/reported` | Edge→Ground | 2 | 30 s | antre, dikirim 2× |
| 11 | `ack/config` | Edge→Ground | 0 | 30 s | antre, dikirim 2× |
| 12 | `ack/operation` | Edge→Ground | 0 | 30 s | antre, dikirim 2× |
| 64–74 | `cmd/config/get`, `cmd/config/patch`, `cmd/processing/set`, `cmd/service/restart`, `cmd/service/ppp/restart`, `cmd/system/reboot/prepare`, `cmd/system/reboot/execute`, `cmd/system/shutdown/prepare`, `cmd/system/shutdown/execute`, `cmd/operation/get`, `cmd/stream/set` | Ground→Edge | 0 | 10 s | antre, dikirim 2× |

Umur maks menjaga data tetap di dalam jendela freshness Ground (health 8 s, DoA 5 s, Angular 10 s) dan
command di dalam deadline 15 s (`expires_ms`). Tidak ada retransmisi: telemetry digantikan yang lebih
baru, salinan kedua command/ACK dibuang penerima (id pesan sama), dan `state`, `capabilities`,
`availability` dikirim ulang oleh jembatan Edge setiap kali link kembali naik.

## Status jembatan

Setiap sisi mempublish status retained ke broker lokalnya: `sdr/v2/<node>/lora/edge` dan
`sdr/v2/<node>/lora/ground` (tiap 5 s), dan mencetaknya ke journal tiap 30 s:

```json
{"v":1,"role":"ground","t":1793500000000,"link":"UP","last_rx_ms":420,"port":"OPEN","port_error":null,
 "mqtt":"CONNECTED","rx":{"frames":812,"crc_errors":1,"bad_frames":0,"lost_frames":3,"messages":640,
 "duplicates":12,"incomplete":0,"heartbeats":160},"tx":{"queued":20,"superseded":0,"expired":0,
 "sent_messages":20,"sent_frames":200,"sent_bytes":2400,"too_large":0},"queue":0,
 "peer":{"uptime_s":812,"rx_frames":198,"crc_errors":0,"lost_frames":2,"clock_offset_ms":-35},
 "mqtt_dropped":0,"ignored":0,"link_changes":1}
```

`link` `DOWN` bila tidak ada frame selama 15 s. Jembatan tidak membuat `availability` palsu untuk Edge;
Ground melihat link putus dari topik ini dan dari health yang berhenti.

## Anggaran

Dengan kompresi: `health` lengkap (kompas + GPS) 232 B JSON → 126 B di udara; kurva Angular 2078 B →
861 B (5 frame). Perkiraan rata-rata: health ~130 B/s, DoA ~100 B/s, diagnostic DoA ~40 B/s, detail
~8 B/s, Angular ~45 B/s → sekitar 320 B/s dari 1000 B/s. Arah Ground→Edge hanya command dan heartbeat,
supaya tabrakan half-duplex tetap jarang.

## Pemasangan

Hentikan PPP lebih dulu: port LoRa hanya boleh dibuka satu program.

**Raspberry (Edge)**

1. `sudo systemctl disable --now t900-ppp.service`, lalu hapus drop-in
   `/etc/systemd/system/t900-ppp.service.d/20-lora.conf` dan `sudo systemctl daemon-reload`.
2. Panel Edge, **Data > Atur**: host `127.0.0.1`, port `1883`, transport `tcp`, TLS mati (mosquitto lokal
   Pi). Kosongkan akun bila broker lokal anonim.
3. Pasang unit [deploy/examples/lora/rdf-lora-edge.service](../deploy/examples/lora/rdf-lora-edge.service)
   ke `/etc/systemd/system/`, lalu `sudo systemctl enable --now rdf-lora-edge.service`.
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
IP, Pi tidak punya NTP. Dua sumber memberi sampel ke **chrony** lewat `refclock SOCK` (chrony yang memilih
dan menyetel jam; jembatan tidak butuh root):

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
refclock SOCK /run/chrony.lora.sock refid LORA delay 0.2 precision 1e-2 poll 4
refclock SOCK /run/chrony.gps.sock refid GPS offset 0.2 delay 0.3 precision 1e-1 poll 4
makestep 1 -1
```

Lalu jembatan Edge dijalankan dengan `--chrony-sock /run/chrony.lora.sock`. Socket dibuat chrony;
pastikan user `rdf-edge` boleh menulis ke socket itu. Ground cukup punya jam NTP yang benar.

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
