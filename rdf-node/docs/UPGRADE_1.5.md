# Upgrade ke 1.5.0: menyamakan versi Edge dan rdf-ground, membersihkan retained

Prosedur ini untuk operator. Semua perintah dijalankan manusia di perangkatnya sendiri, satu per
satu, saat alat menyala. Tidak ada langkah yang dijalankan otomatis dari repo. Ganti placeholder
`<...>` dengan nilai setempat. Jangan menempel isi file credential, PIN, atau sandi ke chat atau
log.

## Posisi sekarang dan target

| Host | Terpasang sekarang | Nomor rilis | Target |
|---|---|---|---|
| Groundstation (`rdf-ground`) | `/opt/rdf-node/releases/1.0.0-20261006185856-*` | 1.1.0 | 1.5.0 |
| Raspberry (`rdf-edge`) | `/opt/rdf-node/releases/1.0.0-20261013194717-*` | 1.2.0 | 1.5.0 |

Rilis sebelum 1.5.0 selalu melaporkan `version: "1.0.0"`. Nomor 1.1.0–1.4.0 diberikan belakangan
(lihat `CHANGELOG.md`). Mulai 1.5.0, `capabilities.version` adalah nomor rilis dan `contract`
adalah revisi kontrak MQTT (`1.5`).

Kenapa perlu disamakan: rdf-ground 1.1.0 lebih tua dari perubahan kontrak 2026-10-07. Ia
mengharapkan Angular dalam chunk biner RDF2, sedangkan Edge 1.2.0 sudah mengirim Angular sebagai
satu JSON. Ia juga masih memakai receipt dan `config/reported` yang sudah dihapus. Jadi preview
rdf-ground 1.1.0 tidak bisa menampilkan Angular dari Edge 1.2.0 atau yang lebih baru.

## Urutan

1. **Groundstation dulu.** rdf-ground 1.5.0 tetap bisa membaca Edge 1.2.0, karena field Edge 1.5.0
   yang baru (`contract`, `why`, `reason`, `ignored`, `message`) semuanya aditif. Bila ada
   masalah, hanya preview Ground yang terdampak, dan Edge tetap jalan.
2. **Raspberry setelah groundstation terverifikasi.**
3. **Bersihkan retained** setelah keduanya 1.5.0.

Ground Console terpisah dari paket ini dan tidak ikut prosedur ini. Ground Console 3.0.0 menerima
payload 1.5.0 (field tambahan diabaikan). `main` Ground Console terbaru juga menampilkan DoA
berlabel `ok: 0`.

Receiver `rdf-ground` 1.5.0 juga menampilkan DoA berlabel (keputusan D4). Sampel `ok: 0`
dengan alasan apa pun, dan sampel yang basi atau tanpa health fresh, tampil di preview sebagai
"UNVERIFIED" beserta alasannya. Deteksi LIVE dan gate command tetap ketat.

## 0. Paket

Setelah PR 1.5.0 di-merge, buat ZIP dari commit merge-nya di komputer operator:

```bash
git clone https://github.com/sanadunt/rdf-indicator-raspberry.git && cd rdf-indicator-raspberry
git checkout <commit-merge-1.5.0>
mkdir /tmp/rdf-pkg && git archive HEAD rdf-node | tar -x -C /tmp/rdf-pkg
(cd /tmp/rdf-pkg && find rdf-node -type f | LC_ALL=C sort | TZ=UTC zip -q -X -D -@ ../rdf-node-1.5.0.zip)
sha256sum /tmp/rdf-node-1.5.0.zip
```

Catat sha256-nya sebagai `<SHA-ZIP>`. Paket yang sama dipakai di kedua host.

## A. Groundstation (rdf-ground)

Lakukan saat tidak ada misi dan tidak ada perintah Kontrol yang berjalan.

