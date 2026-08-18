# GUI-Driven pH PID Autotuning

## Summary

Implement relay-feedback PID autotuning as a server-owned, non-blocking workflow operated from a new NiceGUI page. Operators select the pH
sensor and acid/base pumps, enter chemistry and safety parameters, monitor the live experiment, review candidate gains, and explicitly apply atuning rule.

## Public Interfaces

- Add reactors_czlab.core.ph_model as the production source for Chemistry, PlantParams, PhPlant, buffering intensity, and related model
functions currently under scripts/pid-autotuning.

- Add reactors_czlab.core.autotune with RelayTuneConfig, AutotuneRun, run-state/result dataclasses, relay identification, tuning rules, SIMC, code-gain conversion, and gain scaling.

- Expose these OPC methods as methods on every eligible actuator node; the node receiving autotune_start is the selected base pump:
  - autotune_preflight(...)
  - autotune_start(sensor_id, acid_id, setpoint, base_bolus_ml, acid_bolus_ml, hysteresis_ph, max_minutes, phosphate_molar, base_molar,
    acid_molar, dose_budget_ml, acknowledge_other_loops, acknowledge_budget_override)
  - autotune_status()
  - autotune_abort()
  - autotune_apply(rule)
  - autotune_scale_to_setpoint(target_ph)
  - autotune_reapply_last()

- Return versioned JSON strings containing at least ok, message, phase, and relevant data. This preserves the OPC string contract while giving the GUI structured state.

- Add /reactor/{reactor}/autotune and an “PID autotuning” reactor tab.

## Stage 1: Package the Scientific Model

- Add NumPy and SciPy to base project dependencies and update the lockfile, making the same packaged model available to server, client, GUI, and tests.
- Move reusable chemistry, plant, relay-identification, tuning-rule, gain-scaling, and closed-loop simulation code out of scripts/pid-autotuning into package modules. Keep scripts as thin study/report runners that import package APIs; do not retain duplicate implementations.
- Keep core/__init__.py and the package root docstring-only.
- Use the validated scaling direction beta(target) / beta(tuned).
- Use 0.30 mL for the deterministic Ku=18.6, Pu=293 s acceptance fixture. Retain 0.20 mL as the initial live-run default.
- Add analytic tests for chemistry, identify_ku_pu, all tuning rules, gain conversion, SIMC, and scaling.
- Run targeted tests and Ruff before committing.
- Commit: feat: package the pH autotuning model

## Stage 2: Implement the Safe Core Workflow

- Implement one AutotuneRun per reactor with phases idle, baseline, adapting, settling, collecting, identified, aborted, and failed.
- Advance the state machine from real reactor samples rather than a long OPC call. Store monotonic timestamps, pH, signed requested volume, switch times, actual delivered dose, and cycle summaries.
- Require the selected pumps to:
  - Belong to the reactor and be paired to the selected sensor’s single pH channel.
  - Use PID control with volume output and the same setpoint.
  - Use backwards=False for base and backwards=True for acid.
  - Have fitted calibrations passing installable_reason().

- Measure noise during a no-dose baseline lasting at least 60 seconds and six samples. Estimate sigma robustly after linear detrending, then reject hysteresis below 2*sigma.
- Derive the default combined dose budget from reactor volume, phosphate chemistry, the effective safe-band endpoints, and separate titrant molarities. Permit an operator override only with the explicit override acknowledgement.
- Use safe_low=max(4.0, setpoint-1.0) and safe_high=min(10.0, setpoint+1.0). Abort after two consecutive out-of-band samples; abort immediately on ERROR_VALUE, non-finite input, timeout, configuration loss, pairing loss, or dose exhaustion.
- Require acknowledgement that the pH excursion may affect other loops.
- Validate each bolus against the volume deliverable between one 20 Hz actuator tick and one live control period. Warn, but do not reject solely because control_period > 30 s.
- Begin with the operator-entered boluses. After each initial complete cycle, if amplitude is below 3*h, scale both boluses proportionally by at most 2x, preserve their ratio, reset settling data, and remain within deliverability and safety budgets. Fail rather than identify if adequate amplitude cannot be reached.
- Discard two transient cycles, then require four clean cycles. Reject non-finite cycles, amplitude not exceeding both hysteresis and 3*sigma, period variation above 25%, or base/acid half-cycle asymmetry outside [0.2, 5].
- Add actuator-owned demand/tick APIs so the autotune can use the existing volume Dispenser while calibrating=True; normal PID decisions remain interlocked. Always stop both pumps, restore old_value=0, bank actual volume, and clear both interlocks on every terminal path.
- Prevent CalibrationRun from stealing an actuator already owned by an autotune.
- Test normal identification, adaptive sizing, every automatic abort, operator abort, exception cleanup, actual-dose accounting, and one-active-run-per-reactor behavior.
- Commit: feat: implement safe pH autotune runs

## Stage 3: Add Audit Persistence

