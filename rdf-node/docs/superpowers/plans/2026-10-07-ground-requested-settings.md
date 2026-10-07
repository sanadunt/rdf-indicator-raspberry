# Ground-Requested Settings and Receipt Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Ground-to-Edge receipt traffic, let Ground request the native settings file on demand, and stop requiring a matching Ground config revision for LIVE telemetry.

**Architecture:** Reuse the existing `cmd/config/get` command. Edge responds only to that request on a new non-retained `settings/reported` topic; the payload carries the exact UTF-8 `settings.json` text plus request/session metadata. Ground’s existing Refresh action remains the request trigger. Ground removes revision equality only for LIVE DoA/Angular and keeps session, freshness, DAQ, health, and evidence-flag checks. Existing mutation commands and approval gates remain.

**Tech Stack:** Python 3.10+, standard-library `unittest`, existing MQTT 5 client, plain HTML/CSS/JavaScript.

**Spec:** User-approved choices in this conversation, summarized in “Chosen contract” below.

## Chosen contract

- Remove `ground/receipt` publishing, subscription, counters, alerts, UI status, and receipt-derived `ground_sync`.
- Keep the existing `cmd/config/get` request and `ack/config` command result. Ground may submit this read-only request when MQTT is ready and the current Edge `state` supplies `sid` and `boot`; fresh health is not required for `config.get` only. All other operations retain their current freshness and approval gates.
- Edge sends no automatic config report at startup, reconnect, or revision change. A request produces one `settings/reported` QoS 1, non-retained response with 30-second expiry. Its `settings_json` field preserves the exact native file text; `v`, `sid`, `boot`, `id`, `rev`, and `t` bind it to the request and Edge session.
- Refuse raw-settings export if MQTT TLS is disabled or the complete MQTT payload exceeds the client’s 8 KiB limit. Return a specific command rejection; do not truncate or chunk the file.
- Remove Ground’s `revision == node_config.rev` gate from LIVE DoA and Angular. Keep all other session, sequence, freshness, health, DAQ, and trust-flag checks. This does not promote Edge frames whose source-authority or angle evidence is still UNVERIFIED.
- Remove the obsolete receipt config key and receipt timing thresholds. Keep config mutations and other remote commands unchanged.
- Ground may display the requested raw file in its loopback-only companion UI; retain a safe allowlisted settings view for the existing patch form.

## Global constraints

- Target Python 3.10+ on Linux with systemd; use the standard library and bundled pure-Python PyYAML.
- Runtime work uses threads and bounded loops/queues, not `asyncio`.
- Preserve explicit unknown, stale, rejected, and evidence-pending outcomes; do not infer sensor units, orientation, source authority, or hardware success.
- MQTT payloads are bounded at 8 KiB; keep the raw settings response non-retained and require TLS.
- Do not weaken local approval, command idempotency, DAQ, source-authority, angle, or freshness gates.

## Review Focus

1. **Raw settings file exceeds 8 KiB:** `config.get` is rejected with a specific error; no truncated or repeated response. Test in Task 1.
2. **MQTT TLS is disabled:** raw settings are not published. Test in Task 1.
3. **Settings response belongs to an old request, boot, or session:** Ground rejects it and leaves the current cache unchanged. Test in Task 2.
4. **Telemetry revision differs but other evidence is valid:** Ground accepts it as LIVE; missing health, DAQ, session, freshness, or flags still prevents LIVE. Test in Task 2.
5. **Ground health is absent or stale:** read-only `config.get` remains available while mutations remain blocked. Test in Task 2.

---

### Task 1: Edge on-demand settings response and receipt removal

**Files:**
- Modify: `src/rdf_node/source.py` — retain the stable-read `settings.json` bytes.
- Modify: `src/rdf_node/agent.py` — remove receipt state/subscription/reporting; publish a settings response only after a request.
- Modify: `src/rdf_node/control.py` — allow `config.get` only from the Ground MQTT controller and reject insecure or oversized exports.
- Modify: `src/rdf_node/config.py`, `config/example.yaml` — remove receipt-only legacy setting and thresholds.
- Test: `tests/test_core.py`, `tests/test_system.py`.

**Interfaces:**
- `Source.raw_settings: bytes` contains the exact stable-read file contents; Edge exports it only after a successful current source read.
- `Agent.settings_request_id: str | None` is `None` until `config.get` queues a response, then clears only after the response enters the outbound queue.
- `Ground.settings_request_id: str | None` identifies the one pending Ground request; clear it on matching response, session change, or rejected publish.
- Only the Ground MQTT controller may originate `config.get`; reject Edge-local requests because no local response consumer exists.
- `settings/reported` payload is a JSON object with `v`, `sid`, `boot`, `id`, `rev`, `t`, and `settings_json`; `settings_json` is the exact UTF-8 file text. Publish QoS 1, non-retained, expiry 30 seconds.

