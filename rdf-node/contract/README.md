# Kontrak MQTT RDF Node

Folder ini memuat kontrak MQTT Edge dalam bentuk yang bisa dibaca mesin. Penjelasan untuk manusia
ada di `docs/PROTOCOL.md` (wire contract) dan `docs/MQTT_TOPIC_SUMMARY.md` (QoS, retain, expiry,
ukuran).

## Versi

- `capabilities.version` adalah nomor rilis paket, misalnya `1.5.0`.
- `contract` di `availability`, `capabilities`, dan `state` adalah revisi kontrak (`major.minor`):
  nomor rilis terakhir yang mengubah topik atau field. Rilis patch tidak mengubah kontrak.
- Field tambahan selalu boleh. Consumer harus mengabaikan field yang tidak dikenalnya. Minor baru
  hanya menambah field atau nilai. Major baru boleh menghapus atau mengubah arti field.
- Edge sebelum 1.5.0 tidak mengirim `contract` dan selalu melaporkan `version: "1.0.0"`. Anggap
  `contract` yang hilang sebagai "sebelum 1.5".

## Skema

`schema/<topik>.json`: satu JSON Schema (subset draft 2020-12) per topik yang dipublish Edge.
`/` diganti `.`, misalnya `telemetry/doa` menjadi `telemetry.doa.json`. `cmd.json` adalah envelope
semua command Ground ke Edge di `cmd/#`.

| Skema | Topik |
|---|---|
| `availability.json` | `availability` (online, Last Will, farewell) |
| `capabilities.json` | `capabilities` |
| `state.json` | `state` |
| `telemetry.health.json` | `telemetry/health` |
| `telemetry.health.detail.json` | `telemetry/health/detail` |
| `telemetry.doa.json` | `telemetry/doa` (`ok: 1` atau berlabel `ok: 0`) |
| `telemetry.diagnostic.doa.json` | `telemetry/diagnostic/doa` |
| `telemetry.angular.json` | `telemetry/angular` |
| `telemetry.diagnostic.angular.json` | `telemetry/diagnostic/angular` |
| `settings.reported.json` | `settings/reported` |
| `ack.config.json`, `ack.operation.json` | `ack/config`, `ack/operation` |
| `cmd.json` | `cmd/#` (Ground ke Edge) |

## Memeriksa payload

`validate.py` hanya memakai library standar Python dan berjalan di Raspberry, groundstation, atau
laptop:

```bash
python3 contract/validate.py telemetry/doa payload.json
mosquitto_sub -h <broker> -t 'sdr/v2/uav-01/telemetry/doa' | python3 contract/validate.py telemetry/doa -
```

Setiap baris mencetak `OK` atau `INVALID:` dengan alasannya. Exit code 1 bila ada yang tidak
sesuai. `tests/test_contract.py` memeriksa payload yang benar-benar dibangun Edge terhadap skema
ini, jadi skema dan kode tidak bisa bergeser diam-diam.
