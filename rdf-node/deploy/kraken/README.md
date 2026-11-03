# Patch KrakenSDR untuk RDF Node

## `vfo_peaks.patch` (Edge 1.11)

KrakenSDR menghitung puncak spektrum di lebar setiap VFO pada setiap frame, lalu membandingkannya dengan
squelch. Hanya frame yang lolos yang masuk `DOA_value.html` (sebagai `power`), jadi selama squelch menahan
sinyal, Edge dan Ground tidak tahu seberapa jauh sinyal itu di bawah squelch.

Patch ini menambah `vfo_peaks` ke `_share/status.json` (ditulis setiap frame):

```json
"vfo_peaks": [{"vfo": 0, "frequency_hz": 420820864, "bandwidth_hz": 12500,
               "peak_db": -78.4, "squelch_db": -70.0, "timestamp_ms": 1793692551129}]
```

`peak_db` adalah nilai yang sama dengan yang dibandingkan dengan squelch (dB skala FFT KrakenSDR, bukan
dBm): puncak CH1 pada spectrum Single, puncak semua kanal pada Full. Berbeda dari `power` di
`DOA_value.html`, nilainya tidak dipotong di −100 dB. Nilai tidak finite ditulis `null` supaya `status.json` tetap JSON ketat. Entri berasal dari frame
terakhir yang diproses (`timestamp_ms`), karena status ditulis di awal frame berikutnya.

Edge 1.11 membacanya ke `detection.vfo_peak`, menampilkannya di panel saat DoA belum live, dan mengirim
`pk`/`psq` di `telemetry/health`. Edge tanpa patch ini, atau KrakenSDR stok, tetap bekerja seperti sebelumnya.

Diuji terhadap rdfsdr di Raspberry (`/home/rdf/rdfsdr/rdfsdr_doa`, file asli
md5 `114fc75fbeef2e5214b46026d0e89462`).

Memasang (backup dulu; KrakenSDR harus di-restart agar patch dipakai). File milik user `rdf`, jadi tidak
perlu sudo. `P` adalah path patch ini di paket rdf-node, misalnya hasil salin ke Raspberry:

```bash
P=/home/rdf/vfo_peaks.patch
cd /home/rdf/rdfsdr/rdfsdr_doa
F=_sdr/_signal_processing/kraken_sdr_signal_processor.py
cp -p "$F" "$F.bak-$(date +%Y%m%d-%H%M%S)"
patch -p1 --dry-run < "$P" && patch -p1 < "$P"
sudo systemctl restart rdfsdr
```

Membatalkan: kembalikan file `.bak-*`, restart KrakenSDR, lalu restart `rdf-edge` juga. Edge yang sudah
pernah melihat `vfo_peaks` terus mengirim `pk`/`psq` null sampai di-restart.
