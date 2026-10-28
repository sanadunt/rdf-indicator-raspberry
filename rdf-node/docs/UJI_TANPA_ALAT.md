# Uji tanpa alat: Edge, broker loopback, dan sumber demo di laptop

Panduan ini menjalankan Edge RDF Node di laptop (Linux atau macOS) dengan data KrakenSDR sintetis
dan broker Mosquitto lokal, supaya Ground Console bisa diuji tanpa Raspberry, KrakenSDR, atau T900.
Tidak ada langkah yang menyentuh perangkat sungguhan.

Yang **tidak** dibuktikan uji ini: throughput dan budget T900, PPP, helper root (start/stop SDR,
reboot, shutdown, restart T900), layar sentuh, dan perilaku KrakenSDR asli.

## 1. Kebutuhan

- Python 3.10 atau lebih baru (`python3 --version`). Tidak perlu pip atau venv.
- Mosquitto: `sudo apt install mosquitto mosquitto-clients` (Ubuntu) atau `brew install mosquitto`
  (macOS).
- Checkout repo ini. Semua perintah di bawah dijalankan dari folder `rdf-node/`.

Contoh memakai folder kerja `~/rdf-uji`. Ganti `<sandi-edge>` dan `<sandi-ground>` dengan sandi
uji buatan Anda sendiri, dan jangan memakai sandi produksi.

## 2. Sumber demo KrakenSDR

Terminal 1:

```bash
python3 run.py demo-source ~/rdf-uji/source
```

Setiap 0,5 detik perintah ini menulis `settings.json`, `status.json` (`adc_overdrive` boolean),
`DOA_value.html` (377 field, 360 nilai), dan `doa.xml` (TIME dalam ms, FREQUENCY dalam MHz) dengan
format yang sama seperti KrakenSDR. Hentikan dengan Ctrl+C.

## 3. Broker Mosquitto loopback

Buat akun uji dan konfigurasi broker yang hanya mendengar `127.0.0.1`:

```bash
mkdir -p ~/rdf-uji/broker
mosquitto_passwd -c -b ~/rdf-uji/broker/passwd edge '<sandi-edge>'
mosquitto_passwd -b ~/rdf-uji/broker/passwd ground '<sandi-ground>'
chmod 600 ~/rdf-uji/broker/passwd
cat > ~/rdf-uji/broker/mosquitto.conf <<EOF
listener 1883 127.0.0.1
allow_anonymous false
password_file $HOME/rdf-uji/broker/passwd
EOF
```

Terminal 2:

```bash
mosquitto -c ~/rdf-uji/broker/mosquitto.conf
```

## 4. Konfigurasi Edge laptop

File credential Edge berisi tepat `username` dan `password`:

```bash
printf '{"username":"edge","password":"%s"}\n' '<sandi-edge>' > ~/rdf-uji/edge-mqtt.json
chmod 600 ~/rdf-uji/edge-mqtt.json
```

`~/rdf-uji/edge.yaml` (path harus absolut; ganti `/home/anda` dengan home Anda):

```yaml
runtime_mode: read_only
state_dir: /home/anda/rdf-uji/state
source:
  share_dir: /home/anda/rdf-uji/source
  authority_verified: true
  angle_verified: true
mqtt:
  enabled: true
  host: 127.0.0.1
  port: 1883
  tls: false
  allow_insecure_loopback: true
  control_credentials_file: /home/anda/rdf-uji/edge-mqtt.json
  bulk_credentials_file: /home/anda/rdf-uji/edge-mqtt.json
api:
  port: 18790
  admin_hash_file: /home/anda/rdf-uji/admin-hash.json
testing:
  clock: system
```

`testing.clock: system` adalah cara resmi untuk menguji di luar Raspberry. Tanpa opsi ini, jam
diperiksa lewat `timedatectl`, yang tidak ada di macOS, sehingga jam selalu dianggap belum
tersinkron. Dengan opsi ini:

- Gate data memakai jam OS, sehingga DoA dapat `ok: 1` dan Angular dapat `flags: 63`.
- Jam tetap diberi label jujur: `state.clock` bernilai `TEST`, sedangkan `clk` di `telemetry/health`
  dan `telemetry/doa` tetap `0`. Penerima tidak akan mengira jam sudah tersinkron.
- `stream.set` dan `config.get` dari Ground berjalan. Command yang butuh helper root tetap ditolak
  `CLOCK_UNTRUSTED`, lalu `HELPER_UNAVAILABLE` karena di laptop memang tidak ada helper.
- Config ditolak `TEST_CLOCK_REQUIRES_READ_ONLY` bila `runtime_mode: controlled`. Jangan pakai
  opsi ini di Raspberry.

Terminal 3:

```bash
python3 run.py edge --config ~/rdf-uji/edge.yaml
```

Edge mencetak `JAM UJI: testing.clock=system ...`. Panel lokal ada di `http://127.0.0.1:18790`
(lihat saja; tidak ada PIN admin di laptop).

## 5. Memeriksa topik

```bash
mosquitto_sub -h 127.0.0.1 -u ground -P '<sandi-ground>' -t 'sdr/v2/uav-01/#' -v
```

Periksa payload terhadap kontrak (lihat [contract/README.md](../contract/README.md)):

```bash
mosquitto_sub -h 127.0.0.1 -u ground -P '<sandi-ground>' -t 'sdr/v2/uav-01/telemetry/doa' \
  | python3 contract/validate.py telemetry/doa -
```

Yang diharapkan:

- `availability`, `capabilities`, dan `state` (retained) dengan `contract: "1.5"`, `version: "1.5.0"`,
  dan `state.clock: "TEST"`.
- `telemetry/health` tiap detik dengan `clk: 0` dan `why: []`.
- `telemetry/doa` tiap sampel baru dengan `ok: 1`, `clk: 0`, dan `reason: []`.
- `telemetry/diagnostic/doa` tiap 3 detik dari `doa.xml`, serta `telemetry/angular` lewat Bulk.

Untuk menguji tampilan saat data tidak valid, hentikan `demo-source` (Ctrl+C). DoA berhenti karena
tidak ada sampel baru, dan `health.why` berisi `STATUS_STALE_OR_UNAVAILABLE`, `DAQ_NOT_HEALTHY`,
`NO_FRESH_DOA`, dan `DOA_FILE_NOT_UPDATING`. Jalankan lagi untuk kembali normal.

## 6. Ground Console

Arahkan Ground Console ke broker `127.0.0.1:1883` tanpa TLS dengan user `ground`, root topik
`sdr/v2/uav-01`. Data Out (cadangan) tidak diperlukan untuk uji ini.

## 7. Membersihkan

Hentikan ketiga terminal, lalu hapus `~/rdf-uji`. Tidak ada service, file `/etc`, atau setting
sistem yang dibuat.
