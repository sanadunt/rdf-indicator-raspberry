# Spec: T900 PPP Service Restart

## Objective

Give the Ground operator a confirmed button to request restart of the Raspberry-side `t900-ppp.service` over the existing MQTT control channel. The service is outside RDF Node's existing SDR engine ownership; implement a separate fixed-unit helper operation, not an `engine_service` alias or arbitrary systemd endpoint.

PPP restart may drop the MQTT channel that carries the request. The Edge journal must record the command before the helper side effect. The helper rejects another restart while a prior systemd job is active or status cannot be verified; the readiness check and invocation run under one helper lock. A successful helper response produces the nonterminal stage `PPP_RESTART_REQUESTED`, which means only that systemd accepted the fixed restart request. After the same session returns with health timestamp later than the request, Ground may mark the operation `APPLIED` with separate proof of request acceptance plus fresh node health. It never claims the PPP unit itself was independently verified as recovered. An unacknowledged operation stays unresolved and is never replayed automatically. A new operator request after an unresolved result requires fresh node/health, a second explicit confirmation, and helper proof the fixed unit is loaded/active with no pending job; the earlier journal entry remains `OUTCOME_UNKNOWN`. The button cannot repair a fully down PPP path because no command can reach the Pi over that path.

## Tech Stack

- Python 3.10+, standard library, bundled pure-Python PyYAML.
- Existing MQTT v2 `CommandManager`, Ground API, SQLite operation journal, and restricted root Unix helper.
- Static Ground HTML/CSS/JavaScript; no new dependency.
- `unittest` with mocked systemd calls and loopback MQTT fixture.

## Commands

From `rdf-node/`:

```bash
python3 run.py selftest
bash scripts/check-package.sh
```

Local root approval before flight, in addition to the existing selected capability approvals:

```bash
sudo rdf-node controls approve --settings --lifecycle --reboot --shutdown --remote --ppp-restart
```

The CLI approval verifies on the Raspberry that the exact fixed unit is loaded and active and requires typed confirmation. The helper runs its bounded `systemctl show` readiness check and `systemctl --no-block restart` while holding the existing `Controller.lock`; those calls form one serialized transaction, so another helper request cannot race the readiness result. Before each restart, require `LoadState=loaded`, `ActiveState=active`, and no pending job. Missing or unknown status rejects before invocation. The helper RPC schema is exactly `{'op', 'origin'}` for this operation; first require a JSON object, then require `op == 'ppp.restart'`, the exact key set, and `origin` in the two allowed values. It must not install, rewrite, or take ownership of the PPP unit.

## Project Structure

- `src/rdf_node/control.py`: new operation `ppp.restart`, topic mapping, command/capability checks, journaled execution.
- `src/rdf_node/helper.py`: root policy `allow_ppp_restart`, exact unit constant, constrained restart RPC.
- `src/rdf_node/cli.py`: local `--ppp-restart` approval and unit preflight.
- `src/rdf_node/config.py`, `config/example.yaml`, `src/rdf_node/agent.py`: disabled-by-default capability configuration and advertisement.
- `src/rdf_node/ground.py`: consume local-only explicit confirmation for a distinct new command after an unresolved PPP result, then strip it before publishing the MQTT envelope.
- `web/ground.html`, `web/ground.js`, `web/ground.css`: Ground-only restart button, confirmation, capability/freshness state, and truthful operation result.
- `tests/test_core.py`, `tests/test_system.py`: helper allowlist, exact fixed action, remote gate, journal and Ground acknowledgement behavior.
- `docs/CONTROL.md`, `docs/IMPLEMENTATION.md`, `docs/PROTOCOL.md`, `docs/MQTT_TOPIC_SUMMARY.md`, `docs/OPERATIONS.md`, `README.md`, `CHANGELOG.md`: operation and target-device limits.

The Ground HTTP intent may carry a local-only `confirm_previous_unknown: true` after the operator confirms the prior unresolved restart and the Pi is fresh. `Ground.submit_command()` consumes and removes that field before constructing the MQTT v2 envelope; Edge never accepts it as an MQTT field. Ground rejects a second PPP restart intent without this confirmation while an earlier request is unresolved. On confirmation, Ground preserves or updates the earlier record to `OUTCOME_UNKNOWN` and submits a new operation ID; it never replays the old ID or automatically clears the old outcome.

## Code Style

Use a fixed RPC with no unit argument; never interpolate command payload data into a systemd argument:

```python
if not isinstance(req, dict) or req.get('op') != 'ppp.restart':
    raise HelperError('INVALID_RPC_ARGUMENT')
if set(req) != {'op', 'origin'} or req['origin'] not in ('ground-controller', 'local-admin'):
    raise HelperError('INVALID_RPC_ARGUMENT')
if not self.policy['allow_ppp_restart']:
    raise HelperError('PPP_RESTART_NOT_APPROVED')
if req['origin'] == 'ground-controller':
    if not self.policy['allow_remote_control']:
        raise HelperError('REMOTE_CONTROL_NOT_APPROVED')
elif not self.maintenance():
    raise HelperError('MAINTENANCE_REQUIRED')
self._require_ppp_service_ready()  # bounded status query under Controller.lock
try:
    self._run(['/usr/bin/systemctl', '--no-block', 'restart', 't900-ppp.service'])
except (OSError, TimeoutError, subprocess.TimeoutExpired, HelperError) as error:
    raise TimeoutError('SYSTEMD_ACTION_OUTCOME_UNKNOWN') from error
return {'requested': True, 'service': 't900-ppp.service'}
```

