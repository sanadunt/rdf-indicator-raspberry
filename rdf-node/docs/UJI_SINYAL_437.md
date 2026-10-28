# Uji sinyal di 437.425 MHz: membedakan `ok=0`, sumber basi, dan "tidak ada sinyal"

Prosedur untuk operator, dijalankan saat Raspberry, KrakenSDR, dan antena menyala. Langkah
bertanda **[ubah]** mengubah perangkat atau setting. Jalankan hanya dengan persetujuan, satu per
satu, dan kembalikan nilai aslinya di akhir. Langkah lain hanya membaca.

Prosedur ini menjawab dua temuan lapangan:

- **2026-10-22:** `telemetry/doa` keluar dengan `ok: 0`, sedangkan health segar.
- **2026-10-26:** `health.age` naik di atas 10 s, tidak ada `telemetry/doa`, `DOA_value.html`
  kosong, dan `doa.xml` berumur sekitar 11 menit.

## 1. Baca keadaan sekarang (hanya baca)

Mulai 1.4.0, `telemetry/health.why` dan snapshot panel menyebut alasannya langsung. Di Raspberry:

```bash
curl -fsS http://127.0.0.1:8790/api/v2/snapshot | python3 -c '
import json,sys
s=json.load(sys.stdin); d=s["detection"]
print("valid:",d["valid"],"| why:",d["reasons"])
print("umur DoA ms:",d["source_age_ms"],"| raw:",d["raw_doa_deg"],"| f:",d["frequency_hz"])
print("DAQ:",s["daq"]["state"],"| jam:",s["host"]["clock_state"])'
```

Settings KrakenSDR yang relevan (hanya field aman, tanpa field lain):

```bash
curl -s -m 5 http://127.0.0.1:8081/settings.json | python3 -c '
import json,sys
j=json.load(sys.stdin)
for k in ("center_freq","vfo_freq_0","vfo_bw_0","vfo_squelch_0","uniform_gain","active_vfos","output_vfo","en_doa"):
    print(k,"=",j.get(k))'
```

Apakah file KrakenSDR masih ditulis ulang? Jalankan dua kali dengan jeda beberapa detik:

```bash
for f in status.json DOA_value.html doa.xml; do
  printf '%-15s %s bytes=%s\n' "$f" "$(curl -sI -m 5 http://127.0.0.1:8081/$f | tr -d '\r' | grep -i '^last-modified' | cut -d' ' -f2-)" \
    "$(curl -s -m 5 http://127.0.0.1:8081/$f | wc -c)"
done
```

## 2. Tentukan kasusnya

| Yang terlihat di `why` | Arti | Lanjut ke |
|---|---|---|
| Hanya `SOURCE_UNVERIFIED` dan/atau `ANGLE_UNVERIFIED` | Sampel segar dan sehat, tetapi persetujuan sumber/sudut belum diberikan di config. Bukan masalah sinyal. Inilah kasus 2026-10-22. | Langkah 5 |
| `EMPTY_CSV` (dan `NO_FRESH_DOA`), `DOA_value.html` tetap ditulis ulang, `status.json` segar, DAQ sehat | KrakenSDR berjalan, tetapi tidak ada sinyal di atas squelch di VFO. Kemungkinan besar ini kasus 2026-10-26. | Langkah 3 dan 4 |
| `DOA_FILE_NOT_UPDATING` dan/atau `STATUS_STALE_OR_UNAVAILABLE`, `DAQ_NOT_HEALTHY` | Pengolahan KrakenSDR berhenti atau DAQ bermasalah. Bukan soal sinyal. | Langkah 6 |
| `CLOCK_UNTRUSTED` | Jam Raspberry belum sinkron (umum tanpa RTC sebelum PPP/NTP naik). | Tunggu NTP, cek `timedatectl` |
| `CONFIG_UNAVAILABLE` atau `CONFIG_ATTRIBUTION_UNVERIFIED` | `settings.json` tidak terbaca atau baru berubah. | Ulangi langkah 1 setelah beberapa detik |

Sebelum langkah 3, pastikan juga frekuensinya benar:

- `vfo_freq_0` harus `437425000` (Hz).
- `center_freq` (MHz) harus `437.425`, atau setidaknya cukup dekat sehingga VFO berada di dalam
  bandwidth penerimaan. Dengan `center_freq` 433.92, VFO 437.425 berada 3,5 MHz di luar pita, dan
  tidak akan ada sinyal apa pun.
- `active_vfos` `1`, `output_vfo` `0`, `en_doa` `true`.

