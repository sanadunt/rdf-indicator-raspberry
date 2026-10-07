# MQTT dan Ground companion
Ringkasan lengkap topic, payload, command, settings request, dan contoh Ground: [MQTT_TOPIC_SUMMARY.md](MQTT_TOPIC_SUMMARY.md).

## Pilihan yang paling mudah

Gunakan installer role Ground dan provision script pada README. Paket membuat broker TLS
terpisah pada port8883, receiver pada API8791, bukan mengubah broker/dashboard lama.
Provisioner menunggu alamat PPP10.90.0.1 sehingga startup tidak mengandalkan radio sudah up.
Jika paket mosquitto OS menyalakan broker default1883, itu tetap service OS terpisah;
audit listener/firewall OS tersebut. Script RDF tidak mengklaim mengamankan broker lain.

Bila port8883 sudah dipakai, jangan menumpuk listener. Gunakan broker existing dengan ACL
berikut atau tentukan port alternatif secara terkoordinasi. Provisioner tidak merotasi
credential/certificate yang sudah ada diam-diam; backup terlebih dahulu saat migrasi.

## ACL minimum

Node control:
- publish telemetry/doa, telemetry/diagnostic/doa, telemetry/diagnostic/angular,
  telemetry/health, telemetry/health/detail, state, capabilities, availability,
  settings/reported, ack/config, ack/operation milik node.
- subscribe cmd/# milik node sendiri.

Node bulk:
- publish telemetry/angular saja; tidak memperoleh hak reboot/command.

Ground controller:
- subscribe `telemetry/#`, `state`, `availability`, `settings/reported`, `capabilities`, and `ack/#`.
- publish `cmd/#` milik node sendiri.

Viewer:
- subscribe `telemetry/#`, `state`, `availability`, `settings/reported`, `capabilities`, and `ack/#`.
- tidak publish command dan tidak mendapat credential controller.

Prefix `sdr/v2/uav-01` harus cocok di konfigurasi agent, receiver dan ACL.
Broker harus mempertahankan retained state, capabilities, dan availability. Command, telemetry,
dan `settings/reported` tidak retained. Subscription command memakai Retain Handling 2 dan
Retain As Published; live retained command tetap dapat dikenali kemudian ditolak.

Provisioner memberi Node Control hak tulis `telemetry/diagnostic/doa` dan
`telemetry/diagnostic/angular`. Pada broker yang sudah ada, tambahkan kedua aturan
`topic write <prefix>/telemetry/diagnostic/doa` dan
`topic write <prefix>/telemetry/diagnostic/angular` ke ACL user Node Control; Ground
controller/viewer dengan `telemetry/#` sudah subscribe keduanya. Jangan beri hak diagnostic
itu ke Node Bulk.

## Memakai broker existing

Di Raspberry, atur root-owned `/etc/rdf-node/config.yaml`:

```yaml
mqtt:
  enabled: true
  host: 10.90.0.1
  port: 8883
  tls: true
  ca_file: /etc/rdf-node/credentials/ca.crt
  control_credentials_file: /etc/rdf-node/credentials/control.json
  bulk_credentials_file: /etc/rdf-node/credentials/bulk.json
  keepalive: 15
  reconnect_max: 30
  allow_insecure_loopback: false
```

Ini potongan dalam schema config penuh, bukan pengganti seluruh file. Unknown keys ditolak.
Jika autentikasi broker dipakai, setiap credential JSON berisi `username` dan `password` nyata yang hanya disimpan di perangkat. Pada panel, pasangan kosong mempertahankan credential per-channel yang sudah ada; tanpa kredensial terkonfigurasi, client memakai anonymous CONNECT.
`allow_insecure_loopback` hanya diperlukan untuk tes plaintext pada loopback; `tls: false`
secara eksplisit memilih plaintext untuk endpoint lain. Folder root:rdf-edge0750, files root:rdf-edge0640.
CA public boleh dibaca service; private key CA tetap di mesin provisioning, tidak disalin ke Raspberry.

Pada receiver Ubuntu, gunakan `/etc/rdf-ground/config.yaml`, MQTT endpoint loopback8883,
CA yang sama dan credential controller sendiri. Start:

```bash
sudo systemctl enable --now rdf-ground.service
```

TLS mode mewajibkan certificate chain, SAN hostname/IP, dan waktu mesin valid. `tls: false`
memilih MQTT/TCP atau `ws://` tanpa enkripsi; credential dan payload dapat dibaca di jaringan.
Gunakan hanya pada link privat/tepercaya yang disetujui. `allow_insecure_loopback` tetap mewajibkan
opt-in eksplisit untuk tujuan loopback plaintext, tetapi bukan untuk endpoint remote yang dipilih
secara eksplisit. Tidak ada mode TLS yang mematikan verifikasi.