The Edge worker stores `PPP_RESTART_REQUESTED` after helper success and does not mark `APPLIED`. Ground accepts that ACK stage, then marks the operation `APPLIED` only after newer fresh health in the same session; result proof states that systemd accepted the request and Ground later received fresh node health. If no ACK arrives, health stays stale, or the Edge restarts before it can report, Ground/Edge leave the operation unresolved and mark it `OUTCOME_UNKNOWN` when the operator explicitly requests a new action or the Edge journal recovers. The existing helper `_run` bounds systemctl calls to 10 seconds and the RPC is bounded; any ambiguous error after invocation starts maps to `OUTCOME_UNKNOWN`, not a definite failure. Do not return a `link_recovered` boolean.

The existing engine-unit validation must continue to reject `t900-ppp.service` as an SDR lifecycle target.

`dispatch()` holds the existing `Controller.lock` while it validates readiness and invokes the fixed systemd action. `_run()` uses its existing bounded timeout; a helper/RPC timeout after invocation starts leaves the journal `OUTCOME_UNKNOWN`. Do not return a `link_recovered` boolean: only later independent Ground health/MQTT state can show whether the node returned.

## Testing Strategy

- `tests/test_core.py`: denied by default; denied for non-object, wrong op, arbitrary/missing RPC fields or service unit; denied without root PPP approval; authorized request invokes exactly `/usr/bin/systemctl --no-block restart t900-ppp.service`; a prior active job or unverifiable unit state blocks a second request; two concurrent helper requests cannot pass readiness and invoke restart simultaneously; maintenance bypass is only for Ground origin with remote policy enabled; local origin remains lease-gated.
- `tests/test_system.py`: operation is mapped only to `cmd/service/ppp/restart`, rejected while remote capability is off or node/health stale, stored durably before helper call, duplicate same-ID delivery does not call systemd twice, a different ID is rejected while restart status is pending/unknown, and helper success produces `PPP_RESTART_REQUESTED`, not `APPLIED`.
- Ground tests: accept only matching ID/session ACKs; promote `PPP_RESTART_REQUESTED` to `APPLIED` only after newer fresh-health in that session; preserve unknown across missing ACK, stale health, or session change; reject a second intent without the local-only confirmation; after confirmation keep the earlier record unknown and send a new ID. Map timeout/disconnect or any ambiguous failure after systemd invocation to `OUTCOME_UNKNOWN`. Preflight rejection before invocation stays definite.
- Tests and package checks never execute real `systemctl restart`, stop PPP, reboot, or shutdown.
- Raspberry acceptance must separately verify the installed unit name/state, systemd restart behavior, time to PPP/MQTT recovery, and Ground-side fresh-health return. Repository fixtures do not prove those device facts.

## Boundaries

- Always: fixed target exactly `t900-ppp.service`; disabled by default; explicit local root approval; require existing broad remote authorization and MQTT command gates; accept only the existing v2 ID/session/boot/deadline/revision contract; journal before side effect; duplicate IDs are idempotent; status-unknown/pending systemd jobs block another restart; never replay automatically.
- Always: UI says systemd acceptance is not proof of PPP/MQTT recovery; show current connection/health independently. If prior outcome is unresolved, Ground requires fresh health and explicit operator confirmation before a new command; the helper separately verifies the fixed unit is active with no pending job. The earlier journal entry stays unknown.
- Ask first: changing the fixed service name, installing/modifying PPP systemd units, adding automatic recovery/retry, or adding new transport/control paths.
- Never: arbitrary unit from Ground, shell command, `systemctl enable/disable`, changing PPP configuration, rewriting or stopping PPP outside the explicit restart request, or claiming completion from a broker ACK.
- Device facts remain unknown until checked on the Raspberry; the package contains no as-built reference or validation of `t900-ppp.service`.

## Success Criteria

1. No MQTT payload can choose a service name or command; malformed/non-object RPCs and wrong operation names are denied.
2. PPP restart is disabled unless both root helper policy and Edge config capability are approved, and Ground also has remote authorization enabled.
3. A valid Ground request is durable before the restart action; duplicate same-ID delivery does not call systemd twice; helper preflight is serialized and blocks concurrent/pending requests.
4. The Ground operation is `PPP_RESTART_REQUESTED` after systemd acceptance and becomes `APPLIED` only after later fresh health in the same session. It never claims unit-level recovery from the helper response alone.
5. If the path drops before acknowledgement/health, the result stays unresolved/unknown. A later distinct request requires fresh health, explicit confirmation, and a ready-unit check; no automatic replay occurs.
6. Unit and package tests pass without invoking real PPP/systemd; target Raspberry acceptance remains explicitly outstanding.

## Open Questions

- The installed Raspberry must confirm that `t900-ppp.service` is loaded/active and safe to restart. The codebase does not establish this.
- The recovery time and whether the same MQTT broker route returns after restart can only be measured on the actual radio/PPP deployment.
