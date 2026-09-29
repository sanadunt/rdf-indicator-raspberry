# Mulai di sini - RDF Node

## Raspberry
Gunakan folder sumber `rdf-node/` dari checkout terbaru. Arsip `rdf-node-1.0.0.zip` adalah rilis lama dan belum memuat perubahan PIN ini.


```bash
cd rdf-node
sudo bash install.sh --kiosk=rdf
```

Pilih folder `_share` aktif pada wizard. Aplikasi tidak menebak path lama. Biarkan read-only
saat commissioning. Dari desktop grafis jalankan `rdf-kiosk-session`, atau buka
`http://127.0.0.1:8790`. Tidak perlu reboot untuk backend.

Prerequisites: Linux systemd, Python3.10+, Chromium untuk layar, ACL bila memberikan izin
baca pada folder SDR. Installer tidak mengubah PPP, Conda, atau driver layar.

PIN lokal acak 6 digit dibuat saat instalasi, baca melalui `sudo cat /etc/rdf-node/initial-admin-pin.txt`.
Jangan kirim PIN atau credential ke chat/Git. Ganti PIN dengan `sudo rdf-node set-pin`.
Instalasi lama yang masih memakai password perlu mengganti PIN dari CLI sebelum login baru.

## MQTT dan Ground

Paket menyertakan companion Ground, broker TLS provisioning, decoder360 dan receipt.
Di Ubuntu, setelah paket OS mosquitto/mosquitto-clients/openssl tersedia:

```bash
sudo bash install.sh --ground
sudo bash scripts/provision-ground.sh
```

Salin folder bundle credential hasil provisioning secara aman ke Raspberry, kemudian:

```bash
sudo rdf-node import-bundle /home/rdf/rdf-uav-bundle
sudo systemctl restart rdf-edge.service
```

Ground preview/API: `http://127.0.0.1:8791`. Tidak mengganti dashboard lama.

## Validasi RDF asli

`sudo -u rdf-edge rdf-node doctor`. Pastikan DAQ dan waktu sehat, sumber fresh, dan orientasi
terverifikasi, kemudian `sudo rdf-node setup --verify-source` dan restart hanya edge.
Grafik akan dipause sampai source gate/receipt lolos dan stabil20detik.

## Kontrol

Start/Stop/Restart/konfigurasi/reboot sudah memiliki implementasi, tetapi approval lokal
wajib sebelum write. Ikuti README dan docs/CONTROL.md; jangan memilih unit engine sembarang.
Reboot memerlukan lease maintenance dan prepare/execute, bukan satu tombol tanpa pemeriksaan.

## Bukti dan batas

113 automated tests lulus lokal. Codec360, MQTT fixture dua arah/TLS, API keamanan dan
command validation diuji. Panel4halaman diperiksa di viewport480x320.
Belum diuji pada hardware Raspberry/T900 Anda atau Mosquitto aktual. Baca TEST_REPORT.md.

Semua petunjuk rinci, troubleshooting, rollback dan source ada di `rdf-node/`.
