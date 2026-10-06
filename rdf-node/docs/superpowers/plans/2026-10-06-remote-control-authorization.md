# Remote Control Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Ground invoke all existing supported Edge controls after one local root approval, without turning the local maintenance lease into permanent authorization.

**Architecture:** Persist a second root-owned helper policy bit beside the existing Edge `remote_commands_enabled` flag. Preserve the trusted actor through the single command worker and let only `ground-controller` bypass the helper lease; local lifecycle/reboot/shutdown still need a live lease. Expand Ground's existing settings form to cover the five helper-approved fields.

**Tech Stack:** Python 3.10+, standard library, bundled PyYAML, MQTT 5, static HTML/CSS/JavaScript, `unittest`.

**Spec:** `SPEC-remote-control-authorization.md`; capability index: `CAPABILITY_MAP.md`.

## Global Constraints

- Root-local explicit remote approval; retain per-action local capability approvals; preserve MQTT command validation, bounded deadlines, session/boot identity, config revision, durable idempotency journal, reboot/shutdown prepare-execute challenge, helper allowlists, and all setting field/value checks. Local Edge GUI lifecycle/reboot/shutdown remains lease-gated.
- Remote authorization does not imply broker receipt, DAQ health, applied SDR settings, host reboot completion, or Ground connectivity.
- Ask first: changing the safe settings allowlist or value ranges; making local UI operations lease-free; adding operations outside the current controlled command set.
- Never: remote lease-open/extend/close; shell execution; caller-selected systemd unit/path; arbitrary settings JSON; automatic retry of an uncertain side effect.
- MQTT may be plaintext and anonymous on the user's private link; do not claim publisher authentication or encryption.
- Work on `main`; stage only files in the task; preserve pre-existing unrelated changes. Commit scoped, tested tasks; push only after full integration verification.

## Review Focus

1. Remote approval must never remove the local lifecycle/reboot/shutdown lease gate. Test: `HelperTests.test_local_lifecycle_still_requires_maintenance_when_remote_enabled` in Task 1.
2. Root grant/revoke must survive helper process reload and OS restart. Test: `ControlApprovalTests.test_remote_grant_and_revoke_survive_policy_reload` in Task 1.
3. MQTT payload fields must not choose the actor, and the queue must not lose actor identity. Test: `CommandTests.test_ground_actor_is_internal_and_reaches_helper` in Task 2.
4. `stream.set` has no helper per-action bit but still requires the global remote grant. Test: `CommandTests.test_remote_stream_requires_edge_and_helper_grants` in Task 2.
5. Frequency updates must preserve the center/VFO0 equality rule; every other supported field must retain helper value validation. Test existing/new `validate_changes` behavior in Task 1, then browser form smoke in Task 3.

---

### Task 1: Persist and enforce helper remote approval