Bila salah satu tidak sesuai, perbaiki dulu **[ubah]** lewat UI KrakenSDR, atau lewat Ground
Console (`config.patch`, field `center_frequency_hz` dan `vfo0_frequency_hz` diubah bersamaan).

## 3. Uji dengan sinyal di frekuensi VFO

**[ubah]** Pancarkan sinyal uji di 437.425 MHz: handy talky atau signal generator berdaya rendah,
modulasi FM/CW, dari arah yang diketahui (catat azimutnya dari kompas atau peta), sejauh minimal
beberapa puluh meter dari array.

- Pastikan Anda memegang izin memancar di frekuensi itu. 437 MHz berada di pita amatir/satelit,
  jadi gunakan daya serendah mungkin dan durasi singkat.
- Selama memancar, ulangi langkah 1 tiap beberapa detik dan catat:

| Waktu | Azimut pemancar | `why` | `raw_doa_deg` | `ok` di Ground | Catatan |
|---|---|---|---|---|---|
| | | | | | |

Hasil yang diharapkan:

- `DOA_value.html` berisi satu baris, `EMPTY_CSV` dan `NO_FRESH_DOA` hilang, dan `telemetry/doa`
  keluar tiap sampel.
- `ok: 1` bila persetujuan sumber/sudut sudah ada. Bila belum, `ok: 0` dengan `reason` hanya
  `SOURCE_UNVERIFIED`/`ANGLE_UNVERIFIED` (lihat langkah 5).
- Arah: konvensi `theta_mirror`. `a` di DoA adalah sudut relatif terhadap array setelah koreksi.
  Bandingkan dengan azimut pemancar dikurangi arah hadap array. Selisih tetap (offset) berarti
  orientasi array; selisih yang berubah-ubah berarti multipath atau sinyal terlalu lemah.
- Setelah pemancar dimatikan, kondisinya kembali ke `EMPTY_CSV`. Itu menegaskan bahwa kosongnya
  file berasal dari squelch, bukan pengolahan macet.

## 4. Alternatif: turunkan squelch sementara

Bila tidak ada pemancar uji:

1. Catat nilai asli `vfo_squelch_0` dari langkah 1.
2. **[ubah]** Turunkan squelch 5–10 dB lewat UI KrakenSDR, atau lewat Ground Console
   (`config.patch` field `vfo0_squelch_db`).
3. Amati langkah 1 selama 1–2 menit. Bila DoA mulai muncul dari noise atau sinyal lain, rantai
   pengolahan sehat dan masalahnya memang tidak ada sinyal di atas squelch. Sudut dari noise tidak
   bermakna; yang diuji hanya alirannya.
4. **[ubah]** Kembalikan `vfo_squelch_0` ke nilai asli, lalu pastikan nilainya sudah kembali
   dengan langkah 1.

## 5. Kasus `ok=0` karena persetujuan

Bila `why` hanya berisi `SOURCE_UNVERIFIED`/`ANGLE_UNVERIFIED`, data sudah benar, tetapi Edge
belum diizinkan menyatakannya LIVE. Ikuti README bagian 5 ("Mengizinkan data RDF asli menjadi
LIVE"): `sudo rdf-node setup --verify-source` **[ubah]** (menulis `source.authority_verified` dan
`source.angle_verified` setelah Anda mengetik `VERIFIED`), lalu
`sudo systemctl restart rdf-edge.service` **[ubah]**. Lakukan hanya setelah langkah 3 membuktikan
arah sudut benar.

## 6. Kasus pengolahan macet

Bila `DOA_FILE_NOT_UPDATING` atau `STATUS_STALE_OR_UNAVAILABLE` muncul padahal service KrakenSDR
seharusnya berjalan:

```bash
systemctl status rdfsdr.service --no-pager      # nama unit dari link.engine_service
journalctl -u rdfsdr.service --since '-30 min' --no-pager | tail -n 50
vcgencmd get_throttled                          # 0x0 = daya normal
lsusb | grep -i -E 'realtek|rtl|2838'          # KrakenSDR terdeteksi
```

Restart SDR **[ubah]** hanya dengan persetujuan: lewat panel `Kontrol`, lewat Ground Console, atau
`sudo systemctl restart rdfsdr.service`. Setelah restart, ulangi langkah 1. Alasan
`DOA_FILE_NOT_UPDATING` harus hilang dalam beberapa detik.

## 7. Yang dilaporkan

- Keluaran langkah 1 sebelum dan sesudah uji (tanpa credential).
- Tabel langkah 3.
- Nilai squelch asli dan nilai sementara bila langkah 4 dipakai, serta bukti bahwa nilai asli
  sudah dikembalikan.
