# SDD ledger — plan: docs/superpowers/plans/2026-10-06-t900-ppp-restart.md

Execution mode: Native, selected by user. This plan follows remote-control authorization because its actor and root-approval interfaces are prerequisites. Preserve unrelated worktree changes and stage only task files.

Ruling: The executor skill references `task-start`/`task-done`, but the available utility directory contains only `sdd-workspace`, `task-brief`, and `review-package`; the skill URI is not executable by the shell. Use the saved plan's exact task text as each brief, record baseline/test/commit evidence here, and manually run required checks — this preserves the stated TDD/review contract. Cost if wrong: less automated task-boundary bookkeeping.
Ruling: The plan's focused unittest command omitted the package's `src` and `vendor` import paths; `run.py` adds both. Updated both plans to set `PYTHONPATH=src:vendor` after observing `ModuleNotFoundError` for `rdf_node` and bundled `yaml`. Cost if wrong: focused-test invocation portability.

## Pre-flight interface table

| Producer → consumer | Interface checked | Finding |
|---|---|---|
| Remote Task 1 → PPP Task 1 | `allow_remote_control`, CLI approve path | Implement remote policy first; PPP approval must preserve it and add an independent default-false flag. |
| PPP Task 1 → PPP Task 2 | `allow_ppp_restart`, `control.ppp_restart_enabled`, exact RPC and CLI preflight | Matches the spec; command manager must supply the trusted internal origin. |
| Remote Task 2 → PPP Task 2 | Actor-preserving queue and helper RPC | Required dependency; PPP operation uses the same actor but its separate fixed-unit policy. |
| PPP Task 2 → PPP Task 3 | `ppp_restart` capability, topic, stage and Ground local confirmation API | Matches; UI sends no unit and no confirmation field over MQTT. |
| PPP Task 2 → PPP Task 4 | Exact command/topic and stage semantics | Docs must distinguish systemd acceptance from later same-session fresh health. |
| PPP Tasks 1–4 → PPP Task 5 | Approval CLI, UI outcome, trust boundary and device limitations | Combined docs/checksum/update step follows implementation and browser proof. |
| PPP Task 1 ↔ remote Task 1 | `helper.py`, `cli.py`, `test_core.py` | Shared files; remote grant/revoke is implemented first, then PPP approval is added. |
| PPP Task 2 ↔ remote Task 2 | `control.py`, `agent.py`, `test_system.py` | Shared files; preserve actor, remote and per-action gates. |
| PPP Task 3 ↔ remote Task 3 | Ground UI files | Shared files; sequentially extend the completed settings form. |
| PPP Tasks 4–5 ↔ remote Task 4 | Protocol/implementation/operator docs overlap | Sequential edits; update existing release note/checksum entries only. |

## Per-task self-consistency scan

| Task | Files / tests checked | Finding |
|---|---|---|
| 1 | helper, CLI, config, example config, core tests | Exact-unit approval and preflight fit existing helper boundary; all systemd calls must be mocked. |
| 2 | control, agent, Ground, journal, system tests | A distinct ID may proceed only after fresh health and local confirmation; older result remains unknown. |
| 3 | Ground HTML/JS/CSS | Existing API provides operations/latest and operation/result; actual Chromium smoke required. |
| 4 | PROTOCOL, MQTT_TOPIC_SUMMARY, IMPLEMENTATION | Consistent with Task 2 wire/result behavior and plaintext trust boundary. |
| 5 | README, CONTROL, OPERATIONS, CHANGELOG, SHA256SUMS | Correctly combines approval instructions and device-only acceptance limits. |

Remote-control Task 1 (`414047f`) and Task 2 (`c365888`) complete; PPP Task 1 may consume their root remote policy and actor-preserving helper path.
Task 1: complete (commit d205166; tests: `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p 'test_core.py'` → 114 tests, OK, 2 skipped).
Task 1 evidence: RED run failed on missing config key/runtime gate/helper RPC/parser option; GREEN run passed. Added invalid-shape, fixed-unit, loaded/active/job readiness, serialized concurrent request, separate local lease/remote approval, ambiguous action, config, and root CLI approval coverage.
Task 1 Ruling: Readiness parsing is a shared module-level `require_ppp_service_ready()` used by both CLI preflight and helper dispatch, rather than a Controller-only method — this keeps the CLI and runtime checks identical without duplicating systemctl parsing — cost if wrong: a future readiness policy change must retain this shared call path.

