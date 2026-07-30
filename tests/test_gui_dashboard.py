"""Tests for the dashboard pages, driven by NiceGUI's `user` fixture.

No browser: the fixture renders the page's element tree in process. See
pyproject's addopts and main_file for the wiring.
"""

from __future__ import annotations

from nicegui.testing import User


async def test_the_index_page_renders(user: User) -> None:
    """The app serves, and the routes are registered.

    A failure here means the test plumbing is wrong - main_file, the
    user_plugin addopts entry, or the pages package import - not that a
    page is wrong.
    """
    await user.open("/")
    await user.should_see("Bioreactors")
