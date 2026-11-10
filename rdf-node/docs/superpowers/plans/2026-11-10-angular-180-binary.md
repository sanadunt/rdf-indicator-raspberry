# Angular "Seimbang" 180 points + binary LoRa Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Edge sends each Angular curve as 180 pair-averaged points. The LoRa bridge carries it in ~2 frames instead of 5, and Ground turns it back into a smooth 360° curve. Then 2 s and 4 s curve intervals are measured with dummy commands running.

**Architecture:**
- Edge 1.12.0 (contract 1.12) averages degree pairs and marks the message `step_deg: 2`.
- The LoRa bridge packs the two Angular topics losslessly into a compact binary body (new frame flag), and the Ground bridge rebuilds the exact same JSON bytes.
- Ground console 3.20.0 upsamples 180→360 at intake with a periodic monotone cubic (PCHIP), so everything downstream still sees 360.

**Tech Stack:** Python 3 stdlib (rdf-node, unittest), Ground console Python (pytest-style `tools/test_*.py`) + React frontend (unchanged).

**Spec:** `rdf-node/docs/superpowers/specs/2026-11-10-angular-180-binary-design.md`

## Global Constraints

- Topic ids in `lora.py` never change; frame header layout unchanged; only a new flag bit `F_PACKED=16`.
- `lora.py` `ZDICT` must not change (old peers decompress with it).
- `angular_points: 360` must give Angular payloads byte-identical to 1.11 (no `step_deg` key).
- Edge local panel keeps reading 360 values from the source (`/api/v2/angular/latest`).
- Pi: per-command user OK, `install.sh` once per upgrade, no reboot, no pkill/killall, never show secrets.
- Ground: user `rdf` has no passwordless sudo and no Docker group; Ground installs need the user's password, so the user runs those commands.
- Legacy `rdf-ground` 1.0.0 service on Ground stays untouched (decided).
- Reports to the user are in Indonesian.

## Review Focus

1. **Curve whose values are not on the 0.01 grid, or span more than 2.55.** The bridge must fall back to plain JSON, never send a lossy body. Test in Task 2.
2. **Old Ground bridge receives an `F_PACKED` frame.** Install order puts Ground bridge first. A new Ground bridge receiving an old unpacked curve must pass it through unchanged. Test in Task 2.
3. **Peak between two 2° points.** After upsampling, the peak must be within 1° of the original 360 peak, so `PEAK_MOVED` (3° rule) does not fire more than before. Test in Task 3.
4. **Wrap-around at 359°→0°.** No seam or jump. Test in Tasks 1 and 3 (peak at bin 359/0).
5. **Diagnostic Angular** (`telemetry/diagnostic/angular`, topic 6) gets the same 180 treatment and packing as topic 4. Tests in Tasks 1 and 2.

---

### Task 1: Edge 1.12 — pair averaging, `step_deg`, config, pacing limits

**Files:**
- Create: `rdf-node/src/rdf_node/angular.py`
- Modify:
  - `rdf-node/src/rdf_node/config.py`: `telemetry.angular_points` default 180, allowed {180, 360}. `bulk_budget_bytes_s` range 200..2000. Aggregate cap 1500 → 4000.
  - `rdf-node/src/rdf_node/agent.py:878-893`: `_live_angular`, `_diagnostic_angular`.
  - `agent.py:629`: capabilities `count`, plus a new `angular_step_deg`.
  - `rdf-node/src/rdf_node/codec.py`: `decode` accepts 180 with `step_deg` 2 and upsamples.
  - `rdf-node/src/rdf_node/__init__.py`: version `1.12.0`, `CONTRACT='1.12'`.
  - `contract/schema/telemetry.angular.json` and `telemetry.diagnostic.angular.json`: `values` 180 or 360; optional `step_deg` enum [1, 2].
  - `contract/schema/capabilities.json`: `count` enum [180, 360]; optional `angular_step_deg`.
  - `config/example.yaml`, `docs/PROTOCOL.md`, `docs/MQTT_TOPIC_SUMMARY.md` (Angular section), `CHANGELOG.md` (new `## 1.12.0` section).
- Test: `rdf-node/tests/test_angular.py` (new); `tests/test_contract.py`, `tests/test_nav.py` (existing pass).

