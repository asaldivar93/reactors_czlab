# Goal
the core/calibration.py file might benefit from extracting it into its own subpackage reactors_czlab.core.calibration. Please evaluate if this change is beneficial for codebase architecture, readability and maintenace. If so make a plan for it, If not let me know the reasons why. In this file you'll find a suggestion for the architecture

## Proposed architecture

core/
    calibration/
        __init__.py
        model.py
        fitting.py
        storage.py
        run.py
