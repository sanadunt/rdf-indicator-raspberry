# SDD ledger — plan: docs/superpowers/plans/2026-10-06-remote-control-authorization.md

Execution mode: Native, selected by user. Work remains on `main` per the user's prior explicit instruction. Stage only task files; preserve unrelated modifications and untracked `docs/DEVELOPMENT_SUMMARY.md`.

Ruling: The executor skill references `task-start`/`task-done`, but the available utility directory contains only `sdd-workspace`, `task-brief`, and `review-package`; the skill URI is not executable by the shell. Use the saved plan's exact task text as each brief, record baseline/test/commit evidence here, and manually run required checks — this preserves the stated TDD/review contract. Cost if wrong: less automated task-boundary bookkeeping.
Ruling: The plan's focused unittest command omitted the package's `src` and `vendor` import paths; `run.py` adds both. Updated both plans to set `PYTHONPATH=src:vendor` after observing `ModuleNotFoundError` for `rdf_node` and bundled `yaml`. Cost if wrong: focused-test invocation portability.

## Pre-flight interface table

| Producer → consumer | Interface checked | Finding |
|---|---|---|
| Task 1 → Task 2 | Root `allow_remote_control` policy, helper status, CLI Edge flag | Matches; Task 2 must require both root and Edge grants. |
| Task 2 → Task 3 | Effective `remote_commands` capability; Ground settings command contract | Matches existing API shape; UI must preserve current revision and helper validation. |
| Tasks 1–3 → Task 4 | Persistent grants, internal actor, five safe setting fields and existing proof semantics | Docs can describe tested behavior; PPP-dependent command details belong to the dependent plan. |
| Remote Task 1 ↔ PPP Task 1 | `helper.py`, `cli.py`, `test_core.py` | Shared files; sequence remote grant first, then add PPP flag and tests without overwriting root-revoke behavior. |
| Remote Task 2 ↔ PPP Task 2 | `control.py`, `agent.py`, `test_system.py` | Shared command worker and capabilities; preserve actor queue contract, add PPP as a separate operation. |
| Remote Task 3 ↔ PPP Task 3 | `ground.html`, `ground.js`, `ground.css` | Shared UI surface; implement settings form first, then add PPP button without regressing settings. |
| Remote Task 4 ↔ PPP Tasks 4–5 | `README.md`, `CONTROL.md`, `IMPLEMENTATION.md`, `CHANGELOG.md` overlap | Sequence remote docs then PPP docs; later PPP docs amend as-built text and keep one accurate Unreleased entry. |

## Per-task self-consistency scan

| Task | Files / tests checked | Finding |
|---|---|---|
| 1 | `helper.py`, `cli.py`, `test_core.py` | Tests cover persisted grant/revoke and preserve local lease; consistent. |
| 2 | `control.py`, `agent.py`, `test_system.py` | Actor-loss and missing root capability are RED; unknown MQTT fields are already rejected and must remain rejected. |
| 3 | Ground HTML/JS/CSS and existing browser smoke | Uses existing command/snapshot contracts; actual UI smoke required. |
| 4 | README, CONTROL, IMPLEMENTATION, CHANGELOG | Documentation follows tested CLI and state contracts; no device claim. |

Task 1: RED observed — `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p test_core.py` → 106 tests, 2 failures, 1 error, 2 skipped. Failures showed helper rejected internal origin and status omitted the persistent root grant.
Task 1: GREEN — focused core suite → 106 tests, pass, 2 skipped; full `python3 run.py selftest` → 208 tests, pass, 2 skipped. No actual systemd action executed.
Task 1: complete (commit `414047f`; tests: `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p test_core.py` → 106 pass/2 skipped; `python3 run.py selftest` → 208 pass/2 skipped).
Task 2: RED observed — `PYTHONPATH=src:vendor python3 -m unittest discover -s tests -p test_system.py` → 80 tests, 2 failures, 1 error. Actor was lost at queue boundary; root grant was omitted from effective capability; unknown actor was accepted.
Task 2: An existing demo guard regression was caught by the suite after the range edit; restored the guard and reran `test_no_hardware_write_in_demo` (1 pass).
Task 2: GREEN — focused system suite → 80 tests pass; full `python3 run.py selftest` → 212 tests pass, 2 skipped.
Task 2: complete (base `414047f`, commit `c365888`; tests: focused `test_system.py` → 80 pass; full selftest → 212 pass/2 skipped).

Task 4: complete (commit `fdf41ce`). README, CONTROL, IMPLEMENTATION, and CHANGELOG document both persistent remote grant bits, action-level approvals, local lease behavior, private-link anonymous/plaintext trust limits, safe settings bounds, and device-only evidence limits. No MQTT publisher-authentication or hardware-test claim.
Final verification shared with dependent PPP plan: selftest and package check each passed 227 tests (2 Linux-ping skips); actual Ground browser smoke used a fake MQTT client and made no systemd call.
