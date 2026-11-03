# Changelog

## 1.11.0 - Unreleased

Contract `1.11` (two optional `telemetry/health` fields).

- The output VFO's peak against squelch reaches the panel and Ground also while squelch holds DoA back.
  `deploy/kraken/vfo_peaks.patch` makes rdfsdr write `vfo_peaks` to `status.json` every frame: per active VFO
  the FFT peak inside its bandwidth (CH1 with the Single spectrum, every channel with Full: the value KrakenSDR
  compares with squelch), the squelch it was compared
  with (Manual, or what Auto/Auto Channel computed), frequency, bandwidth and the frame time. Edge reads the
  `source.output_vfo` entry into `detection.vfo_peak` (`OK` with numbers, `STALE` without once older than
  `freshness.source_status_ms`; `above_squelch` null at a 0.0 dB margin, since rdfsdr compares unrounded values
  and writes 0.1 dB); a malformed `vfo_peaks` is null in `daq.null_fields` and the rest of the status
  still counts.
- `telemetry/health` carries `pk` (peak, dB on KrakenSDR's FFT scale, not dBm) and `psq` (squelch), 0.1 dB,
  null when stale. They are there only once KrakenSDR has reported `vfo_peaks`: with stock KrakenSDR health is
  byte-identical to 1.10. `pk` well under `psq` with `why: EMPTY_CSV` means squelch is holding the signal back.
- Panel: without a fresh sample (`NO_RECORD`, `NO_FRESH_DOA`, `EMPTY_CSV`, `DOA_FILE_NOT_UPDATING`) the line
  under the frequency reads `Puncak … dB / SQ … dB` instead of the last sample's PAPR/P. A fresh sample held
  back only by approval keeps its PAPR/P.

## 1.10.2 - Unreleased

Contract `1.10` (unchanged; one optional ACK field and one availability reason added).

- Progress ACKs carry `result.final_within_ms`: the latest, in ms from that ACK, by which Edge will have sent the
  final ACK, summed from the helper timeout, `start_timeout_seconds`, `retune_live_window_seconds` and 2 s of
  polling. `VERIFYING{LIVE}` counts the restart fallback when the patch decided it may take one; `ACCEPTED` of
  `config.patch` counts it whenever `processing_enabled` is set, since the worker decides later. It is on `ACCEPTED` of `config.patch`, `processing.set`, `service.restart`, `system.reboot.*` and
  `system.shutdown.*`, and on the `APPLYING`/`VERIFYING` steps of `config.patch` (241000 ms for a restart with
  the default 180 s start timeout). Ground waits at least until receipt + `final_within_ms` + 30 s (at most
  900 s) before it reports no final ACK; without the field it keeps its own budgets. It is not journaled, so a
  replayed ACK does not carry it.
- A command rejected before the journal (`BUSY`, `CLOCK_UNTRUSTED`, `HELPER_UNAVAILABLE`...) is remembered by id
  for 60 s (at most 64 ids). A second delivery of the same id and payload (an MQTT DUP resend, a restarted radio
  bridge) gets the same `REJECTED` instead of being evaluated again, so it can no longer be `ACCEPTED` and run
  while Ground keeps the first `REJECTED` as final. The same id with another payload is `COMMAND_ID_CONFLICT`.
- A clean stop (`systemctl stop`/`restart`, upgrade, shutdown) publishes a retained
  `availability {online:false, reason:"STOPPED"}` on the primary and on a ready backup broker and waits up to 2 s
  for its PUBACK. The clean DISCONNECT drops the Will, so the retained `online:true` used to stay, and the LoRa
  bridge kept resending it to Ground every 2 min. rdf-edge.service now orders after mosquitto and the LoRa
  bridge, so it stops first and the farewell reaches the broker and the bridge queue. A stop of rdf-edge alone
  leaves the bridge running to carry it; on system shutdown/reboot the bridge stops right after without
  draining its queue, so over LoRa the farewell reaching Ground is best effort.
- `capabilities` is republished (retained) whenever it changes, not only at connect: a helper that came up after
  the agent, or a new approval, no longer leaves Ground with `remote_config_patch: false` until the next reconnect.
  One failed helper status poll (the helper busy with another RPC for over 1 s) keeps the last status; only a
  second failure in a row clears it, so a blip neither flips capabilities nor goes out to Ground.
- rdf-edge subscribes `lora/edge` without Retain As Published, so only the broker's replay is flagged retained and
  aged by the bridge's stamp `t`. A chrony step between the stamp and now no longer shows a live bridge as STALE.
  Command subscriptions keep Retain As Published.
- LoRa bridge: commands are sent three times (the third copy 1 s after the second, in a later turn of the edge),
  ACKs still twice. At ~13% Ground-to-Edge frame loss a one-frame command is lost ~0.2% of the time instead of
  ~1.7%. No wire change; the ground-role bridge needs this release.
- LoRa bridge: a heartbeat whose `uptime_s` went down, or stayed the same while the peer's wall clock in it moved
  on by 1 s or more (a run shorter than one heartbeat period sends 0 each time), means the other bridge restarted;
  the receiver then forgets the message ids it completed (`rx.peer_restarts`), so new messages with reused ids are
  no longer dropped as copies for 30 s. Message ids start at a random value. No wire change.
- `install.sh` prints a NOTICE when `rdf-lora-edge.service` (or `rdf-lora-ground.service` with `--ground`) runs a
  bridge outside `/opt/rdf-node/current`: the installer never updates that unit, so such a bridge keeps its old
  code. When the unit runs from `/opt/rdf-node/current` and is active, it prints a NOTICE that it still runs the
  previous release, with `sudo systemctl restart rdf-lora-<role>.service` (this release's 3x commands and
  peer-restart reset need it). Read-only check: the installer does not restart the bridge.
- Docs: `LORA_SERIAL.md` makes the LORA refclock the time authority (`prefer trust`) and the GPS refclock
  `noselect` (monitored, never steering), because Ground judges freshness by its own clock; it also lists frame
  type 4 TIME and the TURN flag, and the 0.45 s quiet window counted from the air. `MQTT_GROUND.md` adds the LoRa
  bridge roles (edge, ground) with their read/write topics, and `lora/ground`, `lora/edge`, `ground/gps` to the
  Ground controller and viewer subscribe lists.

## 1.10.1 - Unreleased

Contract `1.10` (unchanged).

- Panel: the T900/PPP leftovers are gone. The radio is the LoRa bridge: no "Restart T900" button or
  confirmation, no T900 recovery card, and the home status, focus mode and Link tab always show LoRa. When the
  bridge does not report, they say LORA STALE / TIDAK MELAPOR with the report's age instead of falling back to
  the old PPP rows, and a stale report's port, loss and clock are shown as `--`, not as current green values.
  Old `ppp.restart` operations still read correctly in Riwayat.
- Panel: an enabled backup broker that is not connected marks the Data tab (where it is shown), not Link.
- LoRa bridge (edge): the quiet window after a turn frame now counts from when the radio is expected to have
  sent that frame and every frame queued before it (`air_until`), not from when it was written to the module,
  and lasts 0.45 s instead of 0.3 s. Frames written just before the turn frame were still on the air, so Ground's
  reply collided with the edge's next frames: on the bench the edge lost about 62% of Ground's frames (commands
  such as `config.get` went unanswered) and now about 13%; Edge to Ground stays at about 4%.
- rdf-edge: a retained `lora/edge` status (replayed after an rdf-edge restart or broker reconnect) is as old as
  its own stamp `t`; without a usable stamp it counts as stale. A dead bridge no longer shows LoRa UP for 20 s
  after a restart.

## 1.10.0 - Unreleased

Contract `1.10` (additive).

- Every `telemetry/doa` sample of a node with a compass or GPS carries the array heading at send time: `h`
  (where DoA 0° points, degrees clockwise from north, null when there is none) and `hsrc` (`C` compass, `G` GPS
  course, `H` held GPS course). A valid sample (`ok=1`) also carries `b`, the bearing to the transmitter from
  north: (`h` - `a`) mod 360 with `theta_mirror` (`a` is theta_0, counter-clockwise from the array reference),
  (`h` + `a`) mod 360 with `csv_native`, and with `nav.gps.publish_position` the position it was taken at (`lat`, `lon`).
  Ground no longer has to pair a DoA sample with a heading from a health message up to 2 s older.
- `nav.heading` picks the source: `compass` (default; GPS course when the compass has nothing), `gps` (course
  while moving at `gps_min_speed_mps` or faster for `gps_min_seconds`, then held for `hold_seconds`, else the
  compass) or `auto` (course while moving, else the compass, else the held course). `travel_offset_deg` turns
  the direction of travel into the array reference, as `nav.compass.offset_deg` does for the compass.
- `telemetry/angular` and `telemetry/diagnostic/angular` carry `heading_deg` and `heading_source` (the heading of
  the latest DoA sample): bin theta points at (`heading_deg` + 360 - theta) mod 360.
- Ground receiver: `detection` adds `bearing_deg`, `array_heading_deg`, `heading_source`, `lat`, `lon`; a bad stamp
  is dropped (`nav_rejected`) and the sample still counts. `capabilities.nav.heading` names the source; the Edge
  snapshot shows the last choice in `nav.array_heading`.
- `run.py nav-calibrate --tx-lat .. --tx-lon ..`: compares the bearing with the true bearing to a transmitter at
  a known place and suggests the change to `nav.compass.offset_deg` or `nav.heading.travel_offset_deg`.
  It never writes the configuration.

## 1.9.0 - Unreleased

Contract `1.9` (additive).

- config.patch accepts `vfo0_squelch_mode` (`Manual`, `Auto`, `Auto Channel`, `Default`) for `vfo_squelch_mode_0`.
  rdfsdr computes DoA only while the VFO's peak is above its squelch: Manual uses `vfo0_squelch_db`, Auto the
  mean of the whole spectrum + 5 dB, Auto Channel the mean around the VFO + 3 dB (an automatic squelch just
  under any signal above the noise), Default follows `vfo_default_squelch_mode`. Applied live (no retune).
- `vfo0_squelch_db` is written in whole dB (rdfsdr reads it with `int()`). Setting a level while the squelch is
  automatic also switches it to Manual (result `squelch_mode: Manual`).
- With an automatic squelch the engine saves its computed level into settings.json on a retune; that is no
  longer taken for an override (no needless restart), and `state.rf` then leaves `sq` out. In Manual a
  fractional level left in the file (from Auto or an older Edge) is rewritten as the whole dB the engine runs
  (`int()`), so a later retune is not mistaken for an override. Missing mode keys mean what rdfsdr reads:
  `Default` -> `Auto`. The built-in Ground page asks for the squelch in whole dB, so its settings form matches
  what Edge writes.
- `state.rf.sqm`: the squelch mode. `capabilities.config_fields` lists `vfo0_squelch_mode`.

## 1.8.0 - Unreleased

Contract `1.8` (additive).

- config.patch accepts `ant_spacing_m` (metres, 0.05–1.0, rounded to 0.1 mm) for `ant_spacing_meters` (UCA
  radius). rdfsdr sets the UCA radius itself on every retune and at start: 0.3 m in 30–300 MHz, 0.128 m in
  300–3000 MHz (UCA; a settings file without `ant_arrangement` starts as UCA). Edge follows the same rule: in
  those bands a spacing more than 0.5 mm off the band value is refused before anything is written (`FAILED`,
  `SPACING_SET_BY_ENGINE_BAND`, with `band_spacing_m`), a closer one is snapped to it, and every patch there
  writes the band radius when the file holds another one (named in the result as `band_spacing_m`), so
  settings.json and `state.rf.uca` say what the engine runs and a settings reload never brings an old radius
  back. Outside those bands (or with a ULA) the spacing is free and applies live. Out of range: `NUMBER_OUT_OF_RANGE`.
  The engine moving the radius to its band value on a retune is not an override.
- LoRa bridge (Edge side): availability, state and capabilities are also resent every 2 min while the link is
  up, not only when it comes back. Capabilities is published once at connect and spans ~4 radio frames; one
  lost frame (~2.5 % Edge->Ground) left Ground with the previous version until the next link drop.
- `state.rf.uca` (metres) and `capabilities.config_fields` (the fields config.patch accepts), so Ground sends
  `ant_spacing_m` only to an Edge that takes it. The panel labels it "Jarak UCA".

## 1.7.1 - Unreleased

Contract `1.7` (unchanged).

- Panel: GPS and compass are visible on the Pi. Sistem has a third view "Nav" (compass heading and state,
  GPS fix 2D/3D with satellites used/in view, HDOP with BAIK <= 2 / SEDANG <= 5 / BURUK, position, altitude and
  speed); the home status row shows a glance ("GPS 3D", heading) with a coloured dot, hidden when both sensors
  are off or the Edge has no nav data.
  The GPS colour follows accuracy too: a 2D fix or HDOP above 2 is yellow, above 5 red.
- Compass: one missed or garbled GY-26 reply no longer reports `NO_REPLY`/`BAD_FRAME` (and drops the heading)
  while the last heading is still within `stale_ms`; a missing or broken port still does. The panel, health
  `hs`/`hdg` and Ground no longer flicker to a fault between two good frames.

## 1.7.0 - Unreleased

Contract `1.7` (additive).

- config.patch on a running SDR engine tries the change live and falls back to an engine restart
  (stop -> write -> start) when needed. A gain change or a frequency jump above
  `control.retune_live_max_hz` (20 MHz) restarts directly: the rdfsdr engine re-applies its own band gain
  on a live retune (25.4 dB in VHF) and lost DAQ sync, while at start it takes settings.json as written.
  A live change the engine overrides (any field, also one not in the patch) or that leaves the DAQ
  unhealthy for `retune_live_window_seconds` falls back to the restart with every intended value. Ground
  follows it through intermediate ACKs (`VERIFYING` with `method: LIVE`, `APPLYING` with
  `method: RESTART` and `reason`); APPLIED carries `method` and runtime proof (fresh healthy DAQ held for
  `retune_settle_seconds`, values intact). A stopped engine only gets the file (`ENGINE_NOT_RUNNING`).
- `state.rf`: what the SDR is set to (`f`, `vfo`, `g`, `bw`, `sq`), resent with every config revision, so
  Ground shows frequency, gain and squelch without Data Out. `telemetry/health/detail.ovr`: ADC overdrive.
- Panel: the Config tab fits 480x320 again (five rows + buttons were 267 px in a 244 px page, so the buttons
  slid under the nav and its border line crossed "Riwayat"). A Ground command banner that ends
  "tersimpan, belum terbukti" hides after 30 s instead of covering the rows until closed (it stays in
  Riwayat). config.patch stages show the route: "retune langsung", "restart SDR", "diterapkan (restart SDR)".

## 1.6.1 - Unreleased

Contract `1.6` (unchanged).

- Edge panel on LoRa: rdf-edge subscribes to the bridge's retained `lora/edge` status on its local
  broker and exposes it as `snapshot.link.lora` with rates and loss, plus `link.transport`
  (`LORA` while the status is fresh, else `PPP`). The panel header and Link tab show the LoRa link, data
  rate, loss each way, clock vs Ground and radio port instead of T900/PPP rows. Alerts
  `LORA_LINK_DOWN` and `LORA_BRIDGE_STALE`.
- A `doa.xml` sample older than 60 s is no longer shown as the current bearing (KrakenSDR writes none
  while it has no DoA, so it can be hours old). Without a live or fresh sample the panel shows the
  frequency the SDR is set to, labelled `FREKUENSI SETELAN`.

## 1.6.0 - Unreleased

Contract `1.6` (additive): `telemetry/health` can carry optional compass and GPS fields.

- GY-26 compass and NMEA GPS on the Edge. New `nav.py` polls a GY-26 over USB-serial (read-angle
  command `0x31`, checksummed 8-byte frame) and reads a GNSS receiver's NMEA stream (GGA is the fix
  source; RMC adds speed and course; GSA/GSV add mode and satellites in view). Each sensor runs in
  its own thread with non-blocking I/O, reopens its port after errors, takes `TIOCEXCL`, and refuses
  the T900 port (`link.serial_alias`). A silent, garbled, or stale sensor reports a state and no
  number. A sensor bug cannot freeze the snapshot.