1. Pra-cek, lalu catat hasil `readlink` sebagai `<RILIS-GROUND-LAMA>`:

   ```bash
   systemctl status rdf-ground.service --no-pager
   readlink -f /opt/rdf-node/current
   curl -fsS http://127.0.0.1:8791/api/v2/healthz; echo
   ```

2. Backup config dan state (berisi credential; jangan dibuka atau disalin keluar):

   ```bash
   sudo systemctl stop rdf-ground.service
   sudo install -d -m 0700 /root/rdf-ground-backup
   sudo tar --numeric-owner -cpzf /root/rdf-ground-backup/rdf-ground-$(date +%Y%m%d-%H%M%S).tgz -C / etc/rdf-ground var/lib/rdf-ground
   sudo chmod 600 /root/rdf-ground-backup/*.tgz
   ```

   Bila upgrade dibatalkan di titik ini: `sudo systemctl start rdf-ground.service`.

3. Salin paket, cek checksum, lalu ekstrak:

   ```bash
   echo "<SHA-ZIP>  rdf-node-1.5.0.zip" | sha256sum -c -
   rm -rf /tmp/rdf-upgrade && mkdir /tmp/rdf-upgrade && unzip -q rdf-node-1.5.0.zip -d /tmp/rdf-upgrade
   cd /tmp/rdf-upgrade/rdf-node && sha256sum --quiet -c SHA256SUMS && echo PAKET_OK
   ```

4. Validasi config Ground dengan kode baru. Ini hanya membaca dan harus `exit=0`:

   ```bash
   sudo -u rdf-ground /usr/bin/python3 -B -c 'import sys;sys.path[:0]=["/tmp/rdf-upgrade/rdf-node/src","/tmp/rdf-upgrade/rdf-node/vendor"];from rdf_node.config import load_config;load_config("/etc/rdf-ground/config.yaml");print("CONFIG_OK")'; echo "exit=$?"
   ```

   Bila muncul `<key>: UNKNOWN_CONFIG_KEY`, key itu milik rilis lama. Hapus barisnya dengan
   `sudoedit /etc/rdf-ground/config.yaml`, lalu ulangi langkah ini. Key yang dihapus sejak 1.1.0:
   `telemetry.require_ground_receipt_for_bulk`, `freshness.receipt_warn_ms`,
   `freshness.receipt_lost_ms`.

5. Pasang (sekali saja), lalu jalankan:

   ```bash
   cd /tmp/rdf-upgrade/rdf-node
   sudo bash install.sh --ground
   sudo systemctl restart rdf-ground.service
   ```

   Installer tidak menimpa `/etc/rdf-ground/config.yaml`, hash PIN, atau provisioning broker.
   Jangan jalankan `scripts/provision-ground.sh` lagi: skrip itu menolak menimpa provisioning yang
   sudah ada.

6. Verifikasi:

   ```bash
   systemctl status rdf-ground.service --no-pager
   readlink -f /opt/rdf-node/current /opt/rdf-node/previous
   curl -fsS http://127.0.0.1:8791/api/v2/healthz; echo
   ```

   - `current` harus menunjuk `1.5.0-<tanggal>-<pid>`, dan `previous` harus `<RILIS-GROUND-LAMA>`.
   - Preview Ground harus menampilkan health dan DoA dari Edge 1.2.0. Angular sekarang tampil
     karena kodenya sudah JSON.

7. Rollback Ground bila perlu:

   ```bash
   sudo bash /opt/rdf-node/current/scripts/rollback.sh
   readlink -f /opt/rdf-node/current     # harus <RILIS-GROUND-LAMA>
   ```

   Config dan journal tidak dikembalikan otomatis. Bila journal ikut bermasalah, pulihkan dari
   arsip langkah 2 (hentikan service dulu, lalu ekstrak arsip ke `/`).

## B. Raspberry (rdf-edge)

Prosedurnya sama dengan upgrade Edge sebelumnya. Akses Raspberry lewat LAN atau lokal, bukan
hanya lewat T900. Jangan saat terbang.