- [x] Add `test_config_get_returns_raw_settings_once_without_auto_report` and assert exact `settings.json` text, response metadata, QoS 1, non-retained, and no export on startup/reconnect.
- [x] Add `test_config_get_rejects_unavailable_tls_or_oversized_settings` and assert a specific rejection with no response publication.
- [x] Run the focused tests and confirm they fail because Edge currently publishes retained `config/reported`.
- [x] Implement stable raw-byte retention and one-shot `settings/reported` publishing; reject unavailable/stale source, TLS-off, and complete payloads over 8192 bytes without truncation.
- [x] Remove `ground/receipt` handling, receipt-derived snapshot fields/alerts, and obsolete receipt config values; verify the legacy config key is no longer accepted.
- [x] Run `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p 'test_core.py'` and the focused Edge tests in `test_system.py`.

### Task 2: Ground request, response, and telemetry policy

**Files:**
- Modify: `src/rdf_node/ground.py` — remove receipt publishing and old config-report dependency; request/validate the raw response; remove only the revision gate.
- Test: `tests/test_system.py`.

**Interfaces:**
- `Ground.submit_command({'op': 'config.get'})` requires a ready MQTT client and current `sid`/`boot`, but not fresh health.
- Ground stores `settings_json` only when the response matches the current `sid`, `boot`, and pending request `id`; it parses the content as a strict JSON object for the safe settings view.
- Ground overwrites caller-supplied `base_rev` with the current `node_state.cfg`; config report arrival is not a prerequisite for telemetry or commands.

- [x] Add `test_config_get_without_fresh_health_still_requests_settings` and `test_mutating_command_requires_fresh_health`.
- [x] Add `test_settings_report_requires_current_request_session_and_boot` and assert an old response leaves the current settings cache unchanged.
- [x] Add `test_angular_live_accepts_revision_mismatch_with_valid_evidence`; also assert wrong session, stale health, and missing required flags remain rejected or diagnostic.
- [x] Add `test_ground_settings_request_roundtrip_without_receipt` using the broker fixture; assert the matching non-retained settings response arrives and no client publishes `ground/receipt`.
- [x] Implement the Ground request/response path and remove only the Ground revision comparisons; retain the other integrity gates.
- [x] Run `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p 'test_system.py'`.

### Task 3: Update Edge and Ground panels

**Files:**
- Modify: `web/index.html`, `web/app.js` — remove receipt/sync UI and the Edge-local Refresh action; list `settings/reported` as an outgoing topic.
- Modify: `web/ground.html`, `web/ground.js` — keep Ground's Refresh action as the explicit request; show requested raw JSON and the safe subset; remove receipt wording denoting an app-level confirmation.

**Interfaces:**
- The Ground `Refresh config` action continues to call `config.get`; it does not require health freshness, but still requires an active Ground session and MQTT connection.
- Raw settings remain served only by the existing loopback API; never add MQTT credentials to the browser.

- [x] Update panel copy and response rendering to the new request/response contract.
- [x] Run JavaScript syntax checks.
- [x] Launch the demo and Ground companion, open both actual browser surfaces, and verify no receipt/sync status remains and the request/response state is understandable.
- [x] Remove Edge-local `Refresh config`; Ground remains the sole settings request trigger.

### Task 4: Update protocol and operations documentation

**Files:**
- Modify: `docs/MQTT_TOPIC_SUMMARY.md`, `docs/MQTT_GROUND.md`, `docs/PROTOCOL.md`, `docs/IMPLEMENTATION.md`, `docs/OPERATIONS.md`, `docs/TEST_REPORT.md`, `README.md`, `CHANGELOG.md`, `scripts/provision-ground.sh`.

- [x] Replace `config/reported` and `ground/receipt` descriptions with the request-only `settings/reported` contract and its limits.
- [x] Document that TLS is required for raw settings export, the response is non-retained and capped at 8 KiB, telemetry does not wait for config, and remaining source/evidence gates can still keep frames UNVERIFIED.
- [x] Remove legacy receipt workflow/config instructions and update test coverage descriptions.
- [x] Run `python3 run.py selftest` and `bash scripts/check-package.sh`; inspect outputs before claiming success.