Task 2: complete (commit 848f5c0; tests: `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p 'test_system.py'` → 87 tests, OK).
Task 2 evidence: RED run reported missing `ppp_restart` capability, unsupported operation, missing nonterminal Ground ACK handling, and absent unresolved-operation confirmation. GREEN run passed with durable worker acceptance, same-ID no-replay after timeout, Ground stage-to-health reconciliation, session-change unknown state, fresh-health/confirmation gate, and MQTT flag stripping.
Task 2 Ruling: A prior `OUTCOME_UNKNOWN` remains unknown in the journal after explicit confirmation but carries `confirmed_by` and no longer gates later requests — the operator has explicitly acknowledged it, and a later applied operation must not require repeated confirmation for the same historical result — cost if wrong: a confirmed-but-still-relevant old side effect may need another operator confirmation.
Task 2 Ruling: Confirmation requires both currently fresh health and a health timestamp later than every unresolved request — recency alone does not prove the node returned after the uncertain restart — cost if wrong: clock skew between Ground and Edge can conservatively block a retry until clocks align or the operator resolves it locally.

Task 3: complete (commit 29ebb94, verified Chromium fixture).
Task 3 baseline: Ground Companion had no PPP restart control.
Task 3 smoke: current repository Ground API with a fake MQTT client; fixed topic/op, one offer per confirmed request, operation ID and stages shown, cancel sends nothing, duplicate click submits once, unresolved history survives reload without resubmission, and the local confirmation flag is absent from the MQTT payload. Simulated `PPP_RESTART_REQUESTED` followed by newer same-session health rendered `APPLIED` without claiming unit/link recovery.
Task 3 gates and accessibility: capability, MQTT readiness and stale health disable the button; keyboard traversal reaches the control; focus outline is visible; target height is 44 px; 375 px and 320 px widths had no horizontal overflow. Browser console/errors were empty after clearing.
Task 3 limit: fixture-only; no real systemd, PPP link or aircraft acceptance.
Task 3 Ruling: Persist unresolved PPP IDs in Ground-browser storage, poll only unresolved result IDs, and keep confirmed unknown outcomes visible; Ground journal remains authoritative and a missing browser history falls back to an explicit confirmation-required gate. This avoids automatic replay and unbounded result polling; cost if wrong: clearing browser storage can require one rejected discovery request before the UI can ask for the second confirmation.
UI design: preserve Ground navy/teal; a restrained amber panel distinguishes the MQTT-interrupting action from routine controls. Amber focus ring is 8.80:1 against its button background; button text is 12.96:1.

Task 4: complete (protocol/topic docs commit `cdd732a`; as-built control/PPP ownership text committed in `fdf41ce`). Reviewed topics, QoS 1/non-retained delivery, TTL, stage semantics, local-only confirmation stripping, anonymous/plaintext trust boundary, and full-down-link/device limits against the command mapping.
Task 5 docs: combined approval/revoke instructions, lease distinction, private-link risk, and read-only target-unit acceptance checklist are recorded in README, CONTROL, OPERATIONS, and CHANGELOG.
Final browser smoke: refreshed fixture health, passed the visible Ground PPP confirmation flow twice (prior unknown plus new action), and observed one fake offer to `sdr/v2/uav-01/cmd/service/ppp/restart` with QoS 1/15 s expiry. Confirmation flag and service name were absent from payload; old operation stayed `OUTCOME_UNKNOWN`, new journal entry was `REQUESTED`. No real systemd call.
Final verification: `python3 run.py selftest` and `bash scripts/check-package.sh` each passed 227 tests with 2 Linux-ping skips; package check also passed hashes, compile, shell, and Node syntax checks. No Raspberry/T900 acceptance performed.