1. Pra-cek, lalu catat hasil `readlink` sebagai `<RILIS-EDGE-LAMA>`:

   ```bash
   systemctl status rdf-edge.service rdf-control-helper.service --no-pager
   readlink -f /opt/rdf-node/current
   curl -fsS http://127.0.0.1:8790/api/v2/healthz; echo
   ```

2. Backup config dan state:

   ```bash
   sudo systemctl stop rdf-edge.service
   sudo install -d -m 0700 /root/rdf-node-backup
   sudo tar --numeric-owner -cpzf /root/rdf-node-backup/rdf-node-$(date +%Y%m%d-%H%M%S).tgz -C / etc/rdf-node var/lib/rdf-node
   sudo chmod 600 /root/rdf-node-backup/*.tgz
   ```

3. Salin paket lewat LAN (`scp rdf-node-1.5.0.zip rdf@<IP-LAN-RASPBERRY>:/tmp/`), lalu cek
   checksum dan ekstrak seperti langkah A.3.

4. Validasi config dengan kode baru. Harus `exit=0`:

   ```bash
   sudo -u rdf-edge /usr/bin/python3 -B /tmp/rdf-upgrade/rdf-node/run.py doctor --config /etc/rdf-node/config.yaml; echo "exit=$?"
   ```

   Key baru 1.5.0 adalah `testing.clock` (default `null`). Jangan menambahkannya di Raspberry; key
   ini khusus uji di laptop dan ditolak bila `runtime_mode: controlled`. Key baru 1.3.0,
   `mqtt.backup.settings_report_plaintext`, juga boleh dibiarkan tidak ada.

5. Pasang (sekali saja), lalu muat ulang kiosk:

   ```bash
   cd /tmp/rdf-upgrade/rdf-node
   sudo bash install.sh --no-setup --kiosk=rdf
   sudo -u rdf env XDG_RUNTIME_DIR=/run/user/$(id -u rdf) systemctl --user restart rdf-kiosk.service
   ```

6. Verifikasi:

   ```bash
   systemctl status rdf-edge.service rdf-control-helper.service --no-pager
   readlink -f /opt/rdf-node/current /opt/rdf-node/previous
   curl -fsS http://127.0.0.1:8790/api/v2/snapshot | python3 -c 'import json,sys;s=json.load(sys.stdin);print(s["version"],s["contract"],s["link"]["mqtt_control"]["state"],s["link"]["mqtt_bulk"]["state"],s["detection"]["reasons"])'
   ```

   - Harus tercetak `1.5.0 1.5 CONNECTED CONNECTED`, diikuti alasan DoA saat ini (kosong bila
     LIVE).
   - Panel tab **Config** harus menampilkan baris **Versi / kontrak** `1.5.0 / 1.5`.
   - Di Ground, `capabilities` harus membawa `version: "1.5.0"` dan `contract: "1.5"`.
   - `telemetry/health` sekarang membawa `why`.

7. Rollback Edge bila perlu:

   ```bash
   sudo bash /opt/rdf-node/current/scripts/rollback.sh
   readlink -f /opt/rdf-node/current     # harus <RILIS-EDGE-LAMA>
   ```

   Jurnal 1.5.0 tidak mengubah tabel `operations`, jadi rilis lama tetap bisa menulisnya. Hasil
   error di jurnal sekarang membawa field tambahan `message` dan `detail`, yang diabaikan rilis
   lama.

## C. Membersihkan retained yang tidak dipakai lagi

Retained yang masih dipakai 1.5.0 di `sdr/v2/<node_id>/` hanya `availability`, `capabilities`,
dan `state`. Sisa dari rilis lama yang perlu dihapus:

- `config/reported`: dipublish retained oleh Edge 1.1.0. Sejak 1.2.0 settings dikirim lewat
  `settings/reported` (non-retained) setelah `config.get`.
