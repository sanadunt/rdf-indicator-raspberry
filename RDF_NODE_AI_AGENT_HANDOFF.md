# RDF Node - AI Agent Handoff & Debugging Context

> Paste this file into the coding/debugging AI agent before asking it to modify or diagnose RDF Node.
>
> Project release currently discussed: **RDF Node 1.0.0**
> Date of this handoff: **29 September 2026**

## 1. Mission

RDF Node connects a KrakenSDR/SDR-DoA Raspberry UAV node to an Ubuntu Ground Station over a low-bandwidth T900 radio link.

The transport path is:

```text
KrakenSDR / DAQ / SDR-DoA
          |
          v
local files on Raspberry
          |
          v
RDF Node Edge Agent
  |                 |
  | local API       | MQTT
  v                 v
480x320 panel   TCP/IP -> PPP -> T900 -> Ubuntu Ground
                                      |
                                      v
                                MQTT broker
                                      |
                                      v
                              Ground receiver/backend
                                      |
                                      +-> receipt back to Raspberry
                                      +-> Ground Dashboard integration
```

The application must preserve these boundaries:

- RDF/DAQ processing is local on Raspberry.
- T900 is a transparent serial transport.
- PPP provides the IP link.
- MQTT is the application transport for telemetry and command.
- The 480x320 Raspberry panel is a local status UI only; it must not be required for telemetry to work.
- Ground receipt is a separate concept from MQTT connection.
- Do not replace native SDR processing or push raw IQ through T900.

## 2. Known Network Baseline

### Raspberry / UAV

```text
PPP IP      : 10.90.0.2
T900 alias  : /dev/t900
USB VID:PID : 1a86:7523
USB path    : platform-xhci-hcd.0-usb-0:2:1.0
PPP service : t900-ppp.service
```

### Ubuntu / Ground

```text
PPP IP           : 10.90.0.1
T900 alias       : /dev/t900-ground
USB VID:PID      : 1a86:7523
USB path         : pci-0000:00:14.0-usb-0:3:1.0
PPP service      : t900-ground-ppp.service
```

Transparent serial and PPP connectivity have already been reported working. Do not redesign PPP unless debugging proves that layer is broken.

## 3. Throughput Constraints

Historical project bench test:

```text
10 kbit/s -> 0% loss
12 kbit/s -> 0% loss
15 kbit/s -> 0% loss
18 kbit/s -> maximum tested clean bench point
20 kbit/s -> overload starts
```

Operational design target:

```text
continuous application traffic : about 8-10 kbit/s
short burst                    : about 12-15 kbit/s
18 kbit/s                      : not a continuous operating target
```

The release therefore prioritizes health/command over bulk angular graphics.

Default BALANCED schedule:

```text
Current DoA            : 1 Hz, only new valid sample
Health                 : 1 Hz
Health detail          : 0.1 Hz / every 10 s
360-point angular Q16  : 1 frame / 4 s, 2 chunks
Ground receipt         : 1 / 5 s
State                  : event + reconnect + refresh 60 s
Config reported        : startup/change/request/reconnect
Command/result         : on demand
Navigation             : OFF initially
```

During an active mutating command, new angular bulk transmission should pause first.

## 4. Native Raspberry Sources

RDF Node reads local SDR output. It should not repeatedly fetch these through T900.

Expected source directory is configured as `source.share_dir` and must be discovered/verified on the real Raspberry rather than guessed.

Files:

```text
status.json
DOA_value.html
settings.json
doa.xml              # optional/cross-check, not authoritative by default
```

Important facts:

### `DOA_value.html`

It is not HTML in semantic content. It is a one-line CSV record with 377 fields:

```text
1      timestamp_ms
2      current DoA
3      native confidence/PAPR-like metric
4      native power/RSSI-like value
5      frequency_hz
6      array type
7      processing latency
8      station ID
9-13   position/heading-related values
14-17  reserved
18-377 angular_power[0..359]
```

Do not reconstruct the 360-point graph from a single current DoA. The 360 samples are native data.

### Angle convention

CSV and XML historically use different direction conventions. Current application config includes:

```yaml
source:
  authority_verified: false
  angle_verified: false
  angle_mode: theta_mirror
```

`theta_mirror` follows the upstream relationship:

```text
canonical candidate = (360 - CSV_DoA) mod 360
```

