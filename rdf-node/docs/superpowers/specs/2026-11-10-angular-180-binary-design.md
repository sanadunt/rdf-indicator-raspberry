# Angular curve "Seimbang": 180 points, binary over LoRa, smooth 360 on Ground

Date: 2026-11-10. Repos: this one (RDF Node Edge + LoRa bridge, branch `feat/lora-serial`) and
`sanadunt/sdr-doa-ground-control` (Ground console, local checkout `/Users/mac/Projects/sdr-doa-ground-control`,
`origin/main` 3.19.1).

## Why

The Angular curve (the 360° response KrakenSDR computes with each DoA) draws the lobe on the Ground map. Over
the LoRa link it is the heavy item: 360 values as JSON, ~2.1 KB, zlib ~860 B, 5 radio frames. One lost frame
(~5% each) throws the whole curve away, so the lobe arrives every ~4.5 s at best with 9–30 s holes, and the link
has no room to send it more often. The user wants the lobe to update faster and more reliably, and wants to see
whether a 2 s or a 4 s curve interval works better without hurting commands from Ground.

## What the user decided

- **Seimbang (balanced) mode: 180 points.** Each point is the **average of two neighbouring degrees**
  (0+1, 2+3, …, 358+359). Example from the user: 0.1 and 0.5 → 0.3.
- **Binary on the radio**, not JSON text.
- **Ground still shows 360 and smooth.** Ground receives 180 points and turns them into a smooth 360° curve; the
  map lobe, polar plot and everything downstream keep seeing 360 values.
- **Raspberry sends Seimbang** (no mode switching asked for now).
- **After everything is installed, test curve intervals of 2 s and 4 s** and compare, and **send dummy
  (harmless) commands from Ground during each test** to check that commands still get through.

## Goals

1. Curve over LoRa ≤ 2 frames (target ~200–250 B on air) instead of 5.
2. Ground map lobe updates at the chosen interval (2 s or 4 s) with rare gaps.
3. Ground→Edge commands keep working: in each test ≥ 90% of Ground frames reach the Edge, and every dummy
   command gets its ACK (commands are sent 3×).
4. Ground keeps accepting the old 360-value curve (an Edge or bridge not yet upgraded must still work).

## Non-goals

- Switching between Halus 360 / Seimbang 180 / Ringan 90 at run time (possible later; the format should carry
  the point count so it can be added without another format change).
- Changing DoA itself (its angle stays 0.1° and is sent separately; the bearing line on the map is unaffected).
- The Edge local panel (480×320) — it reads the 360 curve locally and stays as it is.
- Health/GPS/compass changes discussed earlier (separate, not approved).

## Anti-goals (would count as failure even if it "works")

- Ground commands get lost again (see 2026-11-10: curves every 4.5 s with a wrong air-time estimate lost ~92% of
  Ground→Edge frames).
- The lobe on the map visibly jumps or shows a 2° staircase; or its peak points somewhere other than the DoA
  bearing so Ground hides it (`PEAK_MOVED`, peak must stay within 3° of the DoA).
- Breaking the old Edge or old Ground (mixed versions during rollout).

## Facts the builder needs

- Curve values are **not dB in practice**: KrakenSDR writes 360 numbers ~0.00–1.3 with 2 decimals (sampled
  2026-11-10 on the Pi, `/home/rdf/rdfsdr/rdfsdr_doa/_share/DOA_value.html`, columns 17+). Ground calls them
  `angular_power_db` / shifted dB. The binary format must not assume a range; it must carry what it needs to
  restore values with an error no larger than the source resolution (0.01) when the range allows, and stay
  sensible for larger ranges.
- Edge builds `telemetry/angular` in `src/rdf_node/agent.py` `_live_angular` / `_diagnostic_angular`
  (`values` = `r['values']`, 360 floats). Capabilities say `"count":360`, `"codecs":["json"]`.
- Edge spaces curves at `max(PROFILES[profile]['angular_s'], (json_size+topic+160)/telemetry.bulk_budget_bytes_s)`
  (`agent.py` ~1081). Profiles in `config.py`: `balanced` 4 s, `graph_u8` 2 s. Ground can switch profile with
  `cmd/stream/set` (`{"profile":...}`). Pi config now: control 1000 / bulk 500 B/s (validation: bulk 200..500,
  control+bulk ≤ 1500 — PPP-era limits; MQTT is now local and the LoRa bridge does the real pacing).