**Interfaces:**
- Produces:
  - `angular.average_pairs(values: list[float]) -> list[float]`: 360 → 180, `round((v[2k]+v[2k+1])/2, 2)`.
  - `angular.upsample_pairs(values: list[float]) -> list[float]`: 180 → 360. Periodic PCHIP with knots at x=2k+0.5, evaluated at integer degrees 0..359.
  - Angular payload: `'step_deg':2` inserted just before `'values'`, and `'values'` stays the **last key**. Task 2 relies on this.
  - `codec.decode` returns a dict with 360 `values`, plus `upsampled_from: 180` when the input had 180.

- [ ] **Step 1: Write failing tests in `tests/test_angular.py`**
  - `test_average_pairs_example`: `average_pairs([0.1,0.5]+[0.0]*358)[0] == 0.3`; result length 180.
  - `test_average_pairs_rounds_to_hundredths`: every output equals `round(x,2)` of itself.
  - `test_upsample_constant`: `upsample_pairs([0.7]*180) == [0.7]*360` within 1e-9.
  - `test_upsample_reconstructs_smooth_curve`: `orig=[1+cos(radians(d-137)) for d in range(360)]`, then `up=upsample_pairs(average_pairs(orig))`. Assert max |up-orig| < 0.02 and the argmax of `up` is within 1 of 137.
  - `test_upsample_no_overshoot`: one spike `[0]*180` with `[90]=1.0`. Output stays within [0, 1].
  - `test_upsample_wraps`: spike at index 179 and at 0. Argmax lands at 358/359 and 0/1 respectively; `up[359]` and `up[0]` differ by < 0.5.
  - `test_live_angular_180_payload` (agent fixture as in `tests/test_contract.py:79`):
    - the payload has 180 values, `step_deg` 2, and `values` is its last key;
    - it conforms to the schema;
    - with config `angular_points: 360` it is byte-identical to the 1.11 shape (no `step_deg`).
  - `test_diagnostic_angular_180_payload`: same check for `_diagnostic_angular`.
  - `test_capabilities_count_follows_points`: 180 → `count` 180 and `angular_step_deg` 2.
  - `test_codec_decode_upsamples_180`: `decode` of a 180 payload gives 360 values and `upsampled_from==180`. A 180 payload without `step_deg` raises `EXPECTED_360_SAMPLES`.
  - `test_config_bulk_budget_2000_ok_and_aggregate_4000`: bulk 2000 + control 1000 validates; bulk 2001 is rejected; aggregate 4001 is rejected.

- [ ] **Step 2: Run** `cd rdf-node/tests && PYTHONPATH=../src:../vendor:. python3 -m unittest test_angular -v`. Expected: FAIL (`No module named 'rdf_node.angular'`).

- [ ] **Step 3: Implement**
  - Write `angular.py`. Periodic PCHIP:
    - Slopes `m_k` use the Fritsch–Carlson harmonic mean of the neighbouring secants, with indices taken mod 180, and 0 at local extrema.
    - Evaluate the Hermite cubic on the segment `[2k+0.5, 2k+2.5]` that holds each degree `d` (degree 0 sits on segment k=179 shifted by −360).
  - Wire `agent.py` and `config.py` as listed.
  - The Edge spacing formula (`agent.py:~1091`) stays as is. The raised budget limits are what make 2 s possible.

- [ ] **Step 4: Run** the new tests, then `python3 run.py selftest`. Expected: test_angular OK. Selftest shows only the 5 known macOS network/helper failures (`test_helper_socket_failures_name_what_happened`, the 3 slow-uplink/backlog tests, and `test_probe_reads_tcp_and_fails_closed_elsewhere`).

- [ ] **Step 5: Commit** `Edge 1.12.0: Angular as 180 pair-averaged points (contract 1.12)`.

### Task 2: LoRa bridge — lossless binary Angular body

**Files:**
- Modify: `rdf-node/src/rdf_node/lora.py`
  - `F_PACKED=16`.
  - `pack_angular(payload: bytes) -> bytes | None` and `unpack_angular(body: bytes) -> bytes`.
  - `Pending`/`Sender.offer(..., packed=False)` and `split_message(..., packed=False)` set the flag.
  - `Bridge._from_mqtt` packs topics 4 and 6.
  - `Receiver._assemble` unpacks when the flag is set.
  - Default `angular_every_s` 1.5, both in `cli.py` (`--angular-every` default 1.5) and in `Bridge.__init__`.