- New config section `nav:` (`compass` and `gps`, both off by default; `gps.publish_position` off by
  default). Validated strictly; the two sensors and the T900 port must be different devices
  (`NAV_DEVICE_CONFLICT`). Releases before 1.6 reject the section, so remove it before a rollback.
- `telemetry/health` adds `hdg`/`hs` (compass) and `gs`/`fix`/`sat` (GPS) only for configured
  sensors, and `lat`/`lon`/`alt`/`sog`/`cog` only with a fix and `publish_position`. Health without
  sensors is byte-identical to 1.5. The fields go to every READY broker, primary and backup.
  `capabilities.nav` lists the configured sensors. The diagnostic Angular reserve grows by 110 B/s
  while a sensor is enabled.
- Ground keeps the latest nav reading and a position track (a point per 2 m or 10 s, up to 2000),
  exposes them as `snapshot.nav` and `GET /api/v2/nav/track`, and the Ground preview shows a compass
  and the track. A bad nav field is counted as a reject and never costs the health message.
- `rdf-edge.service` gains `SupplementaryGroups=dialout`; `deploy/examples/nav/99-rdf-nav.rules` sets
  stable symlinks and keeps ModemManager off the sensor ports.
- LoRa serial bridge (RLS1, `run.py lora-bridge --role edge|ground`, docs/LORA_SERIAL.md): carries the
  MQTT topics over a transparent LoRa UART without PPP or TCP/IP. Each side talks to its local broker;
  Ground gets byte-identical payloads and retain flags, commands go the other way. Frames are COBS +
  CRC-16, zlib with a shared dictionary, at most 200 B, released one by one under a 1000 B/s budget with
  a 400 B burst (the module buffer is ~1 KB without flow control). Per-topic priority and maximum age
  keep data inside Ground's freshness windows; commands and ACKs go twice, retained topics are resent
  when the link returns. Heartbeats carry link counters and wall clock; status is published retained on
  `sdr/v2/<node>/lora/edge|ground`. Example units in `deploy/examples/lora/`.
