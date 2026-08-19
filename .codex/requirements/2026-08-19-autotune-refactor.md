# Goal
The core subpackage has grown to large with mixed responsibilities. This is one step in a multi step extraction process. The goal of this step is to extract the autotune features into its own subpackage.

## Instructions
In this file you'll find a proposed architecture. You'll use this architecture as a reference. Evaluate the core subpackage make any modifications you think are necessary, or come up with your own architecture.
 
## Proposed Architecture
reactors_czlab/
├── core/
│   ├── __init__.py
│   ├── data.py
│   ├── control.py
│   ├── dispenser.py
│   ├── actuator.py
│   ├── sensor.py
│   ├── reactor.py
│   ├── calibration.py
│   ├── modbus.py
│   ├── hamilton.py
│   └── hardware.py
│
└── autotune/
    ├── __init__.py
    ├── relay.py
    ├── runtime.py
    ├── audit.py
    ├── model.py
    └── simulation.py
    
### Proposed Responsibilities

autotune/relay.py
    RelayTuneConfig
    RelayController
    RelayIdentification
    identify_ku_pu()
    tuning_rules()
    simc_pid()
    to_code_gains()
    from_code_gains()
    scale_gains()
    scale_gains_to_setpoint()

autotune/runtime.py
    AutotuneContext
    AutotunePhase
    AutotuneSample
    CycleSummary
    AutotuneResult
    AutotuneStatus
    AutotunePreflight
    AutotuneRun
    AutotuneCoordinator
    validate_autotune_selection()
    robust_noise_sigma()
    cycle_quality_reason()
    period_quality_reason()
    default_dose_budget_ml()

autotune/audit.py
    AutotuneAudit
    AuditOutcome
    GainCandidate
    ...

autotune/model.py
    Chemistry
    PlantParams
    PhPlant
    state_from_ph()
    ph_from_state()
    buffering_intensity()
    ...

autotune/simulation.py
    Pump
    SimPid
    SplitRangeConfig
    SplitRangeController
    SimulationResult
    run_relay_experiment()
    simulate()
    simulation_metrics()
    settling_time()
    
## Notes
- Consider extracting the application storage to:
	reactors_czlab/
    	paths.py

```python
def data_dir() -> Path: ...
def calibration_dir() -> Path: ...
def autotune_dir() -> Path: ...
```

- Consider this recomendation:

"After moving it, I'd go one small step further and have core.reactor type against a tiny Protocol rather than against the concrete autotune package. Something along these lines conceptually:

```python
class AutotuneRunLike(Protocol):
    is_active: bool
    sensor_id: str
    base_id: str
    acid_id: str

    def sample(self, ph: float) -> None: ...
    def tick(self) -> None: ...
    def abort(self, reason: str) -> object: ...
```

Then core doesn't need to know that the implementation is called AutotuneRun at all. That's a nice architectural rule:

autotune may know about core; core should know only the tiny interface necessary to host an autotune."

