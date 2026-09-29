# Changelog

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
