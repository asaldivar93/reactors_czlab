"""Tests for the reactor dashboard pages.

No browser: NiceGUI's `user` fixture renders the element tree in
process. STATE is stubbed rather than connected - what is under test is
what the page does with readings, not the OPC client.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from nicegui.testing import User

from reactors_czlab.core.data import ERROR_VALUE

READINGS = {
    ("R0", "ph", "pH"): 7.25,
    ("R0", "ph", "oC"): ERROR_VALUE,
    ("R0", "pwm0", "curr_value"): 1500.0,
    ("R0", "pwm0", "total_volume"): 12.5,
    ("R0", "pwm0", "cal_a"): 0.01,
    ("R0", "pwm0", "cal_b"): 0.0,
    ("R0", "pwm0", "cal_r2"): 1.0,
}


async def test_the_index_page_renders(user: User) -> None:
    """The app serves, and the routes are registered.

    A failure here means the test plumbing is wrong - main_file, the
    user_plugin addopts entry, or the pages package import - not that a
    page is wrong.
    """
    await user.open("/")
    await user.should_see("Bioreactors")


@pytest.fixture
def connected(gui_state, monkeypatch: pytest.MonkeyPatch) -> None:
    """gui_state, plus a fixed set of readings."""

    def reading(reactor: str, name: str, channel: str) -> tuple:
        return (
            READINGS.get((reactor, name, channel)),
            datetime.now(),  # noqa: DTZ005 - client stores naive timestamps
        )

    monkeypatch.setattr(gui_state, "reading", reading)


async def test_the_index_lists_every_reactor(
    user: User,
    connected: None,
) -> None:
    """The index is how an operator reaches a reactor."""
    await user.open("/")
    await user.should_see("R0")
    await user.should_see("R1")


async def test_a_reading_is_shown_with_its_units(
    user: User,
    connected: None,
) -> None:
    """Sensor channel names are their units, per the browse contract."""
    await user.open("/reactor/R0")
    await user.should_see("7.250 pH")


async def test_the_error_sentinel_never_reaches_the_screen(
    user: User,
    connected: None,
) -> None:
    """Regression: -0.111 read as a temperature is a live probe failure
    that looks like a plausible measurement. The dashboard must say the
    read failed instead.
    """
    await user.open("/reactor/R0")
    await user.should_see("read failed")
    await user.should_not_see("-0.111")


async def test_the_actuator_card_shows_output_and_delivered_volume(
    user: User,
    connected: None,
) -> None:
    """Both archived series are on the card."""
    await user.open("/reactor/R0")
    await user.should_see("1500.000")
    await user.should_see("12.500 mL")


async def test_a_disconnected_app_says_so(user: User) -> None:
    """No connection must not render an empty dashboard.

    The `connected` fixture is deliberately not requested here.
    """
    await user.open("/")
    await user.should_see("Connecting")