Do not declare true-north/global bearing until orientation, heading, mounting offset, source authority, and timing are validated.

### Confidence / power

Do not silently convert native confidence to probability 0..1 unless an approved calibration exists.
Do not use Kraken power as T900 RSSI.
Do not call the power value calibrated dBm unless verified.

## 5. Data Validity Rules

`service active`, `HTTP 200`, `file exists`, and `MQTT connected` are NOT equivalent to valid DoA.

LIVE DoA should require, at minimum:

- authoritative source approved;
- angle convention approved;
- source timestamp fresh;
- new record progression;
- DAQ health acceptable;
- frame/sample-delay/IQ sync acceptable when available;
- configuration attribution valid;
- clock state trusted enough for the selected operation.

If a file is stale, malformed, partial, or DAQ unhealthy:

- retain last-known internally with age/provenance if useful;
- do not republish it as a new LIVE measurement;
- expose a truthful state such as `NO_FRESH_DOA`, `STALE`, `DEGRADED`, or `UNVERIFIED`.

Do not infer `NO_DETECTION` from a stale file alone.

## 6. Runtime Services

### Raspberry

Existing link service:

```text
t900-ppp.service
```

New RDF Node services:

```text
rdf-edge.service
rdf-control-helper.service
rdf-kiosk.service       # user/display service or kiosk launcher depending desktop session
```

Edge systemd unit runs approximately:

```text
/usr/bin/python3 /opt/rdf-node/current/run.py edge --config /etc/rdf-node/config.yaml
```

Important rule:

- Edge must remain alive if SDR stops.
- Edge must remain alive if Ground/MQTT/PPP disappears.
- Browser failure must not stop Edge.
- PPP failure must not automatically restart SDR.

### Ubuntu / Ground

Existing link service:

```text
t900-ground-ppp.service
```

Companion release services:

```text
rdf-ground-mqtt.service   # broker installed by provisioning path
rdf-ground.service        # Ground receiver / receipt / preview
```

Ground receiver runs approximately:

```text
/usr/bin/python3 /opt/rdf-node/current/run.py ground --config /etc/rdf-ground/config.yaml
```

Ground preview/API default:

```text
http://127.0.0.1:8791
```

Raspberry local panel/API default:

```text
http://127.0.0.1:8790
```

## 7. Important Files and Directories

Release root:

```text
/opt/rdf-node/current
```

Raspberry config:

```text
/etc/rdf-node/config.yaml
/etc/rdf-node/helper.yaml
/etc/rdf-node/admin-hash.json
/etc/rdf-node/credentials/...
```

Raspberry runtime/durable state:

```text
/var/lib/rdf-node
/var/lib/rdf-node-control
/run/rdf-node
/run/rdf-node-control
```

Ground config/state:

```text
/etc/rdf-ground/config.yaml
/etc/rdf-ground-mqtt/...
/var/lib/rdf-ground
```

Key source modules in the package:

```text
src/rdf_node/agent.py     orchestration/scheduling
src/rdf_node/source.py    native source parser/collector
src/rdf_node/codec.py     360-point angular codec/chunking
src/rdf_node/mqtt.py      MQTT wire/client/outbox
src/rdf_node/monitor.py   host/link probes
src/rdf_node/control.py   command validation/journal/workers
src/rdf_node/helper.py    privileged local helper allowlist
src/rdf_node/journal.py   durable command journal
src/rdf_node/api.py       loopback API/auth/UI backend
src/rdf_node/ground.py    Ground receiver/receipt/decoder
src/rdf_node/cli.py       setup/doctor/demo/approval tools
web/                      Raspberry and Ground browser assets
deploy/                   systemd/kiosk files
tests/                    automated tests and broker fixture
```

Documentation to read before changing behavior:

```text
README.md
docs/IMPLEMENTATION.md
docs/PROTOCOL.md
docs/MQTT_GROUND.md
docs/CONTROL.md
docs/OPERATIONS.md
docs/TEST_REPORT.md
```

## 8. Important Config Baseline

Representative `/etc/rdf-node/config.yaml` defaults:

