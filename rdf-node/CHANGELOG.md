# Changelog

## 1.5.0 - Unreleased

Contract `1.5`. This is the first release that reports its own number: `capabilities.version` is
`1.5.0` and `availability`, `capabilities`, and `state` carry `contract: "1.5"`. Releases 1.1.0 to 1.4.0
below were numbered afterwards; their packages still reported `version: "1.0.0"` (the 1.1.0 and
1.2.0 packages are the builds stamped `1.0.0-20261006185856` and `1.0.0-20261013194717`).

- Visible contract version. `rdf_node.CONTRACT` (`1.5`) is published as `contract` in
  `availability` (online, Last Will, and farewell), `capabilities`, and `state`, and shown in the
  panel snapshot. `capabilities.version` follows the release number. The contract revision is the
  major.minor of the release that last changed a topic or field, so a consumer compares `contract`
  and can ignore patch releases.
- Machine-readable contract: `contract/schema/<topic>.json` holds a JSON Schema (2020-12 subset) for
  every topic the Edge publishes plus the `cmd/#` envelope. `contract/validate.py` (standard library
  only) checks a payload or a `mosquitto_sub` stream against it. `tests/test_contract.py` validates
  the payloads the Edge actually builds (each DoA state, diagnostic, angular, settings report, ACKs,
  Last Will). The schemas allow extra fields, as consumers must.
- Testable without hardware:
  - `python3 run.py demo-source DIR` writes the synthetic KrakenSDR files (`settings.json`,
    `status.json`, `DOA_value.html`, `doa.xml`) without demo mode, so a real `run.py edge` can read
    them. The demo `status.json` now writes `adc_overdrive` as a boolean, and the demo also writes
    `doa.xml` (TIME in ms, FREQUENCY in MHz, raw DOA).
  - New `testing.clock: system` (default `null`): the OS clock times the data gates, so DoA can be
    `ok: 1` on a laptop or macOS without `timedatectl`. It is labelled: `state.clock` is `TEST`,
    `clk` in health and DoA stays 0, and helper-backed commands are still `CLOCK_UNTRUSTED` (only
    `stream.set` is timed by it). It is rejected with `runtime_mode: controlled`
    (`TEST_CLOCK_REQUIRES_READ_ONLY`).
  - New guide `docs/UJI_TANPA_ALAT.md`: Edge, a loopback Mosquitto, and the demo source on a
    laptop, to test Ground Console.
- Operations write-ups (steps only; nothing runs automatically):
  - `docs/UPGRADE_1.5.md`: bring the groundstation `rdf-ground` (1.1.0) and the Edge (1.2.0) to
    1.5.0, groundstation first, with backups and rollback. It also clears retained topics no
    longer used (`config/reported` from 1.1.0, any retained `cmd/...`) through a temporary broker
    account.
  - `docs/UJI_SINYAL_437.md`: tell approval-only `ok: 0`, "no signal above squelch", and a stuck
    pipeline apart at 437.425 MHz, with a test transmitter or a temporarily lowered squelch.
- Lenient reading.
  - Commands: fields this Edge does not know are dropped and listed in `result.ignored` on the
    first ACK instead of rejecting the whole command `UNKNOWN_COMMAND_FIELD`. Session, deadline,
    `base_rev`, each operation's own fields, and value limits are still required.
  - Dropped commands are visible:
    - A command over 4,096 bytes is answered `REJECTED COMMAND_TOO_LARGE` when its `id` can still
      be read (up to 64 KiB).
    - Non-JSON payloads (`COMMAND_NOT_JSON`) and a full inbound queue (`COMMAND_QUEUE_FULL`) cannot
      be answered by id. All three go to `remote_rejects` and the snapshot's `link.command_drops`,
      and are counted in `telemetry/health/detail.crej`.
  - KrakenSDR `DOA_value.html`: empty or `NaN` metadata (latency, latitude, longitude, GPS/compass
    heading) becomes null and is named in `detection.null_fields`; the record is no longer lost.
    Timestamp, angle, confidence, power, frequency, and the 360 values stay strict.
  - The read limit for `DOA_value.html` is `max_record_bytes` times `active_vfos` (1-16), so a
    multi-VFO file is no longer rejected for size.
  - `status.json`: a malformed `daq_num_dropped_frames` or `data_frame_index` becomes null
    (`daq.null_fields`) instead of failing the whole status. `adc_overdrive` is read as a boolean,
    and 0/1 are accepted.
  - `settings.json`: one malformed safe field is left out of the safe subset and its digest and
    listed in `config.invalid_settings`, instead of making all settings `CONFIG_UNAVAILABLE` and
    blocking DoA.
