# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@rdf-node/AGENTS.md

The import above is the main repository guide: architecture, module boundaries, conventions, and testing limits. This file adds only what that guide leaves out. There are two AGENTS.md files: `rdf-node/AGENTS.md` is newer than the root one, but both still describe angular chunk reassembly, Ground receipts, and local approval for privileged/Ground commands (the "Ground data" and "Commands" bullets). All three are out of date; "Current behavior" below wins wherever they disagree.

## Layout

- `rdf-node/` is the only runnable package. Run every command from it.
- Workspace-root `*.md` files (mostly Indonesian) and `rdf-node-1.0.0.zip` are handoff and planning artifacts, not the source of truth. `rdf-node/docs/` describes the as-built system.
- Most docs and user-facing strings (CLI messages, panel UI) are in Indonesian. Keep new UI and CLI text in Indonesian to match.

## Commands

```bash
cd rdf-node
python3 run.py selftest                 # full unittest suite (~80 s; verbose)
python3 run.py demo --port 18790        # temp state, MQTT off, no hardware writes
bash scripts/check-package.sh           # SHA256SUMS check + compileall + bash -n + node --check + selftest
```

Running a single suite, class, or test. Tests import `rdf_node` and each other (`from test_core import ...`, `from broker_fixture import ...`), so run from `tests/` with `src` and `vendor` on the path:

```bash
cd rdf-node/tests
PYTHONPATH=../src:../vendor python3 -m unittest test_core.ConfigTests
PYTHONPATH=../src:../vendor python3 -m unittest test_system.ApiTests.<test_method>
```

`run.py` puts `src/` and `vendor/` (bundled PyYAML) on `sys.path` itself. There is no install step for development.

All roles share one CLI (`src/rdf_node/cli.py`): `run.py edge|ground|demo` start the HTTP server plus Agent or Ground receiver; `helper` runs the privileged Unix-socket helper; `doctor`, `setup`, `set-pin`, `import-bundle`, and `controls approve|shutdown-reconcile` are deployment tools. Non-demo roles default to `/etc/rdf-node/config.yaml` (Ground: `/etc/rdf-ground/config.yaml`), so use `demo` for local runs.

## Gotchas

- **SHA256SUMS:** `rdf-node/SHA256SUMS` lists checksums for the packaged files. Both `check-package.sh` and `install.sh` fail if any of them changed. When you edit a listed file, recompute only that file's existing entry. Don't add new entries unless the release scope requires them.
- **Running tests as root:** `test_core.HelperTests.test_shutdown_reconcile_requires_local_root` expects the caller to be non-root, so it fails under uid 0 (as in cloud containers). Under a non-root user it passes. Ping- and openssl-dependent tests skip when those tools are missing.
- **Web panel:** `web/app.js` (Edge panel) and `web/ground.js` (Ground preview) are plain scripts with no build step. `check-package.sh` syntax-checks them only when `node` is installed.

## Current behavior (supersedes older planning docs)

- **Angular telemetry:** each frame goes out as one JSON PUBLISH holding metadata plus exactly 360 `values`. There are no chunks, no Q16/U8 quantization, and no Base64. Older RDF2 binary-chunk consumers are incompatible.
- **No Ground receipts:** Edge gets no application-level receipt from Ground. Ground subscribes to Edge's `state`, `capabilities`, `settings/reported`, `availability`, `ack/config`, and `ack/operation`.
- **Ground commands need no local approval:** Ground's supported write commands run without a remote grant or per-action root approval. Root approval (`rdf-node controls approve ...`) gates only Edge-panel actions. The remaining safeguards are the session/boot, clock, revision, health, and journal checks plus the helper's fixed targets. Edge does not authenticate MQTT publishers, so command access depends on broker ACLs. See `docs/CONTROL.md`.
- **Topics:** all topics are suffixes under `sdr/v2/{node_id}` (demo: `sdr/demo/v2/{node_id}`). The authoritative per-topic table (direction, channel, QoS, retain, expiry) is in `docs/MQTT_TOPIC_SUMMARY.md`. The wire contract is in `docs/PROTOCOL.md`.
- **Channel split:** the Control MQTT client publishes telemetry, state, and ACKs and is the only client that subscribes to commands. The Bulk client publishes normal `telemetry/angular` only and has no command rights.
- **Ports:** Edge API/panel on `127.0.0.1:8790`, Ground receiver/preview on `8791`. The privileged helper listens on the Unix socket `/run/rdf-node-control/control.sock`.
