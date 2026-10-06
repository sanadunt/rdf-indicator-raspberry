# Spec: Remote Control Authorization

## Objective

Allow a Ground client whose PUBLISH is accepted by the deployed broker/link to use the complete set of existing controlled Edge operations during flight without repeatedly opening a local maintenance lease. Ground scope includes `stream.set`, the five safe `config.patch` fields, SDR lifecycle, reboot, shutdown, and the dependent PPP restart. A root operator on the Raspberry grants persistent remote authority before flight. The local Edge GUI continues to require its existing boot-bound 30–900 second lease for lifecycle, reboot, and shutdown; settings and stream controls keep their current local policy.

The application does not authenticate an MQTT publisher by its claimed role or identity. The deployed broker/network is the command trust boundary. If that boundary permits anonymous publishers, any reachable publisher can issue enabled remote commands.

This changes persistent authorization, not the maintenance lease. Ground cannot open, extend, or close that lease. The user accepts plaintext and anonymous MQTT on an isolated private link; the package must not claim that private-link membership provides cryptographic protection.

The Ground settings form exposes the five existing safe fields only: center frequency, VFO0 frequency, bandwidth, gain, and squelch. The current helper requires center frequency and VFO0 frequency to be changed together to the same value, so the UI presents one coordinated frequency control that submits both fields. It uses the existing helper allowlist, range checks, approved gain table, base revision, and runtime proof behavior; it does not write arbitrary native settings JSON.

## Tech Stack

- Python 3.10+, standard library, bundled pure-Python PyYAML.
- MQTT 5 client and existing v2 command envelopes; no new dependency or wire version.
- Ground panel: static HTML/CSS/JavaScript.
- Tests: `unittest`, isolated temporary state, and loopback MQTT fixture.

## Commands

From `rdf-node/`:

```bash
python3 run.py selftest
bash scripts/check-package.sh
python3 run.py demo --port 18790
```

Target-device approval remains an explicit root action; enabling remote capability does not open a maintenance lease:
```bash
sudo rdf-node controls approve --settings --lifecycle --reboot --shutdown --remote --ppp-restart
```

`--remote` authorizes the existing `stream.set` command and permits other remote operations only when their separate capabilities are enabled. The settings and lifecycle prompts still require their existing single-writer and audited-unit confirmations. `--ppp-restart` is added by the dependent PPP module and locally checks the exact service. The grant persists across process/OS restarts. Revoke it locally with `sudo rdf-node controls approve` without `--remote`; confirm `APPROVE`. This disables remote writes while preserving separately approved local capabilities.

## Project Structure

- `src/rdf_node/cli.py`: root approval and revocation state.
- `src/rdf_node/helper.py`: root policy and remote-origin maintenance decision.
- `src/rdf_node/control.py`: preserve trusted actor through the serialized worker and into helper RPC.
- `src/rdf_node/agent.py`: advertise effective remote and per-action capabilities.
- `web/ground.html`, `web/ground.js`, `web/ground.css`: expose all currently supported safe SDR settings and existing remote controls.
- `tests/test_core.py`, `tests/test_system.py`: helper policy, actor separation, command authorization, and operation regressions.
- `docs/CONTROL.md`, `docs/IMPLEMENTATION.md`, `docs/PROTOCOL.md`, `docs/MQTT_TOPIC_SUMMARY.md`, `docs/OPERATIONS.md`, `README.md`, `CHANGELOG.md`: as-built policy, operator instructions, and protocol details.

## Code Style

Keep origin internal to the Edge call path; never accept it from an MQTT payload field. Follow the current enum-like actor labels and fixed helper argument pattern:

```python
origin = {'ground-controller': 'ground-controller', 'local-admin': 'local-admin'}[actor]
result = self._helper({'op': op, 'origin': origin})
```

The helper must validate origin and consult root-owned policy; local lifecycle/reboot/shutdown requests retain the lease requirement. Local settings and stream controls keep their current policy.

## Testing Strategy

- Extend `tests/test_core.py`: remote-authorized Ground origin can perform an approved lifecycle/power action without a lease; local origin still gets `MAINTENANCE_REQUIRED`; remote is rejected when root approval is off; reboot/shutdown challenge and action capability checks remain enforced.
- Remote command tests must include `stream.set`, which is gated by the persistent remote authorization even though it has no helper-side per-action flag; the separately approved config, SDR lifecycle, reboot, shutdown, and PPP actions must still require their own capabilities.
- Extend `tests/test_system.py`: verified actor reaches helper through the real serialized worker; `stream.set`, remote setting changes, deadline, session/boot, revision, helper availability, remote revoke, and per-action capability rejection remain effective.
- Test every allowed settings field and representative invalid/range/table cases using real `validate_changes`; do not assert source text or incidental UI defaults.
- Test root CLI grant and revocation persisted to `/etc/rdf-node/helper.yaml` and config, then reload a new helper/controller from those files; a helper/OS process restart must preserve both enabled and revoked states.
- Exercise the Ground panel in a browser: fields are keyboard/touch operable, confirmation is visible, disabled capability states are clear, focus is visible, and narrow viewports do not overflow.
- Run `python3 run.py selftest`, then `bash scripts/check-package.sh`; use a safe live Ground/Edge loopback smoke path without invoking real systemd, reboot, or shutdown.

## Boundaries

- Always: root-local explicit remote approval; retain per-action local capability approvals; preserve MQTT command validation, bounded deadlines, session/boot identity, config revision, durable idempotency journal, reboot/shutdown prepare-execute challenge, helper allowlists, and all setting field/value checks. Local Edge GUI lifecycle/reboot/shutdown remains lease-gated.
- Always: remote authorization does not imply broker receipt, DAQ health, applied SDR settings, host reboot completion, or Ground connectivity.
- Ask first: changing the safe settings allowlist or value ranges; making local UI operations lease-free; adding operations outside the current controlled command set.
- Never: remote lease-open/extend/close; shell execution; caller-selected systemd unit/path; arbitrary settings JSON; automatic retry of an uncertain side effect.
- Deployment risk accepted by the user: MQTT may be plaintext and anonymous on an isolated private link. Plaintext exposes credentials and commands; anonymous access gives every reachable broker publisher the same command authority. Documentation must describe this trust boundary and must not claim end-to-end authentication.

## Success Criteria

1. Root-approved remote lifecycle, reboot, shutdown, settings, stream, and PPP commands work without an active local maintenance lease. Local Edge lifecycle/reboot/shutdown still fail without the lease; local settings/stream behavior remains unchanged.
2. Remote writes, including `stream.set`, remain disabled unless both Edge config and root helper policy allow them; `sudo rdf-node controls approve` without `--remote` revokes remote authority without removing local per-action approvals.
3. Existing lifecycle, reboot, and shutdown capability checks and reboot/shutdown challenges remain unchanged in effect.
4. The Ground panel exposes each of the five supported setting fields, submits only valid field/value updates with current revision, and renders validation/runtime proof distinctly.
5. MQTT transport security is not silently asserted or newly required; documentation matches the user's private-link plaintext choice and existing transport behavior.
6. Existing tests and package check pass; no real systemd lifecycle, reboot, shutdown, or radio action is run by tests.

## Open Questions

- Device acceptance must confirm the actual broker/link is isolated as the user states; repository tests cannot establish that deployment fact.
- The exact `t900-ppp.service` unit and reconnect behavior are verified in the dependent `t900-ppp-restart` module on the Raspberry, not by this spec.