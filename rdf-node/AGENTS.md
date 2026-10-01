# Repository Guidelines

## Project Overview

RDF Node bridges existing SDR-DoA/KrakenSDR output on Raspberry Pi to an Ubuntu Ground receiver. It reads native `_share` files, publishes gated telemetry and approved commands over the existing PPP/T900 link, and serves a local 480x320 status panel. Read-only is the default. Do not take ownership of the SDR engine or PPP, and do not transport raw IQ.

## Architecture & Data Flow

- Edge data: native `_share` files -> `source.py` stable parsing and provenance/freshness/DAQ/config/clock/angle gates -> `agent.py` cached snapshots and scheduler -> separate MQTT control and bulk clients -> existing PPP/T900 link.
- Ground data: `ground.py` validates telemetry and session/revision evidence, reassembles angular chunks, updates its local API/preview, and publishes receipts. MQTT connected or broker-accepted is not proof of Ground receipt.
- Panel: `api.py` serves loopback HTTP and static UI. The browser reads API snapshots; it does not read native SDR files or hold MQTT credentials.
- Commands: `control.py` validates policy and journals work through `journal.py`; one worker calls the restricted Unix-socket `helper.py`. Privileged operations require explicit local approval and evidence. Persisted settings are not proof of runtime application.
- Keep missing or stale evidence unknown/blocked, not healthy by default. Service active, DAQ health, MQTT readiness, Ground receipt, and verified hardware state are distinct.

**MQTT deployment behavior:** Edge supports TCP or WebSocket, with verified TLS enabled by default. `tls: false` explicitly selects plain TCP or `ws://`; broker credentials and MQTT payloads are then unencrypted, so use only on an approved trusted link. The built-in Ground provisioner still creates a TLS listener; plaintext deployments need a matching broker listener, path, and ACL.

## Key Directories

- `src/rdf_node/`: Python runtime, source gates, orchestration, MQTT, HTTP API, Ground receiver, command policy, helper, and journal.
- `web/`: plain HTML/CSS/JavaScript for the local panel and Ground preview.
- `tests/`: standard-library `unittest` suites and independent loopback MQTT broker fixture.
- `config/`: canonical example configuration and defaults.
- `scripts/`, `deploy/`: package QA, provisioning/rollback/uninstall, systemd units, and kiosk launchers.
- `docs/`: as-built architecture, protocol, control, operations, and test limitations. `docs/plans/` is planning material, not the implementation source of truth.
- `vendor/yaml/`: bundled pure-Python PyYAML. `evidence/`: recorded test and browser QA output, not a rerunnable test command.

## Development Commands

Run from the package root:

```bash
python3 run.py selftest
python3 run.py demo --port 18790
bash scripts/check-package.sh
```

`selftest` runs the `unittest` suite. The demo uses temporary state, keeps MQTT off, and does not write hardware; choose an unused port. There is no separate build or documented standalone lint command. `check-package.sh` runs compilation, shell syntax checks, optional Node syntax checks, and `selftest`; it also uses `sha256sum` when `SHA256SUMS` is present.

## Code Conventions & Common Patterns

- Use Python `snake_case` and follow the nearby formatting. Keep runtime work in bounded threads, loops, and queues; do not introduce `asyncio` or new package managers without a concrete need.
- `config.py` merges overrides into `config/example.yaml` and strictly rejects unknown keys, invalid types, and invalid values. Keep defaults and validation aligned.
- Keep responsibilities in their modules: parsing/gates in `source.py`, scheduling/snapshots in `agent.py`, wire encoding in `codec.py`, transport in `mqtt.py`/`mqtt_ws.py`, HTTP/auth in `api.py`, command policy in `control.py`, and privileged allow-list operations in `helper.py`.
- Preserve explicit stale, unknown, rejected, and evidence-pending outcomes. Do not infer sensor units, orientation, receipt, or hardware success.
- Keep control and bulk credentials separate. Credential files contain exactly non-empty `username` and `password` strings; never hard-code, log, or return secrets through the API.
- Preserve loopback API binding, authentication/CSRF checks, durable command journaling, single-worker mutation, explicit approvals, and the read-only default.

## Important Files

- `run.py`, `src/rdf_node/cli.py`: source launcher and CLI dispatch.
- `src/rdf_node/source.py`, `agent.py`, `ground.py`: source collection, edge orchestration, and Ground receiver.
- `src/rdf_node/mqtt.py`, `mqtt_ws.py`, `config.py`, `web/app.js`: MQTT wire/transport, transport policy, and UI settings. For the plain TCP/`ws://` requirement, do not update only the low-level client; cover normal config and Agent/UI paths too.
- `src/rdf_node/api.py`, `control.py`, `journal.py`, `helper.py`: local API/auth, command safety, durable state, and privileged operations.
- `config/example.yaml`: canonical defaults. `scripts/check-package.sh`: package QA entry point.
- `docs/IMPLEMENTATION.md`, `PROTOCOL.md`, `MQTT_GROUND.md`, `CONTROL.md`, `OPERATIONS.md`, `TEST_REPORT.md`: as-built behavior and verification limits.

## Runtime/Tooling Preferences

Target Python 3.10+ on Linux/systemd; deployed services use `/usr/bin/python3` and the standard library plus bundled pure-Python PyYAML. No pip, npm, Docker, or SDR Conda runtime is required. The panel uses OS Chromium in a graphical session; backend operation does not require a display. Installer and service changes target Linux/systemd hosts, not routine local development.

## Testing & QA

- Use `python3 run.py selftest` for behavioral tests. Tests use temporary files, loopback sockets, an independent MQTT fixture, and mocks for systemd/reboot actions. Add deterministic regression coverage in the existing suites for consumer-visible changes.
- Relevant suites: `tests/test_core.py` (config/source/codec/journal/helper/wire), `tests/test_network.py` (MQTT network/auth/TLS/WSS), `tests/test_websocket.py` (RFC 6455 framing/handshake), and `tests/test_system.py` (API and end-to-end fixture flow). Config and API/Agent tests cover `tls: false` TCP and WebSocket with username/password using a loopback fixture. They do not prove interoperability with a remote Ground broker, Mosquitto, Raspberry, or T900.
- Fixture tests do not prove Mosquitto interoperability, Raspberry installation, display/touchscreen behavior, T900 throughput, or real lifecycle commands. Keep those as explicit device-acceptance checks. `docs/TEST_REPORT.md` and `evidence/` are recorded QA results, not claims about the current machine or hardware.