- Store a versioned document at calibration_dir() / "<reactor>_ph_autotune.json" using the same atomic temp-file replacement pattern as pump
calibration.
- Record every run that passed preflight and started, including selections, chemistry, effective limits, baseline sigma, adjusted boluses,
switch/cycle summary, Ku/Pu when available, actual dose, timestamps, terminal state, and abort/failure reason.
- Record separate apply and scale events and maintain latest_applied with pump identities, tuning rule, tuned/reference pH, chemistry, and
gains.
- Calculate repeated scaling from the recorded gain reference pH so calls do not compound incorrectly. Require the target to match both
controllers’ current shared setpoint.
- Reapply only after revalidating identities, pairing, PID/volume configuration, directions, and calibrations.
- Treat missing, malformed, non-finite, or unsupported-version files as operator-visible refusals, never server failures.
- Add round-trip, atomic-write, malformed-file, history, non-compounding scaling, and reapply tests.
- Commit: feat: persist PID autotune audit history

## Stage 4: Integrate Reactor and OPC UA

- Give Reactor an optional active autotune coordinator. Feed it the selected pH sample immediately after sensor reads; continue driving
unrelated pairings normally.
- Let actuator_loop() advance only the deliveries owned by an active tune while the selected actuators remain interlocked.
- Reject pairing changes involving the active pair. Abort if selected controller configuration or required pairing changes through another client.
- Initialize autotune methods after ReactorOpc has the complete actuator-node mapping so one base-owned method can safely update and publish both actuator nodes.
- Apply gains only after explicit operator confirmation. Acquire both actuator configuration locks in stable ID order, validate both complete candidates first, change only kp/ki/kd, preserve PID runtime state, publish both read-backs, and roll back on an unexpected partial failure.
- Keep start non-blocking. Status must remain readable after GUI navigation or reconnect and include bounded live trace data, phase, pH, relay direction, elapsed time, cycles, adjusted boluses, dose use, safety limits, and candidate gains.
- Add stub-node OPC tests for argument declarations, selection validation, status JSON, abort, atomic dual apply, rejection without
identification, scaling, reapply, and unchanged OPC browse naming.
- Commit: feat: expose pH autotuning over OPC UA

## Stage 5: Build the Operator GUI

- Add a pure GUI controller that maps status/preflight JSON to form and run-view states, with unit tests independent of NiceGUI.
- Build the page once and refresh only labels, metrics, and Plotly trace data. Do not rebuild buttons or inputs inside the polling timer.
- Let operators select the pH sensor, base pump, and acid pump each run. Show the required directions prominently: “Base pump: backwards=False” and “Acid pump: backwards=True.”

- Prefill 0.20 mL boluses, 0.02 pH hysteresis, 30 minutes, 14 mM phosphate, and 0.5 M acid/base. Display calibration timestamps and all server preflight refusals verbatim.
- Show the chemistry-computed dose budget; permit an override only with a separate acknowledgement. Require the other-loop acknowledgement and a final start confirmation summarizing pumps, pH limits, time, and dose.
- While running, poll status and show the pH trace with setpoint, hysteresis, and safety bands plus relay direction, current/adjusted boluses, sigma, cycles, dose, elapsed time, and Abort.
- On success, show Ku/Pu and gains for TL-PI, ZN-PID, TL-PID, and SIMC. Default the selector to TL-PI, warn when derivative action is chosen, and require confirmation before Apply.
- Provide explicit “Scale to current setpoint” and “Reapply last tune” actions with confirmation and server read-back.
- On page reopening or GUI reconnect, reconstruct the active view from server status without starting another run.
- Add route smoke tests for connected, disconnected, idle, running, failed, and identified states plus interaction tests for validation,
acknowledgements, abort, apply, scale, and reapply.
- Update navigation and README screen listings.
- Commit: feat: add the pH PID autotuning GUI

## Stage 6: End-to-End Validation

- Run AutotuneRun against the packaged PhPlant using the 5L, 14 mM phosphate, 0.5 M titrant, 0.30 mL relay, 0.02 pH hysteresis, 10s sampling, 10s dead time, 0.005 pH noise, seed 0 fixture.
- Require Ku within 15% of 18.6 mL/pH, Pu within 10% of 293 s, and TL-PI gains within 15% of kp=5.83, ki=0.0090, kd=0.
- Reproduce disturbance rejection, pH 5.8 scaling, sample-period portability at 5/20/40 seconds, and the undersized 2-second fixed-flow
rejection.
- Add regressions for GUI disconnect during a run, server-side continuation, paired/unpaired loop behavior, pump-calibration mutual exclusion, sample_ready behavior, and unchanged archival subscriptions.
- Run the complete suite with uv run pytest and uv run ruff check ..
- Commit: test: validate pH autotuning end to end

## Stage 7: Operator Documentation

- Document prerequisites, pump pairing/configuration, reagent roles, chemistry fields, adaptive boluses, safety limits, acknowledgements, live phases, reviewing/applying rules, scaling, reapplying, abort behavior, audit-file location, and recovery after GUI disconnect.
- State that TL-PI is the default, applying is never automatic, and temperature autotuning remains out of scope.
- Re-run the full test and lint suite after documentation changes.
- Commit: docs: document pH PID autotuning