```yaml
schema_version: 2
node_id: uav-01
runtime_mode: read_only

source:
  share_dir: null
  output_vfo: 0
  authority_verified: false
  angle_verified: false
  angle_mode: theta_mirror
  poll_ms: 250
  require_frame_progress: true

link:
  serial_alias: /dev/t900
  expected_vid: '1a86'
  expected_pid: '7523'
  expected_usb_path: platform-xhci-hcd.0-usb-0:2:1.0
  local_ip: 10.90.0.2
  peer_ip: 10.90.0.1
  engine_service: null

mqtt:
  enabled: false
  host: 10.90.0.1
  port: 8883
  tls: true
  keepalive: 15

telemetry:
  profile: balanced
  resume_stable_seconds: 20
  require_ground_receipt_for_bulk: true
  control_budget_bytes_s: 850
  bulk_budget_bytes_s: 350

freshness:
  source_status_ms: 3000
  doa_ms: 2500
  frame_progress_ms: 5000
  receipt_warn_ms: 10000
  receipt_lost_ms: 15000

control:
  config_patch_enabled: false
  processing_enabled: false
  restart_enabled: false
  reboot_enabled: false
  remote_commands_enabled: false

api:
  port: 8790

display:
  theme: dark
  blank_after_seconds: 0
```

Do not enable write controls just to make a UI button work.

## 9. MQTT Architecture

Default broker target:

```text
10.90.0.1:8883
TLS enabled
```

RDF Node uses separate logical control and bulk MQTT connections.

Namespace:

```text
sdr/v2/uav-01
```

Primary topics:

```text
telemetry/doa
telemetry/health
telemetry/health/detail
telemetry/angular
state
availability
config/reported
capabilities
ground/receipt

cmd/config/get
cmd/config/patch
cmd/processing/set
cmd/service/restart
cmd/system/reboot/prepare
cmd/system/reboot/execute
cmd/operation/get
cmd/stream/set

ack/config
ack/operation
```

Policy:

```text
DoA / health / angular : QoS 0, non-retained
state/config           : QoS 1, retained last-known where defined
commands               : QoS 1, NEVER retained
ACK/results             : QoS 1, non-retained
```

Key semantic rule:

```text
MQTT CONNECTED != Ground received data
PUBACK != command applied
HTTP 200 != DAQ healthy
```

Ground sends an application receipt every ~5 s summarizing data actually decoded.

## 10. Angular 360 Codec

Q16 default:

```text
48-byte RDF2 header
+ 360 * int16 samples
= 768-byte frame
```

Default frame is split into 2 MQTT chunks.

U8 alternative keeps all 360 angles but lowers amplitude precision.

Ground must assemble complete chunks for the same session/sequence before rendering. Never merge chunks from different frames.

Current DoA and angular curve have separate timestamps/ages.

## 11. Raspberry 480x320 UI

Target logical viewport:

```text
480 x 320 landscape
```

Tabs:

```text
Utama | Link | Sistem | Config
```

Overview priorities:

1. RDF processing state.
2. PPP status.
3. MQTT control status.
4. Ground backend receipt status.
5. Current relative DoA + age.
6. Active frequency.
7. DAQ health/sync.
8. Config sync/proof.
9. Last command/result.
10. Critical host alert / temperature summary.

Do not collapse all health dimensions into one generic ONLINE lamp.

Examples of valid combinations:

```text
PPP UP + MQTT CONNECTED + GROUND RX LOST + RDF RUNNING
```

or:

```text
PPP UP + MQTT CONNECTED + GROUND RX RECEIVING + DAQ DEGRADED + DOA INVALID
```

Those combinations are intentional and informative.

## 12. Command / Control Safety

Release installs read-only by default.

Supported safe-settings contract is intentionally small:

```text
center_frequency_hz
vfo0_frequency_hz
gain_db
vfo0_bandwidth_hz
vfo0_squelch_db
```

Remote mutating operations must validate:

- authenticated/authorized channel;
- node/session/boot identity;
- command ID;
- expiry;
- revision;
- supported operation/field;
- range/type;
- single active mutation;
- command deduplication;
- durable journal before side effect;
- read-back/evidence after side effect.

Result states can include:

```text
RECEIVED
ACCEPTED
APPLYING
VERIFYING
APPLIED
FAILED
PERSISTED_UNVERIFIED
OUTCOME_UNKNOWN
REJECTED / EXPIRED / CONFLICT / BUSY / UNSUPPORTED
```

Do not report APPLIED merely because settings.json was written.

