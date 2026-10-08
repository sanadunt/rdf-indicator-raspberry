# Changelog

## Unreleased

- Edge panel black screen: `Config > Layar hitam` blanks immediately; the automatic timeout is a
  tap-to-choose setting (off, 1, 5, 15 min). The overlay is pure black with a hint that fades out,
  the waking tap is swallowed so it cannot press a control underneath, and a new error alert wakes
  the screen. Tidied dialogs: icons throughout, Profil options with descriptions and the active
  profile marked, Kontrol grouped into Stack SDR and Raspberry, Batal on every destructive
  confirmation, Tampilan as tap-to-save chips, grouped MQTT settings, and an on-screen numeric
  keypad for frequency entry (the kiosk has no physical keyboard). Link, Sistem, and Config rows
  gain icons.

- Edge panel: fading trail of recent gate-valid bearings on the dial (cleared on any invalid
  sample or new session); colour-grouped stage chip for the last operation with a short toast on
  terminal stages (stage codes stay verbatim, so `PERSISTED_UNVERIFIED` remains amber); warn/bad
  dots on the Link, Sistem, Config, and Data tabs; horizontal touch swipe between tabs. Policy
  discards (`DIAGNOSTIC_NOT_NEEDED`, `BULK_PAUSED_*`) show as skipped instead of failed. Add a
  red low-luminance `night` display theme, now accepted by config and API validation.
- Ground companion: grouped card layout, health tiles decoded from `telemetry/health` codes (dimmed
  while health is stale; raw JSON kept behind a toggle), a status pill in the header, separated
  SDR-stack and host action groups, and a HiDPI polar plot with degree labels, peak dot, and a DoA
  needle only under `theta_mirror` for a valid, non-stale detection. The STALE plot style now
  applies; the old `.plot.stale` selector never matched the canvas.

- Edge panel: draw the latest 360-value Angular frame as a per-frame-scaled curve inside the DoA
  dial, only under `theta_mirror` and only when its `q` matches the displayed gate-valid detection
  (the curve hides after 2 s without a matching frame). Tap the dial for a full-screen
  far-reading mode with a larger dial, 68 px angle, frequency, link/DAQ status, and the active
  alert. `Config > Tampilan` now exposes `blank_after_seconds` (off, 1, 5, 15 minutes).

- Refresh the 480x320 Edge panel for small touchscreens. Utama gets a relative-DoA dial (0 at the
  top, clockwise, matching the Ground plot) that moves only for gate-valid detections; raw or
  UNVERIFIED angles stay numeric with the needle hidden. Status values gain colour dots, Link and
  Sistem use segmented view toggles, the Data topic table becomes a tappable 11-12 px list instead
  of 9 px columns, the nav uses SVG icons, and the Config hint no longer hides behind the nav.
  Hover styles apply only on hover-capable pointers so taps do not leave sticky highlights.

- Remove remote grants and per-action root approvals from supported Ground writes. Ground retains
  command validation, journal, fixed-target checks, and UI confirmations; Edge does not authenticate
  MQTT publisher identity, so broker credentials and command-topic ACLs remain the trust boundary.
  Local Edge lifecycle, PPP, reboot, and shutdown actions use persistent one-time root approvals,
  not a timed maintenance lease. The panel retains Admin PIN, capability, and final-action
  confirmation gates. Lifecycle intent recovery preserves actor origin and audited-target checks.

- Replace Ground receipt/config sync with an explicit `config.get` settings export. Edge sends
  exact native UTF-8 settings text on `settings/reported` only after a request, with verified
  TLS, QoS 1, no retention, 30 s expiry, and an 8 KiB compact-envelope limit. Ground does not
  require fresh health for this read-only request; unavailable, stale, TLS-off, and oversized
  reports fail. LIVE DoA/Angular no longer require revision equality; other evidence gates remain.

- Accept settings exports only from Ground's MQTT command path; remove the Edge-local Refresh config action, which had no response consumer.

