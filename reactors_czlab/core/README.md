## Pairing sensors to actuators

Every actuator starts unpaired and is refreshed by `actuator_loop` at 20 Hz
from its own controller. Calling the `<reactor>:set_pairing` OPC method with
a sensor id, an actuator id and a channel index moves that actuator into
`sampling_loop`, where it is driven from the paired sensor channel once per
sample period. `<reactor>:unpair` hands it back.

## Configuring actuators over OPC UA

Control configuration is one atomic OPC method call:
`<actuator>:apply_control_config`. It returns an accepted flag and the
server's validation message; `<actuator>:get_control_config` returns the
configuration that is actually running plus the server's enum options.

**The individual control variables (`method`, `output_unit`, gains, bounds,
times, and related fields) are read-only.** Generic OPC clients that used to
write those variables one at a time must call `apply_control_config()`
instead. This prevents the 20 Hz actuator loop from observing a partially
written configuration.

## Calibrating a pump

A pump's channel has a `Calibration` slot (`file = "R0_pwm0"`, etc.) that
converts between raw duty counts and mL/min. To fit one from the OPC client:

1. `calibrate_point(duty, seconds)` — run the pump at `duty` for `seconds`.
2. Measure the volume that actually came out, in mL.
3. `record_point(volume_ml)` — attach that measurement to the point just run.
4. Repeat steps 1-3 for at least four different duties spanning the range
   you intend to use.
5. `fit_calibration()` — fit both `flow = a*duty + b` and
   `flow = a*duty**b` with LMFit, then install the valid model with the
   lowest residual (linear wins an effective tie).

The fit persists a 95% prediction band. `max_duty` is the highest measured
integer duty whose lower bound is positive and whose band half-width is at
most 20% of predicted flow. The GUI plots the measurements, selected model,
band, and min/dispense/max duty markers. Legacy linear files remain readable
without a band until they are refitted.

Three more methods sit on the same actuator node:

- `clear_points()` — throw the collected points away and start the
  measurements over. The installed line is left alone.
- `set_duties(min_duty, dispense_duty)` — adjust the stall floor and the
  duty a non-PID volume dose is dispensed at, without refitting. Lowering
  `dispense_duty` is how you trade dosing speed for dose accuracy: a
  slower pump runs longer for the same mL, so the delivered volume is
  less sensitive to when the dose actually stops.
- `reload_calibration()` — re-read the stored file from disk, for after
  editing it by hand.

Each returns a status string, and each refuses a change that would leave
the pump unsafe to drive: the reason comes back in that string rather
than in the log.

Finite manual, timer, and boundary volume requests are silently capped to
what `dispense_duty` can deliver in one hour. PID volume output uses the
uncertainty-qualified `max_duty` and is capped just below what it can deliver
in one sampling period. Zero and negative volume demands stop the pump;
non-finite demands are rejected with the pump safely off. Relay autotuning
keeps its exact-dose preflight at `dispense_duty` and refuses an experiment
that cannot deliver the requested relay doses.

Calibrations are saved to `~/.reactors_czlab/calibrations/` as
`<name>.json`; the `REACTORS_CALIBRATION_DIR` environment variable
overrides that directory.