- Readable command errors. Every `REJECTED`, `FAILED`, and
  `OUTCOME_UNKNOWN` result now carries `message` (Indonesian operator text) and, when there is a
  technical cause, `detail` (at most 200 characters) next to the unchanged `error` code. Raw
  exception names are gone:
  - Helper socket missing: was `OUTCOME_UNKNOWN OSERROR`, now `HELPER_UNAVAILABLE`, `FAILED`
    (nothing was sent, so nothing ran). `HELPER_ACCESS_DENIED` covers a socket the agent may not
    open.
  - Timeout or socket loss after the request was sent: `HELPER_TIMEOUT` and
    `HELPER_CONNECTION_LOST`, still `OUTCOME_UNKNOWN`.
  - An unexpected exception inside the helper: `HELPER_INTERNAL_ERROR` with the cause in `detail`.
  - In the agent: `INVALID_COMMAND_FIELD`, `JOURNAL_UNAVAILABLE`, or `INTERNAL_ERROR` instead of
    `JOURNAL_OR_REQUEST_ERROR`.
- Mutations that need the root helper (`config.patch`, `processing.set`, `service.restart`,
  `ppp.restart`, reboot and shutdown) are rejected `HELPER_UNAVAILABLE` before they are journaled
  when the helper does not answer a status probe. Before this, Ground learned this only after the
  command was queued and failed.
- The Edge panel labels the new codes and falls back to the Edge `message` for codes it does not
  know. The Ground preview shows the `message` next to the code. Ground Console 3.0.0 drops
  `message` and `detail` (its ACK allowlist) but already shows the clearer `error` code.

## 1.4.0 - 2026-10-28

- Labelled DoA. `telemetry/doa` is no longer withheld when a gate fails:
  every new DoA sample (new `q` with a parsed timestamp and angle) is published once with
  `ok: 0` when the source is stale, DAQ is not healthy, the clock is not verified, or a
  verification flag is missing. New fields on every DoA: `clk` (clock verified, 0/1), `age`
  (source age in ms at publish time), and `reason` (the gate codes, `[]` when `ok: 1`). The angle
  `a` is the reference-corrected angle when `ok: 1` and the raw native angle when `ok: 0`
  (`angle_reference: "RAW"`, `trust: "UNVERIFIED"`). The old `validation_reasons` field is still
  sent, but only when the sample is blocked by `SOURCE_UNVERIFIED`/`ANGLE_UNVERIFIED` alone, so
  existing strict consumers keep accepting that case. A sample is never sent twice; when there is
  no new sample there is no DoA, and `telemetry/health` carries the why.
- `telemetry/health` adds `why` (the current DoA gate codes) so Ground can tell "no signal above
  squelch" (`NO_FRESH_DOA` while `DOA_value.html` keeps being rewritten) from "processing stuck"
  (`DOA_FILE_NOT_UPDATING`, new: the file itself has not changed within `freshness.doa_ms`). The
  code table is in `docs/PROTOCOL.md`.
- `telemetry/detail` adds `perr`: the topics whose last publish attempt raised an error. Each
  scheduled topic is now published inside its own error boundary, so one failing topic no longer
  stops health, DoA, or angular from going out in the same tick.
- Diagnostic angular frames on the blocked schedule are spaced at least 6 s apart and
  `diagnostic/doa` now goes ahead of them in the Control outbox (priority 2), so the larger
  angular frames cannot starve the small DoA diagnostic. While the source still produces new DoA
  samples (valid or labelled), the Control budget reserves room for them, so diagnostic angular is
  sent less often (about every 60 s instead of about every 9 s on an 850 B/s budget); once the
  source is stale the smaller reserve applies again. A diagnostic angular frame that expired in the
  outbox before it was sent (for example behind the burst at connect) is replaced by a current one
  on the next tick instead of waiting a whole budget period.
- Compatibility: consumers that only read `ok: 1` DoA are unaffected. Ground Console 3.0.0 and
  the bundled `rdf-ground` receiver still treat `ok: 0` DoA with reasons other than
  `SOURCE_UNVERIFIED`/`ANGLE_UNVERIFIED` as invalid and drop it; they keep working on health and
  verified DoA until they are updated to read the new labels.


## 1.3.0 - 2026-10-27