## 13. Start / Stop / Restart / Reboot

Do not confuse scopes:

```text
Stop RDF        != Stop bridge
Restart RDF     != Reboot Raspberry
Restart bridge  != Restart PPP
```

Expected behavior when RDF is intentionally stopped:

```text
RDF engine      : STOPPED
rdf-edge        : remains running
MQTT health     : remains running if link available
local panel     : remains running
Ground command  : remains available if link available
DoA             : no new LIVE sample
```

Watchdog interaction is a major integration risk. An old SDR watchdog may restart a service that the operator intentionally stopped. Audit it before lifecycle controls are approved.

Reboot is maintenance-only and uses prepare/execute with a short-lived challenge and local maintenance lease.

## 14. Useful Debug Commands - Raspberry

Start diagnosis read-only whenever possible.

```bash
# services
systemctl status t900-ppp.service rdf-edge.service rdf-control-helper.service --no-pager -l

# recent logs
journalctl -u rdf-edge.service -n 80 --no-pager
journalctl -u rdf-control-helper.service -n 80 --no-pager
journalctl -u t900-ppp.service -n 80 --no-pager

# PPP
ip -br addr show ppp0
ip route
ping -c 3 10.90.0.1

# T900 alias
ls -l /dev/t900
readlink -f /dev/t900
udevadm info --query=property --name=/dev/t900 | grep -E 'ID_PATH=|ID_VENDOR_ID=|ID_MODEL_ID=|ID_MM_DEVICE_IGNORE='

# application doctor
sudo -u rdf-edge rdf-node doctor

# local API
curl -fsS http://127.0.0.1:8790/api/v2/healthz
curl -fsS http://127.0.0.1:8790/api/v2/readyz
curl -fsS http://127.0.0.1:8790/api/v2/snapshot

# config, but DO NOT publish secrets
sudo sed -n '1,220p' /etc/rdf-node/config.yaml

# package tests
cd /opt/rdf-node/current
python3 run.py selftest
```

Do not paste MQTT passwords, private keys, admin hashes, or full secret config into chat/debug tickets.

## 15. Useful Debug Commands - Ubuntu Ground

```bash
# link
systemctl status t900-ground-ppp.service --no-pager -l
ip -br addr show ppp0
ping -c 3 10.90.0.2

# RDF services
systemctl status rdf-ground-mqtt.service rdf-ground.service --no-pager -l
journalctl -u rdf-ground-mqtt.service -n 80 --no-pager
journalctl -u rdf-ground.service -n 80 --no-pager

# Ground preview/API
curl -fsS http://127.0.0.1:8791/

# listener check
ss -lntp | grep -E ':8883|:8791'
```

If a broker already exists, do not create a second listener blindly on the same port.

## 16. Debugging Order

Always debug from the lowest layer upward. Do not skip layers.

### Layer A - USB / serial

```text
Is the intended CH340 present?
Does /dev/t900 or /dev/t900-ground resolve correctly?
Is another process holding the serial device?
```

### Layer B - T900 transparent link

Only if PPP fails and serial path is suspect, perform a controlled bidirectional serial test. Stop pppd first so two programs do not own the same port.

### Layer C - PPP / IP

```text
Raspberry ppp0 = 10.90.0.2 peer 10.90.0.1
Ubuntu    ppp0 = 10.90.0.1 peer 10.90.0.2
```

Check ping and routes.

### Layer D - MQTT transport

Check:

```text
TCP/TLS reachable
certificate identity/time valid
CONNACK received
required SUBACK received
ACL does not reject expected topic
control and bulk state separately
```

### Layer E - Ground receipt

MQTT can be connected while Ground consumer is broken. Verify receipt sequence progresses.

### Layer F - Native source

Check:

```text
share_dir correct
status.json fresh
DAQ health and frame progress
DOA_value.html changes
377-field parse valid
source timestamp fresh
frequency/VFO expected
```

### Layer G - Validity / authority

Check authority, angle approval, clock, configuration attribution. A valid parse may still be UNVERIFIED by policy.

### Layer H - Scheduler/bandwidth

Check queue/backlog, latest-value replacement, angular pauses, receipt age, control/bulk budgets. Never solve congestion by increasing every queue.

### Layer I - UI

Only after backend state is correct. UI should display backend truth rather than inventing health.