- LoRa bridge `src/rdf_node/lora.py`: transparent per-topic transport (zlib with a preset dictionary), frames
  ≤ 200 B, budget 1000 B/s, turn taking (edge marks a TURN frame ~1/s then stays quiet 0.45 s counted from its
  air-time estimate, `AIR_S_PER_BYTE` 0.0008), `--angular-every` 3.5 s min spacing. Angular max age in the bridge
  queue 8 s. Topic ids must never change, only be added.
- Ground validation: `tools/rdf_node_mqtt_v2.py` ~line 775 rejects anything but exactly 360 `values`
  (`EXPECTED_360_SAMPLES`) and derives `peak_index`. Everything after that (map `TacticalMap.tsx`, polar plot,
  pairing in `telemetry_source.py`) assumes 360. Over LoRa the Angular freshness window is 45 s.
- Deployed today: Pi Edge 1.11.0 (`/opt/rdf-node/current`) plus bridge files in `/opt/rdf-lora`
  (`run.py lora-bridge`, patched in place); Ground bridge `rdf-lora-ground.service`; Ground console as Docker
  release images (`docker-compose.release.yml`). Ground user `rdf` has no passwordless sudo and no Docker access:
  Ground installs need the user's password. On the Pi every changing command needs the user's OK per command;
  `install.sh` runs once per upgrade; no reboot; no pkill/killall.
- Measurement tooling used so far: Ground `/api/snapshot` (`mqtt.angular.received_at_ms`, `pairing`), bridge
  status topics `sdr/v2/uav-01/lora/{edge,ground}` (journal of each bridge), scripts in this session's
  scratchpad `lora_angular_deploy/measure_*.{py,sh}` (copy them into the repo's test tooling if useful).

- A second Ground consumer exists: this repo's legacy `rdf-ground` 1.0.0 companion runs on the Ground machine
  (`rdf-ground.service`, `/opt/rdf-node/current` → `releases/1.0.0-20261006185856-757084`, config
  `/etc/rdf-ground/config.yaml`). Its `codec.decode()` and `web/ground.js` accept exactly 360 values; the contract
  schema `contract/schema/telemetry.angular.json` also pins 360.
- The Edge panel reads the 360 values straight from the source (`/api/v2/angular/latest`), so averaging in
  `_live_angular` does not touch it.

## Shape of the solution (decided)

- **Averaging happens on the Edge** (the producer), so local MQTT, the radio and Ground all see the same 180
  points; the Edge says how many points and their spacing in the Angular message and in capabilities. This is a
  contract bump (1.12, Edge 1.12.0).
- **Binary encoding happens in the LoRa bridge** for the Angular topics only: the Edge bridge packs the values
  compactly and the Ground bridge rebuilds the same JSON Ground's MQTT consumers expect. Other topics stay as
  they are. The bridge must handle both 180 and 360 curves.
- **Ground upsamples at intake**: a 180-point curve is turned into 360 smooth values right where Ground
  validates the message, so the rest of the console is unchanged. Points sit at the centre of their pair
  (0.5°, 2.5°, …); interpolation is circular (359° joins 0°) and smooth (no staircase, no overshoot that invents
  a new peak). The node view keeps a note that the curve was upsampled from 180.
- **Old formats keep working** in every combination during rollout (old Edge → new Ground, new Edge → old
  Ground bridge/console must not crash or show garbage; if a combination cannot work, it must fail visibly with
  a reason code rather than draw a wrong lobe).

## Test plan the user asked for (after all parts are installed)

For each interval, 2 s then 4 s (Ground switches profile via `cmd/stream/set`, or the builder's equivalent),
run ~3 minutes with a real signal above squelch and record:
- curve arrivals at Ground (count, gaps), share hidden as `PEAK_MOVED`;
- LoRa B/s and frames/s Edge→Ground, frame loss both ways, incomplete curves;
- **dummy commands**: from Ground send `operation.get` repeatedly, e.g. one every 5 s; record ACK rate and
  round-trip time. (My call: not `config.get`, whose reply is the whole settings file, up to 8 KB, sent twice,
  and would skew the link numbers. `operation.get` is handled inline and does not pause Angular sending;
  `stream.set`, used to switch the interval, does start the Edge's 20 s Angular pause, so measure after it.)
- link status on both bridges (UP/DOWN changes).
Report a side-by-side table (now vs 2 s vs 4 s) to the user in Indonesian and recommend one interval.

## Left to the builder

Exact binary layout and how the bridge recognises/rebuilds the message; the Edge pacing change needed so a
2 s curve is possible (budget limits or pacing rule); the interpolation method; version numbers and CHANGELOG
entries; test fixtures (Ground has fixture generation via `make_fixtures`); the install order across Pi bridge,
Pi Edge, Ground bridge and Ground console; how to run the dummy commands.
