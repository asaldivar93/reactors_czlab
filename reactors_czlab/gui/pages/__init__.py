"""Route registration.

Importing this package registers every ``@ui.page``. It is what
``run_gui.py`` and the test entry module both import; nothing here runs
on its own.
"""

from reactors_czlab.gui.pages import (  # noqa: F401 - registers the routes
    calibration,
    dashboard,
    experiments,
    plots,
)
