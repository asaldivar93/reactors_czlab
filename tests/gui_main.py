"""Entry module NiceGUI's test plugin imports to find the pages.

The ``user`` fixture needs a module that registers every ``@ui.page``
and then calls ``ui.run()``, the way ``run_gui.py`` does. The plugin
intercepts that call, so no server is started and no port is bound -
importing this module opens no OPC connection and touches no database.
"""

from nicegui import ui

from reactors_czlab.gui import pages  # noqa: F401  - registers the routes

ui.run()