- `cmd/config/get` on the primary broker is now answered with `settings/reported` whether or not
  the primary uses TLS. `mqtt.tls: false` is the operator's choice of a trusted link (for example
  the private PPP/T900 point-to-point link) that already carries telemetry and `config.patch` in
  plaintext; before this change every `config.get` there was rejected
  `TLS_REQUIRED_FOR_SETTINGS_REPORT`, so Ground never received settings. The backup broker keeps
  the TLS requirement by default because it may be shared or public; the new boolean
  `mqtt.backup.settings_report_plaintext` (default `false`, panel `Data > Cadangan > Settings tanpa
  TLS`) allows plaintext reports there explicitly. `TLS_REQUIRED_FOR_SETTINGS_REPORT` now only
  occurs for the backup broker. Everything else about `settings/reported` is unchanged: only after
  `config.get`, only to the requesting broker (never mirrored), QoS 1, non-retained, expiry 30 s,
  the same envelope and error codes, and one read request per second per broker. Panel backup
  settings saved before this release (without the new key) keep loading, with the `config.yaml`
  value.
- Edge panel: **Restart T900** in `Kontrol > Raspberry` restarts the fixed `t900-ppp.service` from
  the touch panel. It needs the existing one-time `sudo rdf-node controls approve --ppp-restart`
  (controlled mode, `control.ppp_restart_enabled`, helper `allow_ppp_restart`); when disabled the
  dialog says why. Local `ppp.restart` is not rejected `CLOCK_UNTRUSTED` (a Pi without RTC gets
  NTP over the link that is down) and ignores `base_rev`.
- `ppp.restart` from every origin runs on its own small worker lane: an SDR start/restart still
  verifying (`BUSY`) no longer blocks it, and it does not block SDR mutations. New rejections:
  `PPP_RESTART_IN_PROGRESS` (a previous restart is still running or being observed) and
  `PPP_RESTART_TOO_SOON` (less than 15 s since the last accepted request, any origin).
- The helper now also restarts a `failed` unit and one in `activating/auto-restart` (any origin),
  and starts an `inactive` unit for the local panel only. A pending job is still refused
  (`PPP_RESTART_PENDING`).
- The helper reads the idle unit's `Job=` correctly. `systemctl show -p Job` prints an empty value
  for a unit with no job, never `Job=0`, so before this fix every real `ppp.restart` and the
  `controls approve --ppp-restart` preflight failed with `PPP_SERVICE_STATUS_UNKNOWN`. An empty or
  `0` job now means no job; any job number is `PPP_RESTART_PENDING`. `LoadState` and `ActiveState`
  must still be present and non-empty.
- PPP recovery observer on the Edge for local, Ground, and backup restarts: after
  `PPP_RESTART_REQUESTED` it probes the link about every second and reports `APPLIED` with proof
  (PPP cycled, interface up, peer ping reply, primary MQTT ready over the new link) or, after
  120 s, `OUTCOME_UNKNOWN` with a reason chosen from the evidence, in priority order:
  `T900_USB_MISSING` (T900 USB alias `MISSING`), `MQTT_NOT_RECOVERED` (PPP and peer ping back,
  primary MQTT not), `PPP_RECOVERY_TIMEOUT` (cycle seen, PPP/ping not back), or
  `PPP_CYCLE_NOT_OBSERVED`. The panel shows the steps (`Restart diminta`, `PPP turun/naik`,
  `PPP UP`, `Ping Ground`, `MQTT utama`) and `Tersambung ke Ground ✓`, with `Coba lagi` after a
  timeout. The built-in Ground receiver keeps its own health-based reconcile and does not adopt the
  observer's final ACK.
- Late recovery: while a timed-out record is still shown (90 s), the observer keeps evaluating the
  link and updating the steps. If the peer ping and primary MQTT (or MQTT disabled) recover late,
  `link.ppp_recovery` switches to `phase: "DONE"` with the new `late: true`; `stage` stays
  `OUTCOME_UNKNOWN`, `reason` keeps the timeout reason, and the journal and ACK are not rewritten.
  The panel shows the card green and hides `Coba lagi` while the snapshot shows the link healthy
  (PPP `UP`, peer `REPLY`, primary MQTT ready or disabled). The 90 s linger uses the Edge's
  monotonic clock only, so an NTP clock step on an RTC-less Pi no longer hides the result early.
- A helper timeout or socket error during `ppp.restart` stays `OUTCOME_UNKNOWN` (systemd may have
  restarted the unit); the panel shows it amber as `Hasil restart T900 belum pasti` with
  `Coba lagi`, not as a red failure.