### Layer J - Commands

Only after read-only path is stable. Verify authorization, revision, expiry, journal, side-effect scope, read-back/evidence, watchdog interaction, and outcome reconciliation.

## 17. Common Symptom -> Likely Area

### `PPP DOWN`

Investigate serial alias, pppd service, peer configuration, T900 transparent path.

### `PPP UP, MQTT DISCONNECTED`

Investigate broker listener, TLS, CA/SAN, credential, firewall, ACL, Ground service startup.

### `MQTT CONNECTED, GROUND RX UNCONFIRMED/LOST`

Investigate `rdf-ground.service`, subscription/decoder, Ground receipt publisher, session/sequence mapping.

### `GROUND RX good, DOA -- / stale`

Investigate source path, native file freshness, DAQ status, frame progress, authority/angle/config/clock gates.

### `DoA visible but angular paused`

Check telemetry profile, receipt freshness, active command, stable-resume timer, token budget, source validity.

### `Config write persisted but UI says PERSISTED_UNVERIFIED`

This can be correct. Runtime evidence was insufficient. Do not force APPLIED status.

### `STOP command succeeds then SDR comes back`

Audit watchdog/supervisor. Intentional STOP may be fighting legacy auto-restart logic.

### `Dashboard shows old-looking value`

Check source timestamp, API snapshot_seq, `age`, config revision, and whether the frontend is showing last-known with stale label.

## 18. Things the Agent Must NOT Do Without Explicit Evidence/Approval

Do NOT:

- redesign the working T900/PPP link just because application data is broken;
- run `rtl_test` or open SDR dongles while DAQ is active;
- reset all USB devices;
- `chmod -R 777` SDR/application directories;
- publish raw IQ/waterfall/audio/recordings over T900;
- publish full raw `settings.json` or secrets over MQTT;
- make Ground routinely poll port 8081 as the primary telemetry architecture;
- assume CSV and XML DoA are numerically identical;
- convert confidence to percent/probability without an approved mapping;
- treat Kraken power as T900 RSSI;
- fabricate GPS/heading/global bearing;
- turn stale DoA into a new measurement by replacing its timestamp;
- enable arbitrary shell commands through MQTT;
- accept arbitrary service names or filesystem paths from a remote command;
- make commands retained;
- assume QoS1 guarantees exactly-once side effects;
- report reboot success merely because the connection disappeared;
- report config APPLIED merely because a file write succeeded;
- stop/restart PPP or SDR as a side effect of restarting the dashboard/browser;
- expose the Raspberry API beyond loopback by default;
- disable TLS verification to make MQTT connect;
- leak passwords/private keys/admin hashes into logs or chat.

## 19. Test Status of Release 1.0.0

The packaged release records:

```text
113 automated tests passed
```

Covered areas include parser/gates, angular codec, command journal/helper policy, MQTT fixture network/TLS tests, API security, end-to-end synthetic flow, Ground receipt, and health continuity when the fixture SDR source stops.

Browser assets were checked at 480x320 using Chromium/Playwright in the build environment.

This does NOT prove:

- real Mosquitto interoperability on the user's Ubuntu;
- installer/systemd behavior on the user's Raspberry;
- cold boot/kiosk/autologin/display driver behavior;
- actual T900 MQTT throughput/latency;
- performance/thermal impact with KrakenSDR running;
- actual native settings watcher semantics;
- actual Start/Stop/Restart/Reboot behavior on the device;
- old Ground Dashboard integration.

Treat those as commissioning gates.

## 20. Recommended Commissioning Sequence

Use this order instead of enabling everything at once:

```text
1. Install RDF Node Raspberry read-only.
2. Verify local API and 480x320 panel.
3. Verify actual SDR share_dir and native freshness.
4. Validate DAQ/frame progress.
5. Validate source authority and angle convention.
6. Provision real Mosquitto/TLS/ACL on Ubuntu.
7. Verify health + Ground receipt through T900.
8. Verify current DoA through T900.
9. Verify full 360 Q16 graph and actual traffic budget.
10. Long-running reconnect/static-link test.
11. Enable config read/query.
12. Approve a minimal settings write subset only after native semantics/read-back are proven.
13. Test frequency retune with read-back/evidence.
14. Audit watchdog/service ownership.
15. Enable processing start/stop if safe.
16. Test restart RDF.
17. Reboot remains maintenance-only and last.
18. Cold boot both Raspberry and Ubuntu and verify automatic recovery.
```