## Transport and certificate trust

The Edge and Ground clients support `mqtt.transport: tcp` (default) or `websocket`, with
`mqtt.websocket_path` (default `/mqtt`). `tls: true` (the default) uses verified TLS for either
transport: TCP/TLS or WSS. `tls: false` uses plaintext TCP or `ws://`. The Ground provisioner
still creates only the TCP/TLS listener on 8883; configure a matching plaintext TCP or WebSocket
listener, path, and ACL separately when that is the required deployment.

With `tls: true` and `ca_file: null`, Python uses the OS default CA store and keeps certificate
and hostname verification enabled. A broker certificate issued by a public CA can therefore work
without installing a private CA bundle. Set `ca_file` to the provisioned CA bundle for a private
or self-signed broker certificate. Trusting system CAs does not make a private certificate trusted.

## Readiness dan assurance

CONNECTED setelah CONNACK; READY sesudah semua subscription SUBACK diterima. Penolakan
ACL saat subscribe/publish membuat error, bukan indikator hijau diam-diam.

Maksimum satu outgoing PUBLISH aktif per koneksi. Local latest-value outbox tidak
mengumpulkan history offline. New connection memakai clean start, sessionexpiry0,
subscribe ulang, bootstrap state/capability lalu telemetry. Edge tidak mengirim settings saat
bootstrap; Ground meminta `settings/reported` hanya melalui `config.get`.

Command QoS 1 acknowledgement dari broker tidak sama dengan hasil tindakan. Local send completion
QoS 0 tidak membuktikan Ground menerima. Edge tidak menerima konfirmasi aplikasi atas telemetry;
PUBACK hanya membuktikan broker mengakui PUBLISH QoS 1.

Bulk reset/reconnect tidak merestart control atau engine. Dua koneksi tetap berbagi TCP/IP/
PPP/radio. Token bucket dan coalescing mengurangi burst, bukan menjamin latency RF.
Default pause setelah command/source/link issue dan resume20s stabil dipertahankan.

## Uji penerimaan nyata

Setelah MQTT dikonfigurasi:
1. Pastikan kedua client READY pada tab Link dan health receiver bertambah dengan DAQ yang benar.
2. Gunakan `Refresh config` secara eksplisit. TLS harus aktif; pastikan `cmd/config/get` diikuti
   `settings/reported` yang cocok dengan session dan request ID. Report tidak muncul otomatis.
3. Validasi output source lalu lihat DoA serta satu PUBLISH `telemetry/angular` berformat JSON,
   dengan `encoding="json"` dan tepat 360 angka pada `values`.
4. Jika authority/angle approval belum ada tetapi seluruh gate integritas data lulus, pastikan Edge tetap mengirim `telemetry/doa` (`ok=0`, RAW) dan `telemetry/angular` (flags parsial) pada topic normal. Ground menampilkan keduanya sebagai `UNVERIFIED`; detection tetap invalid.
5. Pastikan `telemetry/diagnostic/doa` berulang setiap 3 detik selama XML tersedia, termasuk saat sampel sama dan DoA normal valid; data tetap `UNVERIFIED`. Saat gate normal memblokir, pastikan panel utama Edge menampilkan sudut raw dan frekuensi XML secara terpisah.
6. Saat `DOA_value.html` berisi record 360 sampel dan gate integritas source gagal (misalnya service/DAQ berhenti atau source stale), pastikan `telemetry/diagnostic/angular` tiba. Interval 6 detik saat source tidak eligible dan 30 detik saat source eligible tetapi Bulk terblokir adalah minimum; ukuran JSON dan headroom Control dapat memperpanjangnya. Saat Bulk normal mengalir, tidak ada duplikasi. Periksa timestamp sumber dan 360 values di `/api/v2/angular/diagnostic/latest`; kandidat tetap `UNVERIFIED`, tidak mengubah detection LIVE. Profile `control` sengaja tidak mengirim array diagnostik.
7. Putuskan radio sementara dengan jalur management aman, lihat stale/lost, reconnect tanpa burst data lama; jangan reset SDR.
8. Jalankan command config/read-only, lalu subset write yang sudah di-approve.
9. Ukur actual PPP counters, capture MQTT/TCP terkontrol, latency dan loss >30menit.
10. Cold boot kedua host, radio belum terpasang, kemudian radio hadir; periksa recovery.

Paket build diuji dengan broker fixture loopback, bukan uji Mosquitto/T900 nyata.