- Modify: `rdf-node/docs/LORA_SERIAL.md`: flag table, Angular row ("min. tiap 1.5 s"), budget section with new sizes, install order (Ground bridge first).
- Test: `rdf-node/tests/test_lora.py`

**Interfaces:**
- Consumes: Task 1 payload shape, with `values` as the last key and values on the 0.01 grid.
- Produces: `pack_angular` returns the body before zlib, or `None` to mean "send the original JSON". The body is:
  - byte `0x01`, the layout version;
  - `!i` `nmin`, the minimum of `round(v*100)`;
  - `!H` the header length;
  - the header: the original JSON bytes up to and excluding `,"values":[`;
  - one `u8` per value, `round(v*100)-nmin`.

  `unpack_angular` rebuilds `header + ',"values":[' + ','.join(json.dumps(n/100) ...) + ']}'`. Returns `None` unless:
  - the payload ends with the values array as its last key;
  - every `round(v*100)/100 == v`;
  - every delta ≤ 255;
  - the round trip matches the original bytes exactly.

- [ ] **Step 1: Write failing tests**
  - `test_pack_angular_round_trip_180_and_360`: real-shaped payloads from Task 1 (180) and 1.11 (360). `unpack_angular(pack_angular(p)) == p`, and `len(compress(pack_angular(p180))[0]) <= 400`.
  - `test_pack_angular_refuses_lossy`: values with 3 decimals → `None`; range 3.0 → `None`; `values` not the last key → `None`.
  - `test_bridge_sends_packed_angular_in_two_frames`: a Sender offer of a 180 payload with `packed=True` → `split_message` gives ≤ 2 frames, all with `F_PACKED` set.
  - `test_receiver_unpacks_packed_angular`: feed those frames to `Receiver` → the payload equals the original JSON bytes.
  - `test_unpacked_angular_still_passes_through`: an old-style 360 payload offered unpacked arrives byte-identical (existing `BridgeTests` ANGULAR test keeps passing).
  - Extend the existing end-to-end broker test (`tests/test_lora.py:399`) with a 180 payload: the Ground broker gets identical bytes.

- [ ] **Step 2: Run** `cd rdf-node/tests && PYTHONPATH=../src:../vendor:. python3 -m unittest test_lora -v`. Expected: FAIL (`pack_angular` missing).

- [ ] **Step 3: Implement** as specified in Interfaces. `pack_angular` checks the round trip itself before returning.

- [ ] **Step 4: Run** test_lora plus selftest. Expected: OK, with the same 5 known macOS failures only.

- [ ] **Step 5: Commit** `LoRa bridge: lossless binary Angular body (F_PACKED), angular-every 1.5 s`.

### Task 3: Ground console 3.20.0 — accept 180 and upsample to 360

**Files** (repo `/Users/mac/Projects/sdr-doa-ground-control`, new worktree on branch `claude/angular-180` from `origin/main`):
- Create: `tools/angular_resample.py` with `upsample_pairs(values: list[float]) -> list[float]`. It is the same algorithm as rdf-node `angular.upsample_pairs` and must pass the same test vectors.
- Modify: `tools/rdf_node_mqtt_v2.py:775-781` (`_validate_angular_frame`):
  - accept 360 as today, or 180 when `step_deg == 2`, which is upsampled;
  - set `value["upsampled_from"]=180`;
  - `peak_index` comes from the upsampled values;
  - 180 without `step_deg` 2 → `EXPECTED_360_SAMPLES`.
- Modify: `VERSION` → `3.20.0`; `CHANGELOG.md` gets a new `## 3.20.0` section in Indonesian, in the existing style.
- Test: `tools/test_angular_resample.py` (new), `tools/test_rdf_node_mqtt_v2.py`.

**Interfaces:**
- Consumes: the Task 1 payload (`step_deg` 2, 180 values).
- Produces: the node view's Angular with 360 `values`, `peak_index`, and `upsampled_from`. The frontend is unchanged.