- Clock sync without IP: the edge bridge exchanges NTP-style TIME frames with the ground bridge and feeds
  the best sample to chrony (`--chrony-sock`, `refclock SOCK`); the GPS reader can feed RMC time of a
  valid fix to chrony too (`nav.gps.chrony_sock`). `run.py lora-test` measures LoRa throughput, loss and
  latency with RLS1 TEST frames (rx, tx sweep, echo).
- Field test on the real link drove three bridge changes: DoA ranks first among telemetry and goes twice;
  health is the newest one at most every 2 s (`--health-every`); half-duplex turn taking (the edge sets
  F_TURN about once a second and stays quiet 0.3 s, the ground transmits only then or after 3 s of edge
  silence). TIME frames are stamped when they leave, not when queued.

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
- Ground receiver (`rdf-ground`) labels instead of dropping (decision D4).
  - DoA with `ok: 0` and any reasons, and `ok: 1` samples Ground cannot confirm (stale timestamp,
    stale health, DAQ not healthy), appear in `diagnostic_doa` as `UNVERIFIED`, with the Edge
    `reason` plus Ground labels (`SOURCE_STALE`, `HEALTH_STALE`, `DAQ_NOT_HEALTHY`,
    `PREVIOUS_CONFIG`, `OK_FLAG_MISSING`), `clk`, and `source_age_ms`.
  - Angular frames without `flags: 63`, fresh time, or fresh health go to the diagnostic view with
    their reasons, instead of `ANGULAR_EVIDENCE_INCOMPLETE`/`ANGULAR_WITHOUT_HEALTH`.
  - Health with a skewed timestamp is kept and shown, but `health_fresh`/`health_time_ok` stay
    false. It never makes DoA LIVE, never lets a command through, and never reconciles a reboot or
    PPP restart.
  - `diagnostic/doa` with missing labels is accepted (always `UNVERIFIED`); an out-of-order older
    `doa.xml` sample is skipped instead of counted as rejected.
  - `settings/reported` with extra fields is accepted.
  - Only unreadable payloads still count in `link.rejected`, and the reason is now counted per
    code in `link.reject_reasons`.
  - A rejected message no longer leaves the Ground event queue unfinished.
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
  verified DoA until they are updated to read the new labels (`rdf-ground` does from 1.5.0).


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
