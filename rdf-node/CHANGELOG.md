# Changelog

## Unreleased

- Default setup to `rdfsdr.service` for SDR lifecycle controls when the unit is registered; Raspberry reboot and shutdown remain separately approved host actions.

- Publish diagnostic `doa.xml` every 3 s while a sample is available to keep periodic Control traffic within the default byte budget alongside health and valid DoA. Show raw angle and XML frequency as `UNVERIFIED` on the Edge main panel when strict DoA is blocked. Ground keeps it separate from normal DoA, receipt, and command gates. Existing broker ACLs need the new Control-topic write permission.

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
- Show each outbound MQTT topic's latest publish result in the Data table. QoS 0 socket writes and QoS 1 broker PUBACK remain distinct from Ground receipt.
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
