"""Entry module for NiceGUI's `user` fixture.

The fixture imports this as ``__mp_main__`` to discover the routes, and
requires it to call ``ui.run()`` - which the fixture patches, so nothing
is served. It is separate from ``run_gui.py`` because ``cli()`` parses
``sys.argv``, and under pytest that argv belongs to pytest.
"""

from nicegui import ui

from reactors_czlab.gui import pages  # noqa: F401 - registers the routes

if __name__ in {"__main__", "__mp_main__"}:
    ui.run()