- [ ] **Step 1: Write failing tests**
  - In `test_angular_resample.py`: the same five vectors as Task 1 Step 1 (example, constant, smooth-cos error < 0.02 with peak within 1°, no overshoot, wrap).
  - In `test_rdf_node_mqtt_v2.py`:
    - `test_angular_180_is_upsampled_to_360`: a valid 180 frame is accepted with 360 values and `upsampled_from==180`;
    - `test_angular_180_without_step_is_rejected` → `EXPECTED_360_SAMPLES`;
    - `test_angular_180_pairing_same_frame`: a DoA and a 180 Angular with the same `q` pair as `SAME_FRAME` when the original peak matches the DoA.

- [ ] **Step 2: Run** `python3 -m pytest tools/test_angular_resample.py tools/test_rdf_node_mqtt_v2.py -q`. Use the repo's documented runner if it differs (see `AGENTS.md`/`CLAUDE.md`). Expected: FAIL.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** the two test files, then the full Python suite. Expected: new tests pass, and there are no failures beyond the known macOS-only ones on `main`. Record that count before starting.

- [ ] **Step 5: Commit**, push the branch, and open a PR to `sanadunt/sdr-doa-ground-control` (attribution line from the system reminder). Do not merge or tag without the user.

### Task 4: Install (user approval per command)

Order: Ground bridge → Pi bridge + Edge → Pi config → Ground console. The Ground bridge goes first because an old Ground bridge would publish packed bodies as garbage.

- [ ] **Step 1: Read-only checks.**
  - Pi: `/opt/rdf-node/current`, `/opt/rdf-lora` checksums, `config.yaml` budgets.
  - Ground: `systemctl cat rdf-lora-ground` (where its code lives), and how the console is deployed (`docker-compose.release.yml`, image tag).
- [ ] **Step 2: Ground bridge.** Stage the new `lora.py` and `cli.py` in `~/rdf-lora/`. Give the user the exact `sudo` copy and restart commands to run, since we have no sudo there. Verify `lora/ground` status shows link UP.
- [ ] **Step 3: Pi.**
  - Build the Edge 1.12.0 package as done for 1.11.0 (see `docs/OPERATIONS.md` upgrade section).
  - Run `install.sh` once (with OK). This keeps the backup and the previous release for rollback.
  - Update `/opt/rdf-lora/src/rdf_node/lora.py` and `cli.py` with backups.
  - Restart `rdf-lora-edge` and `rdf-edge` (each with OK).
- [ ] **Step 4: Pi config** (with OK): `bulk_budget_bytes_s` 500→1000, plus a backup. Leave `angular_points` unset, so the default 180 applies. Restart `rdf-edge`.
- [ ] **Step 5: Ground console 3.20.0.** After the user merges and tags (CI builds the image), the user pulls and restarts the release compose on Ground. Verify `/api/snapshot` Angular `upsampled_from==180`.

### Task 5: Bench — 2 s vs 4 s with dummy commands

**Files:**
- Create: `rdf-node/tools/lora_bench/measure_ground.py` and `measure_edge.sh`. Base them on the scratchpad versions, adding a run length argument and `PEAK_MOVED` share.
- Create: `rdf-node/tools/lora_bench/dummy_ops.py`. It publishes `cmd/operation/get` on the Ground broker every 5 s for N seconds, in the command format of `docs/PROTOCOL.md` (unique id, `expires_ms`), and counts `ack/operation` replies and their RTT.
- Modify: `rdf-node/docs/LORA_SERIAL.md`: add a "Hasil uji 2026-11-xx" table.

Steps:
- [ ] **Step 1:** Ground sends `stream.set` `graph_u8` (2 s). Wait 25 s for the Edge's 20 s Angular pause to end. Run the bench for 180 s with a signal above squelch.
- [ ] **Step 2:** Run the same with `balanced` (4 s).
- [ ] **Step 3:** Report to the user in Indonesian, side by side with today's numbers:

  | | before (360, 4.5 s) | 2 s | 4 s |
  |---|---|---|---|

  Rows: curves arrived and gaps, `PEAK_MOVED` share, Edge→Ground B/s and loss, Ground→Edge loss, incomplete curves, dummy ACK rate and RTT, link changes.

  Then recommend one interval and leave the profile on the user's choice.
- [ ] **Step 4:** Commit the bench tools and docs.
