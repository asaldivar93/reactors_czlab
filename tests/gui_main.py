"""Entry module NiceGUI's test plugin imports to find the pages.

The ``user`` fixture needs a module that registers every ``@ui.page``
route, the way ``run_gui.py`` does at startup. Importing the page
modules here is the whole job: nothing is run, and no OPC connection is
opened, so importing this module stays free of side effects.
"""

from reactors_czlab.gui import pages  # noqa: F401  - registers the routes