- Add direct Ground controls, a five-field safe-settings form, and fixed `t900-ppp.service`
  restart. Ground separates persistence from runtime proof and systemd acceptance from later fresh
  health; PPP helper readiness is checked on each request.

- Publish Bulk Angular without a Ground application confirmation. The retired
  `telemetry.require_ground_receipt_for_bulk` key is no longer accepted; remove it from existing
  config because strict validation rejects unknown fields.

- Default lifecycle setup to `rdfsdr.service` when registered and require an audited fixed target for Ground SDR controls; Ground reboot/shutdown retain explicit UI confirmation and durable challenges.

- Publish diagnostic `doa.xml` every 3 s when available, independent of normal DoA validity.
  Ground keeps it `UNVERIFIED`, separate from live DoA and commands.

- Replace RDF2/Q16/U8 Angular chunk transport with one JSON PUBLISH containing all 360 source
  values on normal and diagnostic topics. Ground validates session, freshness, health, and flags
  before marking LIVE; the reported revision is metadata, not a LIVE equality gate. Edge and
  Ground must be upgraded together; byte-budget pacing can extend profile and diagnostic intervals.

- Distinguish local API outage from stale cached data in both panels; add immediate manual retry while regular polling continues, without automatic service or SDR recovery.
- Prevent duplicate shutdown scheduling per boot, retain unresolved outcomes across all journal pages/sessions, and add root-only systemd-evidence-checked local reconciliation.
- Keep ambiguous shutdown results `OUTCOME_UNKNOWN`; tests mock the systemd action and never power off a host.

- Replace local admin passwords with six-digit PIN login and a touch keypad on the Raspberry panel.
- Keep admin PIN sessions alive while the panel is actively used; the rolling inactivity timeout remains enabled.
- Add saved accent-color and font preferences to the Config panel.
- Existing installations must set a new PIN locally with `rdf-node set-pin`.
- Launch kiosk Chromium with `--disable-gpu`; on the Raspberry, EGL config errors were logged with no `/dev/dri`, and the panel rendered with software rendering.
- Allow the RDF edge systemd sandbox to use AF_NETLINK for PPP interface discovery.
- Probe the configured PPP peer with one interface-bound ICMP echo about every five seconds; a missing reply does not mark PPP down.
- Add a bottom Data page with MQTT broker settings and a read-only topic/payload inventory.
- Show each outbound MQTT topic's latest publish result in the Data table. QoS 0 socket writes
  and QoS 1 broker PUBACK describe Edge-to-broker delivery only; Ground processing is not reported
  back to Edge.
- Add a touch keyboard for MQTT host, port, client ID, WebSocket path, and CTRL/BULK credentials.
- Support plaintext MQTT/TCP and `ws://` with either anonymous broker access or per-channel credentials; TLS remains enabled by default.
- Keep CTRL/BULK credentials separate and reconfigure clients live after admin save. Plaintext exposes credentials and payloads; use only on a trusted link.
- Retain verified TCP/TLS and WSS, including MQTT binary frames and the `mqtt` WebSocket subprotocol.
- Use OS CA roots by default or a configured CA bundle for private/self-signed certificates; migrate MQTT UI settings schemas 1 and 2 to schema 3.


## 1.0.0 - 2026-09-29

Initial commissioning release:
- Python-only offline-installable runtime (vendored YAML, no npm/pip needed).
- Native SDR source adapters and strict health/freshness/config/clock gates.
- Full360 Q16/U8 protocol, chunk assembly, two-connection MQTT5/TLS with bounded pacing.
- 480x320 Raspberry panel, four pages, login and maintenance controls.
- Ground companion receiver, preview graph, commands and receipt.
- Restricted helper, single-writer policy, persistent stop intent, safe settings backup,
  reboot challenge and boot-ID reconciliation.
- Installer/wizard, kiosk startup, isolated Ground TLS provisioning, rollback/uninstall.
- 113 automated tests; browser DOM checks and screenshot evidence.

Hardware commissioning, actual Mosquitto interoperability, unit scope/writer audit and
radio capacity measurements remain device acceptance work; see docs/TEST_REPORT.md.