- The `Restart T900` confirmation and the progress card warn while `link.usb` is `MISSING`
  (`T900 USB tidak terdeteksi — restart PPP tidak membantu; cek kabel/daya radio.`): a freshly
  restarted pppd cannot open a missing device. When `Coba lagi` is not allowed, the card hides it
  and shows the approval or helper hint instead.
- Snapshot adds `link.ppp_service` (`state`, `substate`, `restarts` of `t900-ppp.service`) and
  `link.ppp_recovery` (kept 90 s after it finishes). The Link tab `Rincian` view shows
  `Layanan PPP`. `telemetry/health/detail` adds `ppp`, `peer`, `ppp_svc`, and `ppp_restarts`
  (about +62 B per message, ~6 B/s), so a backup-broker consumer can see the primary link state.
- After the PPP interface comes back up or the peer ping returns, the Agent calls `kick()` on the
  primary MQTT clients so they reconnect without waiting out the backoff.
- MQTT client: the reconnect backoff resets after a session that stayed connected for at least 20 s
  after CONNACK; shorter sessions and failed connects keep growing it up to `reconnect_max`, so a
  rejected SUBACK/PUBACK no longer reconnects every second. PINGREQ is also sent after `keepalive`
  seconds without receiving anything, even while QoS 0 telemetry keeps the transmit side busy.
  New `queue_age_ms` status field.
- MQTT client: a PINGRESP that is late only because the PINGREQ is queued behind the client's own
  backlog no longer resets the session. When `keepalive` has passed since the PINGREQ, the client
  waits up to 4x `keepalive` while TCP is still draining: unacknowledged bytes in the kernel send
  queue (`TIOCOUTQ`) or the PINGREQ not yet handed to the kernel, and a TCP ACK from the peer within
  the last `keepalive` seconds (`TCP_INFO` `tcpi_last_ack_recv`). This covers TCP, TLS, and
  WebSocket. A dead link sends no ACKs and still resets `keepalive` after the PINGREQ, or after the
  last TCP ACK if that is later; if the probe fails, the old rule applies. This stops false
  `PING_TIMEOUT` resets, offline LWTs, and a flapping `MQTT utama` on a slow but working T900 link.
- `CONTROL_BACKLOG` now pauses primary Angular when the oldest queued Control message is older
  than 3 s or an in-flight QoS 1 PUBLISH has waited more than 2.5 s for PUBACK, instead of on any
  PUBACK slower than 500 ms. Below those limits Bulk yields without the 20 s penalty: the next
  primary Angular frame waits while a Control PUBACK is more than 1 s late or more than 2,048 Bulk
  bytes are still unACKed in the kernel (`Client.socket_backlog`).
- Remote-command indicator: the Edge journal records each command's origin (`local`, `ground`,
  `backup`) and before-values in a new side table `operation_meta`. The `operations` table keeps
  its original seven columns, so `scripts/rollback.sh` to the previous release still journals
  commands on the same database (the previous release ignores the side table). Databases written
  by pre-release builds of this change, which added `link`/`meta` columns to `operations`, keep
  working with this build but are not usable by the previous release. Public operation records add
  `origin`, `summary` (`{kind, changes:{field:{from,to}}}`), and `reason`. The snapshot adds
  `last_remote_operation`, `operation_seq`, and `remote_rejects` (up to five recent remote
  rejections); `last_operation` skips read-only operations. `operation_seq` also increases on
  journal-only stages (`APPLYING`, `VERIFYING`, `REBOOT_SCHEDULED`, `SHUTDOWN_SCHEDULED`), so an
  open `Riwayat` dialog refreshes during verification. The panel shows a `Perintah dari GROUND` /
  `Perintah dari CADANGAN` banner with an Indonesian summary and stage, also for rejected remote
  commands (`ditolak` plus reason), origin chips `[G]`/`[C]`/`[L]` and reasons in the CMD row, and
  a `Riwayat` dialog with the last 10 mutations. The banner's stage/result text is no longer
  clipped by a long summary, and toasts are no longer hidden under the banner or recovery card.
  Old operations no longer toast on page load.
