# Basis rancangan, sumber teknis, dan lisensi

## Dokumen project

Implementasi mengikuti dua dokumen pengguna yang disertakan kembali pada `docs/plans/`:

1. Dashboard_RDF_Raspberry_480x320_Rencana_Implementasi_v1.md
2. Rancangan_Lengkap_RDF_Telemetry_Command_Dashboard_Raspberry_5inci.md

Format output, unit, freshness dan lifecycle juga diturunkan dari dokumen project:
SDR_DOA_8081_DATA_REFERENCE, SDR_DOA_UPSTREAM_ARCHITECTURE,
SDR_DOA_TELEMETRY_ARCHITECTURE_AND_SETTINGS_CONTROL,
Dokumentasi_Service_SDR_T900_PPP_Autostart dan dokumentasi throughput.

Bahan project tersebut adalah dokumentasi dan output terminal, bukan source deployment
engine/ground lengkap yang diverifikasi sekarang. Read-only runtime lama tidak dianggap
status Raspberry sekarang. Detail teknis yang tidak tersedia menjadi setup/approval gate.

## Rujukan primer

Diperiksa sebagai rujukan protokol/deployment; bukan bukti versi paket di perangkat:

- MQTT5 OASIS Standard: https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html
  CONNECT/clean session, PUBACK, QoS, retain, Message Expiry dan subscription options.
- Mosquitto config: https://mosquitto.org/man/mosquitto-conf-5.html
  TLS listeners, passwords/ACL, queue and packet caps.
- Paho API (alternatif yang diusulkan rencana):
  https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
  Release ini **tidak** membundel/menggunakan Paho; lihat IMPLEMENTATION.md.
- Raspberry Pi kiosk tutorial:
  https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/
  Adaptasi graphical-session/kiosk, bukan pemasangan driver layar generik.
- systemd service reference:
  https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
  Ordering/lifecycle service berbeda dari readiness DAQ.

## Lisensi dan provenance kode

Kode aplikasi baru dalam src, web, scripts, tests dan deploy ditulis untuk paket ini dan
tersedia dengan MIT pada LICENSE. Tidak menyalin algoritma DSP/receiver upstream KrakenRF.
Tidak menyediakan atau mengganti engine GPL yang telah terpasang.

`vendor/yaml` ialah PyYAML6.0.3 pure Python dari environment build, beserta lisensi upstream
MIT pada `vendor/PyYAML-LICENSE`. Tidak membawa extension binary/folder site-packages umum.
Font browser menggunakan system font; tidak ada file font dalam ZIP. Tidak ada asset
berbayar, secret produksi, sertifikat pribadi produksi, atau hasil pengukuran live yang dibuat-buat.

Screenshot diberi mode DEMO dan angka berasal fixture sintetis, bukan kondisi Raspberry.