## 21. Preferred AI Agent Debugging Behavior

When asked to debug this project, the AI agent should:

1. State which layer appears broken.
2. Ask for or collect the smallest relevant diagnostic output.
3. Prefer read-only inspection first.
4. Keep Raspberry and Ubuntu commands clearly separated.
5. Never ask the operator to paste secret credentials.
6. Explain what each command proves before changing configuration.
7. Preserve known-working PPP/T900 settings unless evidence implicates them.
8. Make one change at a time.
9. After a change, verify both the direct symptom and neighboring layers.
10. Keep rollback instructions for any state-changing change.
11. Distinguish measured facts from assumptions.
12. Use absolute paths/service names from the installed machine, not guessed archive paths.
13. Treat `active`, `connected`, and `HTTP 200` as liveness only, not end-to-end correctness.
14. Do not call data LIVE unless freshness/DAQ/authority gates pass.
15. For command failures, inspect command ID, revision, expiry, stage, journal, and read-back before retrying with a new ID.

## 22. Suggested Debug Report Format

When returning a diagnosis, use this structure:

```text
Observed symptom:

Current layer status:
USB/T900 :
PPP      :
MQTT     :
Ground RX:
SDR/DAQ  :
DoA      :
Angular  :
Config   :
UI       :

Evidence:
- ...

Most likely fault layer:
- ...

Next safe checks:
1. ...
2. ...
3. ...

Changes proposed:
- ...

Rollback:
- ...

Success criteria:
- ...
```

## 23. Fast Context Prompt for a New Agent

Use this if there is no room to attach the whole handoff:

```text
You are debugging RDF Node 1.0.0, a Raspberry KrakenSDR/SDR-DoA edge bridge and 480x320 status panel connected to an Ubuntu Ground Station through T900 transparent serial + PPP. Raspberry PPP is 10.90.0.2, Ground is 10.90.0.1. Existing PPP services are t900-ppp.service and t900-ground-ppp.service and are known to have worked; do not redesign them without evidence.

RDF Node reads local status.json, DOA_value.html (CSV 377 fields, including 360 angular values), settings.json, and optional doa.xml. Edge service is rdf-edge.service; privileged helper is rdf-control-helper.service. Raspberry API/UI is loopback 127.0.0.1:8790. Ubuntu companion receiver is rdf-ground.service, preview/API 127.0.0.1:8791, and the optional dedicated broker service is rdf-ground-mqtt.service. MQTT target is normally 10.90.0.1:8883 TLS with separate control and bulk clients.

Default telemetry is DoA 1Hz, health 1Hz, health detail every10s, full Q16 360-point angular every4s, Ground receipt every5s, state/config event-driven. Continuous T900 target is roughly8-10kbit/s; command/health have priority and angular pauses during mutations or degraded receipt.

MQTT connected is NOT proof Ground received data. Ground receipt is separate. Service active/HTTP200 is NOT proof DAQ healthy. Stale native DoA must never be republished as new LIVE data. CSV/XML angle conventions differ historically. Do not convert native confidence to probability without calibration and do not treat Kraken power as T900 RSSI.

Controls are read-only by default. Safe settings are limited to center_frequency_hz, vfo0_frequency_hz, gain_db, vfo0_bandwidth_hz, and vfo0_squelch_db after approval. Commands use ID/revision/expiry/dedup/journal/read-back; write success does not equal runtime APPLIED. Stop RDF must leave edge/health/panel alive. Audit watchdog before lifecycle control. Reboot is maintenance-only prepare/execute and requires proof of a new boot to call it successful.

Debug bottom-up: USB/serial -> T900 -> PPP -> MQTT/TLS/ACL -> Ground receipt -> native source -> DAQ/freshness/authority -> scheduler/bandwidth -> UI -> commands. Use read-only checks first, keep Raspberry and Ubuntu commands separate, never request secrets, and never make broad service/USB/reset/chmod changes without evidence.
```

---

This handoff summarizes the packaged RDF Node 1.0.0 implementation and the Telemetry RDF architecture discussed during commissioning. The installed machine is the final authority for actual paths, unit names, source state, certificates, and runtime capabilities.