- Docs: new `docs/T900_PPP.md` (pre-flight procedure, progress steps, why unplugging the T900
  helps, hardening both ends, diagnostics). New examples under `deploy/examples/t900/` (udev rules
  for both hosts, pppd options, a `Restart=always` drop-in); they are not installed by
  `install.sh`. The drop-in suits `Type=simple`/`exec` (pppd `nodetach`) or `Type=forking` without
  `updetach`. systemd refuses `Restart=always` for a `Type=oneshot` unit (common `pon` wrapper),
  which then becomes `bad-setting`; the example carries an opt-in block that turns such a unit into
  `Type=simple` + `nodetach`, and the docs add `-p Type` before and `-p LoadState` after
  installation. `Type=notify` with `up_sdnotify` or `forking` with `updetach` keeps the unit in
  `activating/start` while the radio is down, so `Restart T900` is refused with
  `PPP_SERVICE_NOT_ACTIVE`; the docs recommend `Type=simple` + `nodetach`. Operators are told to
  check the Link tab before pressing `Coba lagi`.


## 1.2.0 - 2026-10-13

- Add an optional backup MQTT broker on the Edge (`mqtt.backup`, panel `Data > Cadangan`). A third
  client, `<client_id>-backup`, mirrors every Edge publish to a LAN or online broker under the same
  `sdr/v2/{node_id}` topics, independent of the PPP/T900 primary, and keeps publishing while the
  primary is down. It has its own transport, TLS (`ca_file: null` uses the system CA store; a
  private or self-signed CA needs `mqtt.backup.ca_file`), credentials, and token bucket
  (`budget_bytes_s`, default 8000 B/s, outside the T900 aggregate check). Backup Angular is paced
  by the profile interval and the backup budget and is not paused by primary-only reasons
  (`BULK_NOT_READY`, `CONTROL_BACKLOG`, `WAIT_STABLE`). Saving either broker's settings never
  restarts the other broker's clients.
- With `commands: true` (panel `Terima perintah`, default on) the Edge also subscribes `cmd/*` on
  the backup broker and runs those commands like Ground commands (actor `ground-controller`, same
  gates, shared single mutation). Commands from the backup are acknowledged only on the backup;
  primary and local-panel commands are acknowledged on every ready broker. `config.get` requires
  TLS on the broker it came from, and `settings/reported` goes only to that broker. Restrict
  publish on `cmd/#` in the backup broker's ACL, or turn commands off for a publish-only broker
  (a rejected SUBACK keeps the backup client from becoming READY).
- New Edge API `GET`/`POST /api/v2/mqtt/backup` (admin session; POST also needs CSRF). Panel
  settings persist in `mqtt-ui-backup-settings.json` and `mqtt-ui-backup-account.json` (mode
  0600) and override `mqtt.backup` from `config.yaml`. The snapshot adds `link.mqtt_backup`
  (state, readiness, error, queue, pause reason, and a separate per-topic delivery map) and
  `config.mqtt_backup_configured`; `link.mqtt_topic_delivery` stays primary-only.
- Commands still queued from a replaced MQTT session are now dropped by a per-broker connection
  epoch instead of draining the shared inbound queue on primary reconfigure.
- Replaced MQTT clients are stopped (and their successors started) on a helper thread, so a client
  stuck in DNS, TCP or TLS on one broker no longer pauses publishing on the other for up to 7 s.
  Disabling or repointing the backup first publishes retained `availability`
  `{online:false, reason:"BACKUP_DISABLED"|"BACKUP_RECONFIGURED"}` to the old backup broker.
  Changing the primary base Client ID also reconnects the backup under the new
  `<client_id>-backup`. A pending `config.get` report for a broker that dropped no longer blocks
  `config.get` on the other broker. A blank `Pakai akun` save reuses the account in use, then the
  panel account, then `mqtt.backup.credentials_file`; `GET /api/v2/mqtt/backup` reports which one
  as `credentials_source`.
- Online backup traffic follows the OS default route. If that route is PPP, the backup consumes T900
  bandwidth; check `ip route get <host>` before enabling it.

- Edge panel: the black screen shows a dim clock and date from the Pi system time, marked
  `BELUM SINKRON` when the Edge reports the clock as untrusted. Remove explanatory captions from
  the panel (Config approval note, PIN keypad hint, Profil intro, frequency note, far-reading exit
  hint) and shorten destructive-action dialogs to one line; status and disabled reasons remain.
  Ground drops the plot axis caption.

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

- Replace RDF2/Q16/U8 Angular chunk transport with one JSON PUBLISH containing all 360 source
  values on normal and diagnostic topics. Ground validates session, freshness, health, and flags
  before marking LIVE; the reported revision is metadata, not a LIVE equality gate. Edge and
  Ground must be upgraded together; byte-budget pacing can extend profile and diagnostic intervals.


## 1.1.0 - 2026-10-06

- Publish diagnostic `doa.xml` every 3 s when available, independent of normal DoA validity.
  Ground keeps it `UNVERIFIED`, separate from live DoA and commands.

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