- Command `cmd/...` yang terlanjur retained. Edge menolak setiap command retained
  (`RETAINED_COMMAND_REJECTED`), tetapi broker tetap mengirimkannya setiap kali Edge reconnect, dan
  penolakan itu muncul berulang di panel.
- Topik prefix uji (`sdr/demo/v2/...`) bila pernah dipublish ke broker produksi.

Jangan menghapus retained milik aplikasi lain (misalnya Ground Console) sebelum memastikan
pemiliknya.

ACL hasil `provision-ground.sh` tidak memberi akun mana pun hak baca atau tulis ke
`config/reported`. Karena itu pembersihan memakai akun sementara yang dihapus lagi di akhir.
Lakukan di groundstation, di broker yang dipakai Edge. Langkah yang sama berlaku di broker
cadangan bila dipakai: path file broker berbeda, sesuaikan.

1. Backup ACL dan password file broker, lalu tambah akun sementara:

   ```bash
   B=/etc/rdf-ground-mqtt          # sesuaikan bila broker lain
   sudo cp -a $B/acl $B/acl.before-cleanup
   sudo cp -a $B/passwd $B/passwd.before-cleanup
   sudo mosquitto_passwd $B/passwd rdf-cleanup      # sandi diketik, tidak tampil
   printf '\nuser rdf-cleanup\ntopic readwrite sdr/#\n' | sudo tee -a $B/acl >/dev/null
   sudo systemctl kill -s HUP rdf-ground-mqtt.service   # muat ulang ACL dan password tanpa restart
   ```

2. Lihat semua retained di bawah `sdr/`. Perintah ini hanya membaca:

   ```bash
   read -rs CLEANUP_PW   # ketik sandi rdf-cleanup
   sudo mosquitto_sub -h 127.0.0.1 -p 8883 --cafile /etc/rdf-ground/credentials/ca.crt \
     -u rdf-cleanup -P "$CLEANUP_PW" -t 'sdr/#' --retained-only -W 3 -F '%t %l' 2>/dev/null
   ```

   `sudo` diperlukan karena `ca.crt` hanya bisa dibaca grup `rdf-ground`. Setiap baris berisi
   topik dan panjang payload. Bila yang muncul hanya `availability`,
   `capabilities`, dan `state`, langsung ke langkah 4.

3. Hapus retained yang tidak dipakai, satu per topik (payload kosong dengan flag retain):

   ```bash
   sudo mosquitto_pub -h 127.0.0.1 -p 8883 --cafile /etc/rdf-ground/credentials/ca.crt \
     -u rdf-cleanup -P "$CLEANUP_PW" -r -n -t 'sdr/v2/<node_id>/config/reported'
   ```

   Ulangi untuk setiap topik usang dari langkah 2, lalu jalankan langkah 2 lagi untuk memastikan
   topik itu sudah hilang.

4. Hapus akun sementara dan kembalikan ACL:

   ```bash
   unset CLEANUP_PW
   sudo cp -a $B/acl.before-cleanup $B/acl
   sudo cp -a $B/passwd.before-cleanup $B/passwd
   sudo systemctl kill -s HUP rdf-ground-mqtt.service
   ```

Alternatif yang lebih kasar adalah menghentikan broker lalu menghapus file persistence-nya (di
`/var/lib/rdf-ground-mqtt/`). Cara itu menghapus semua retained, termasuk `availability`,
`capabilities`, dan `state`. Edge 1.5.0 mempublish ulang ketiganya saat reconnect (`state`
langsung, bukan menunggu 60 s), tetapi selama Edge belum tersambung, Ground tidak punya state
terakhir. Pakai cara ini hanya bila langkah 1–4 tidak bisa.

## D. Setelah upgrade

- Jalankan `python3 contract/validate.py` terhadap topik yang diterima Ground (lihat
  `contract/README.md`) untuk memastikan payload sesuai kontrak 1.5.
- Bila uji sinyal diperlukan, ikuti `docs/UJI_SINYAL_437.md`.
