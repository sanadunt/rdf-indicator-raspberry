# Repository Guidelines

## Project Overview

RDF Node 1.0.0 bridges existing SDR-DoA/KrakenSDR output on a Raspberry Pi to an Ubuntu Ground station. The edge service reads native local files, sends telemetry and approved commands over MQTT/TLS on the existing PPP/T900 link, and serves a local 480x320 status panel. A separate Ground receiver provides receipt and preview APIs. The default operating mode is read-only. Preserve the existing SDR and PPP ownership boundaries; raw IQ is not transported.

The runnable package is `rdf-node/`. The workspace root also contains handoff, planning, and release artifacts. Run package commands from `rdf-node/`; treat planning documents as intent and `rdf-node/docs/IMPLEMENTATION.md` as the as-built reference.

## Architecture & Data Flow

- `source.py` stably reads native `_share` files and gates data on parsing, provenance, freshness, DAQ, configuration, clock, and angle evidence. `agent.py` collects cached snapshots and schedules API and MQTT work.
- `api.py` serves the loopback HTTP API and static panel. The browser reads snapshots through the API; it does not read native SDR files or hold MQTT credentials. The panel is local status UI, not a prerequisite for telemetry.
- `mqtt.py` implements the bounded MQTT 5 client and separate control/bulk paths. `ground.py` validates incoming telemetry, reassembles angular chunks, sends receipts, and accepts Ground-side command intents.
- Commands flow through `control.CommandManager`, a durable SQLite journal, and one mutation worker to the restricted Unix-socket `helper.py`. Privileged operations require explicit local approval and evidence checks.

Keep these states distinct: service active is not proof of valid DAQ data; MQTT connected is not Ground receipt; persisted settings are not proof of runtime application. Missing evidence remains unknown or blocked, not healthy by default.

## Key Directories

- `rdf-node/src/rdf_node/`: Python application and CLI modules.
- `rdf-node/web/`: local edge panel and Ground preview HTML/CSS/JavaScript.
- `rdf-node/tests/`: `unittest` suites and independent loopback MQTT broker fixture.
- `rdf-node/config/`: example YAML configuration and defaults.
- `rdf-node/deploy/systemd/`, `rdf-node/deploy/kiosk/`: service and graphical-session units/launchers.
- `rdf-node/scripts/`: package checks, provisioning, rollback, uninstall, and service helpers.
- `rdf-node/docs/`: implementation, protocol, control, operations, and test references; `docs/plans/` holds planning copies.
- `rdf-node/vendor/yaml/`: vendored pure-Python PyYAML. `rdf-node/evidence/`: recorded test and browser QA artifacts.

## Development Commands

Run these from the package directory:

```bash
cd rdf-node
python3 run.py selftest
python3 run.py demo --port 18790
bash scripts/check-package.sh
```

`selftest` runs the standard-library test suite. The demo uses temporary state, leaves MQTT off, and does not write hardware; choose an unused port. The package check runs Python compilation, shell syntax checks, optional Node syntax checks, and `selftest`; checksum verification requires `sha256sum` when `SHA256SUMS` is present. There is no separate application build or documented standalone lint target. Installation scripts are for target Linux/systemd hosts, not routine local development.

## Code Conventions & Common Patterns

- Keep module boundaries: source parsing/gates in `source.py`, orchestration and snapshots in `agent.py`, wire encoding in `codec.py`, MQTT transport in `mqtt.py`, HTTP/auth in `api.py`, command policy in `control.py`, and privileged allow-list operations in `helper.py`.
- Follow existing Python naming (`snake_case` functions/modules, `PascalCase` classes) and nearby formatting. Configuration is merged from `config/example.yaml` and strictly validated; unknown or invalid values should fail rather than silently fall back.
- Runtime work uses threads and bounded loops/queues, not `asyncio`. Commands are serialized through one worker and journaled; retain those limits and idempotency behavior.
- Use shared strict/stable/atomic helpers in `util.py` where applicable. Preserve explicit unknown, stale, rejected, and evidence-pending outcomes; do not infer sensor units, orientation, receipt, or hardware success.
- Keep the HTTP server loopback-only and retain the edge/helper privilege split. Do not widen helper permissions, bypass TLS validation, enable controls by default, or take over PPP/SDR/display-driver configuration.
- No formatter or linter configuration is documented; avoid unrelated reformatting.

## Important Files

- `rdf-node/run.py`, `rdf-node/src/rdf_node/cli.py`: source launcher and CLI entry points (`edge`, `ground`, `demo`, `selftest`, setup and control commands).
- `rdf-node/src/rdf_node/agent.py`, `source.py`, `api.py`, `mqtt.py`, `ground.py`: primary runtime paths.
- `rdf-node/src/rdf_node/control.py`, `helper.py`, `journal.py`, `codec.py`, `config.py`: command safety, persistence, telemetry encoding, and configuration.
- `rdf-node/config/example.yaml`: canonical configuration defaults.
- `rdf-node/install.sh`, `rdf-node/scripts/check-package.sh`, `rdf-node/deploy/`: installation, package QA, and service definitions.
- `rdf-node/docs/IMPLEMENTATION.md`, `PROTOCOL.md`, `CONTROL.md`, `TEST_REPORT.md`: as-built architecture, wire contract, command policy, and verification limits.

## Runtime/Tooling Preferences

Target runtime is Python 3.10+ on Linux with systemd, using `/usr/bin/python3`, the standard library, and bundled pure-Python PyYAML. No pip, npm, Docker, or frontend build step is required. The panel uses plain HTML/CSS/JavaScript and OS Chromium in the graphical session. Do not use the SDR Conda environment as the service runtime.

## Testing & QA

- Tests use Python's `unittest`; `python3 run.py selftest` discovers `tests/test_*.py`. Add behavioral regression coverage in the existing suites for consumer-visible changes.
- Tests isolate file state with temporary directories, use loopback sockets and an independent MQTT fixture, and mock systemd/reboot actions. The checked-in `docs/TEST_REPORT.md` records 113 passing tests for its release run; this is not a claim about the current machine or hardware.
- Fixture success does not prove interoperability with a real Mosquitto broker, Raspberry installation, display, source data, PPP/T900 throughput, or real lifecycle commands. Keep those as explicit device-acceptance checks; never describe mocked reboot or fixture-broker tests as hardware/integration proof.
- `evidence/browser-qa.json` and screenshots are recorded QA output, not a rerunnable browser test command. Re-exercise affected paths when changing the panel or API.