## Subagent Execution and Review

Use one persistent lead agent as coordinator and independent reviewer, with one implementation subagent per stage. Execute stages strictly in order;
do not launch stage implementation agents in parallel. Stages 2, 4, and 6 share reactor-loop, actuator-ownership, cleanup, and OPC invariants, so
the lead must preserve those decisions across handoffs and include the accepted interfaces from earlier stages in each later assignment.

### Suggested Assignments

| Stage | Model | Reasoning effort | Rationale |
| --- | --- | --- | --- |
| 1. Scientific model | `gpt-5.6-sol` | `medium` | Numerical correctness, API extraction, deterministic fixtures, and removal of duplicate implementations |
| 2. Safe core workflow | `gpt-5.6-sol` | `high` | Asynchronous state machine, safety invariants, dispenser ownership, cleanup, and extensive failure paths |
| 3. Audit persistence | `gpt-5.6-terra` | `high` | Atomic writes, schema versioning, malformed input handling, audit history, and non-compounding scaling |
| 4. Reactor and OPC UA | `gpt-5.6-sol` | `high` | Cross-loop integration, stable lock ordering, atomic dual-controller updates, reconnect behavior, and naming contracts |
| 5. Operator GUI | `gpt-5.6-sol` | `high` | NiceGUI lifecycle and event behavior, polling, reconnect reconstruction, confirmations, and interaction tests |
| 6. End-to-end validation | `gpt-5.6-sol` | `high` | Adversarial cross-stage verification, numerical acceptance bounds, regression coverage, and failure diagnosis across layers |
| 7. Documentation | `gpt-5.6-terra` | `medium` | Accurate synthesis of the completed behavior and operator workflow |

If an assigned model or effort is unavailable, do not silently substitute a weaker configuration. Report the substitution, use the strongest available
coding model with the nearest supported effort, and preserve the same acceptance and review gates.

### Lead-Agent Procedure

For each stage, the lead agent must:

1. Inspect `git status` and record unrelated pre-existing changes before launching the stage agent. Never ask a subagent to revert, overwrite, stage,
   or commit those changes.
2. Give the stage agent a bounded assignment containing this plan, the repository `AGENTS.md` constraints, the exact stage scope, inherited public
   interfaces, permitted files, acceptance tests, and the required commit message. Require applicable repository skills; in particular, the Stage 5
   agent must use the `pythonista-nicegui` skill if it is available.
3. Keep only that implementation agent active for repository edits. All agents share one worktree, so do not start the next stage or another editing
   agent until review, corrections, validation, and commit are complete.
4. Require the implementation agent to inspect existing code and tests before editing, implement the whole stage, add the specified regressions, run
   targeted tests and Ruff, and return a concise summary of changed files, commands run, results, assumptions, and remaining risks. The implementation
   agent must not stage or commit; the lead owns the review and commit gate.
5. Review the actual worktree diff independently rather than accepting the agent's summary. Check the stage against every plan bullet and relevant
   `AGENTS.md` invariant, inspect tests for meaningful failure coverage, and look specifically for dependency-boundary violations, duplicated scientific
   logic, unsafe terminal paths, incorrect actuator ownership, OPC naming or subscription changes, config/runtime-state clobbering, and GUI lifecycle
   regressions as applicable.
6. Run the targeted tests and Ruff checks independently. For Stages 2, 4, and 6, also run the relevant existing reactor, dispenser, pairing, OPC,
   calibration, and `sample_ready` regressions even if the stage agent reports them passing.
7. Send concrete review findings back to the same stage agent for correction, then re-review the resulting diff and rerun affected checks. Repeat until
   no material finding remains. If the stage uncovers an earlier prerequisite defect, fix and test it within the current stage as required by this plan.
8. Confirm the diff contains only stage-owned changes, make the listed stage commit, verify the commit and clean/expected worktree state, and only then
   launch the next stage agent.

After Stage 7, the lead must inspect the cumulative commit series and final diff, run the complete test and Ruff suites, and report the exact results and
any environmental skips or limitations. A subagent's passing report is evidence for review, not a substitute for the lead's own verification.

## Commit Discipline

- After each stage, run that stage’s targeted tests and Ruff checks, inspect the diff, and commit only files belonging to that stage.
- Do not stage or revert unrelated pre-existing worktree changes.
- Do not begin the next stage until the current stage’s commit is complete.
- Keep each listed commit independently testable; if a stage uncovers a prerequisite defect, fix and test it within that stage rather than
leaving a broken intermediate commit.

## Assumptions

- Pump selection is per run; no persistent reagent-role metadata is added.
- Pumps must be paired and correctly configured before tuning; autotuning changes gains only.
- A calibration is current when it is fitted and installable; age alone does not reject it.
- NumPy and SciPy are supported production dependencies.
- Started runs, including aborts and failures, are audited; preflight rejections are logged but not persisted.
- The GUI reviews results before applying them; no automatic gain installation occurs.
- Gain scaling uses beta(target) / beta(tuned).
- Temperature autotuning, automatic setpoint retuning, authentication, and database schema changes are out of scope.