**Files:**
- Modify: `src/rdf_node/helper.py`
- Modify: `src/rdf_node/cli.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: current `Controller.dispatch(req, uid)`, `DEFAULT_POLICY`, and `controls approve --remote`.
- Produces: `DEFAULT_POLICY['allow_remote_control']` defaulting to `False`; helper status exposes that flag; lifecycle, reboot, and shutdown RPCs accept only the internal origins `ground-controller` or `local-admin`. Ground-origin operations bypass maintenance only when the root policy flag is true. Local operations remain lease-gated.
- CLI `approve --remote` persists the helper policy bit and existing Edge config bit. `approve` without `--remote` revokes both remote bits while leaving separately approved action capabilities intact.

- [x] **Step 1: Write failing helper and CLI tests**

Add `HelperTests.test_remote_lifecycle_requires_root_policy_not_lease`, `HelperTests.test_local_lifecycle_still_requires_maintenance_when_remote_enabled`, and `ControlApprovalTests.test_remote_grant_and_revoke_survive_policy_reload`. Use temporary policy/config files and mocked root/systemd boundaries; reload a fresh `Controller` from the saved YAML to prove persistence.

- [x] **Step 2: Run the focused tests and verify the expected failures**

Run: `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p test_core.py`
Expected: fail because helper policy does not yet contain remote authority/origin handling, and CLI does not persist the new policy bit.

- [x] **Step 3: Implement the root-owned remote policy and revocation path**

Add the default-false policy key, validate it as a boolean, expose it through helper status, and branch maintenance checks on the validated internal origin. Update `controls approve` to persist both flags and preserve existing per-action approvals when remote is revoked.

- [x] **Step 4: Rerun focused tests**

Run: `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p test_core.py`
Expected: grant survives policy reload; revoke survives policy reload; local operations still require the live lease; remote operations still require per-action approval/challenge.

- [x] **Step 5: Commit the tested helper/CLI slice**

Stage only `src/rdf_node/helper.py`, `src/rdf_node/cli.py`, and `tests/test_core.py`.

### Task 2: Preserve actor and enforce both remote gates

**Files:**
- Modify: `src/rdf_node/control.py`
- Modify: `src/rdf_node/agent.py`
- Test: `tests/test_system.py`

**Interfaces:**
- Consumes: Task 1 helper `allow_remote_control` status and `ground-controller` / `local-admin` origin values.
- Produces: command worker queue items `(request, actor)`; `_worker()` calls `_execute(request, actor)`; `Agent.capabilities()['remote_commands']` is true only when Edge config and root helper policy both grant remote authority. The actor remains internal and is never an allowed MQTT request field.

- [x] **Step 1: Write failing command-path tests**

Add `CommandTests.test_ground_actor_is_internal_and_reaches_helper`, `CommandTests.test_remote_payload_cannot_choose_origin`, and `CommandTests.test_remote_stream_requires_edge_and_helper_grants`. Exercise the real command manager submission/worker path with the existing test agent and a mocked helper side effect.

- [x] **Step 2: Run focused tests and verify the expected failures**

Run: `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p test_system.py`
Expected: actor context is lost at the queue boundary and effective remote capability ignores the helper grant. The strict-field test should already pass because `CommandManager.submit()` rejects unknown envelope fields; preserve that invariant and do not weaken its test.

- [x] **Step 3: Carry trusted actor through the serialized worker**

Queue `(request, actor)`, pass the actor to `_execute`, and include the fixed origin only in helper RPCs that need lease decisions. Require both remote flags before accepting any Ground write, including `stream.set`; leave read-only requests and all existing TTL/session/boot/revision checks unchanged.

- [x] **Step 4: Rerun the focused tests**

Run: `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p test_system.py`
Expected: Ground operations fail closed if either grant is absent; actor is derived only from the transport/API entry point; local actor reaches helper as local.

- [x] **Step 5: Commit the tested actor-boundary slice**

Stage only `src/rdf_node/control.py`, `src/rdf_node/agent.py`, and `tests/test_system.py`.

### Task 3: Expose every supported safe SDR setting in Ground UI

**Files:**
- Modify: `web/ground.html`
- Modify: `web/ground.js`
- Modify: `web/ground.css`

**Interfaces:**
- Consumes: existing Ground `POST /api/v2/commands` contract for `config.patch`, `snapshot.config.safe_settings`, and helper field/value validation.
- Produces: labeled controls for frequency, bandwidth, gain, and squelch. The frequency control submits both `center_frequency_hz` and `vfo0_frequency_hz` with the same value. Updates retain current revision and show persisted/runtime proof distinctly.

- [x] **Step 1: Record the failing user workflow before edits**

Open the Ground Companion in Chromium and verify that bandwidth, gain, and squelch cannot currently be edited from the remote panel. Record the browser baseline; do not add a source-text or DOM-presence regression test.

- [x] **Step 2: Implement the Ground settings form**

Add visible labels, current reported safe values, inline invalid-value feedback, and a single clear apply path. Preserve the existing dark palette and visible keyboard focus. At narrow content widths, stack the form and panels, preserve 44 px touch targets, and avoid horizontal overflow.

- [x] **Step 3: Verify the settings workflow in the browser**

Use the actual Ground UI and a safe fixture-backed command path. Change each supported field, observe the command/result and safe config report, verify the coordinated frequency pair, exercise a rejected value, keyboard navigation, focus, narrow layout, and no horizontal page scroll.

- [x] **Step 4: Commit the verified UI slice**

Stage only `web/ground.html`, `web/ground.js`, and `web/ground.css`.

### Task 4: Update remote authorization operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/CONTROL.md`
- Modify: `docs/IMPLEMENTATION.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: tested behavior from Tasks 1–3.
- Produces: exact local root grant/revoke commands, the distinction between persistent remote permission and short local lease, accepted private-link plaintext/anonymous risk, supported settings and validation limits, and clear device acceptance limits.

- [x] **Step 1: Update the control and deployment instructions**

Replace the old troubleshooting implication that a remote Ground operator must keep opening a lease. Document the combined approval command, the local revoke command, local-vs-remote lease behavior, trust boundary, and supported safe settings.

- [x] **Step 2: Update as-built architecture and changelog**

Describe the trusted actor boundary and both remote grant bits. Add one concise `Unreleased` entry without claiming that MQTT authenticates a Ground publisher.

- [x] **Step 3: Review changed documentation against the spec**

Verify every command and risk statement against the implemented CLI/config; do not describe a device test not run.

- [x] **Step 4: Commit the documentation slice**

Stage only `README.md`, `docs/CONTROL.md`, `docs/IMPLEMENTATION.md`, and `CHANGELOG.md`.
