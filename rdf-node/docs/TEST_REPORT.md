# Laporan pengujian release 1.0.0

Tanggal build: 29 September 2026. Lingkungan Linux container x86_64, Python3.13.
Aplikasi sendiri menargetkan Python3.10+ pada Raspberry/Ubuntu systemd.

Catatan: hasil 113 test di bawah adalah arsip release 1.0.0 sebelum Angular beralih ke JSON.
Coverage RDF2/Q16/U8 di bagian tersebut tidak menggambarkan source saat ini.

## Hasil yang benar-benar dijalankan

**113 automated tests lulus**, 19.598 detik pada run final terdokumentasi.
Lihat output lengkap `evidence/unittest-results.txt`. Semua tes menggunakan temporary files,
loopback network dan mock command systemd/reboot. Tidak menjalankan reboot/stop SDR nyata.

| Area | Cakupan |
|---|---|
| Config | schema strict, key unknown, TLS/plaintext policy, unit/profile validation |
| Native parser | 377 fields, trailing delimiter, finite values, metadata units, ambiguity, redaction |
| Source gates | no path, same timestamp, clock/source approval, DAQ invalid, stale/partial, old config |
| Angular | header/chunk exact size, Q16 error bound, U8 constant, overflow rejection, order/duplicate/timeout |
| Journal | dedup/hash conflict, crash no replay, config revisions, boot reconciliation |
| Helper | allowlist, approved paths, safe patch, digest conflict, nofollow, maintenance/reboot checks |
| Wire MQTT | varint/properties/packet format, queue caps and expiry |
| MQTT network | auth, QoS0/1, PUBACK, SUBACK denial, reconnect, retained flag/history |
| TLS network | local TLS server trust and unknown CA rejection |
| App command | session/expiry/topic/revision/clock/remote defaults, idempotency, challenge redaction |
| API | Host/Origin/auth/CSRF, incorrect passwords, JSON/size limits, preferences, logout, health semantics |
| End-to-end | Agent -> broker fixture -> Ground decoder -> full360 -> config query ACK |
| Stop source | Fresh bridge health continues after fixture SDR source stops |

End-to-end test memakai packet pacing default dan mengurangi hanya resume-stability wait
menjadi1s untuk mempercepat tes. Produksi tetap20s. Tidak melakukan capacity emulation T900.

## Browser 480x320

Actual local HTML/CSS/JavaScript dirender menggunakan Chromium headless/Playwright.
Keempat halaman mempunyai scrollWidth480, scrollHeight320, dan empat tombol navigasi
120x48. Tidak ada JavaScript error. Frozen snapshot_seq memunculkan alarm setelah5s.
Screenshot Overview, Link, Sistem, Config, Login dan Stale ada pada `evidence/`.

**Metode:** policy Chromium environment memblokir navigasi URL. Oleh karena itu local assets
 dimasukkan ke DOM lewat Playwright, dengan pembacaan snapshot demo melalui test adapter;
API HTTP/auth diuji terpisah oleh suite. Ini pemeriksaan DOM/layout/logic actual assets,
BUKAN klaim berhasil navigasi full browser HTTP di environment tersebut maupun panel fisik.
Tidak mengubah policy browser global atau driver layar. Data screenshot adalah DEMO.

## Pemeriksaan tambahan

Python compileall untuk src/tests, JavaScript `node --check` untuk kedua bundle,
serta bash syntax untuk tiap script installer/deployment dijalankan tanpa error.
Dependency runtime tidak diambil dari pip. Third-party pure YAML disertai license.
ZIP final dilengkapi checksum dan diperiksa integritas/isi.

## Belum diuji / acceptance perangkat

- Mosquitto broker aktual dan ACL/TLS service pada Ubuntu. Jaringan package download
  environment build tidak tersedia; fixture MQTT independen tidak menggantikan test Mosquitto.
- Installer systemd sebagai root pada Raspberry. Environment build bukan host Pi yang boot.
- Cold boot, service recovery dengan USB T900, packet loss/airtime nyata dan worst-case ACK latency.
- Display480x320 fisik, touchscreen, brightness/driver, compositor/autologin/kiosk OS.
- CPU/thermal/dropped-frames engine saat browser aktif pada model Raspberry pengguna.
- Actual write/read-back watcher native, cakupan stop wrapper/cgroup, native configuration race.
- Start/Stop/Restart/Reboot perangkat asli. Helper system commands **dimock** dalam tests.
- Source active path, authority/orientation, clock sync dan metadata unit pada runtime pengguna.
- Integrasi source code aplikasi Ground lama, yang tidak diberikan.

Karena itu release adalah paket implementasi yang dapat dipasang untuk commissioning,
bukan sertifikasi sistem lapangan atau jaminan semua command aman sebelum approval runtime.
Read-only default dan explicit gates membantu commissioning bertahap tanpa menyembunyikan batas.

## Mengulang test

```bash
python3 run.py selftest
```

TLS tests memerlukan executable openssl; jika tidak tersedia, test tersebut ditandai skipped,
bukan dianggap lulus TLS. Simpan hasil rerun pada perangkat sebagai bukti terpisah.
Jangan menjalankan tester throughput bersamaan dengan operasi penting melalui radio.

## Verifikasi setelah Ground-requested settings cutover

`python3 run.py selftest`: 233 tests dijalankan, 0 gagal, 2 dilewati karena `Linux ping` tidak
tersedia.

Coverage baru mencakup retensi byte settings native yang persis, `config.get` satu kali tanpa
report otomatis, penolakan TLS-off/source unavailable/stale/payload di atas 8 KiB, serta
validasi Ground terhadap request ID, session, boot, freshness, dan JSON mentah. Edge menolak
`config.get` lokal yang tidak punya consumer; Ground selalu memakai `state.cfg` sebagai
`base_rev`, bukan nilai caller. Test command memastikan read-only `config.get` tidak butuh health
fresh, sementara mutation tetap membutuhkannya. DoA/Angular menerima revision berbeda hanya bila
session, freshness, health, dan evidence lain lolos.

End-to-end memakai broker TLS fixture loopback untuk memeriksa request, response settings mentah,
dan ketiadaan konfirmasi aplikasi Ground-ke-Edge. Ini bukan uji Mosquitto, Raspberry, PPP/T900,
atau display fisik.

Uji browser manual membuka demo Edge dan Ground companion melalui HTTP lokal. Edge menampilkan
inventory `settings/reported` tanpa status sync lama. Ground renderer menampilkan raw JSON dan
safe form values dari state browser yang disiapkan; `evidence/browser-qa.json` adalah artifact
DOM-injection Edge terpisah, bukan capture halaman Ground atau respons MQTT live. Request/response
nyata diuji terpisah dengan TLS broker fixture.
