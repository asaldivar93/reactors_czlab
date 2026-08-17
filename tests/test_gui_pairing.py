"""Tests for the short-name / full-id boundary in the pairing panel.

The address book keys devices by the middle part of their browse name
(``R0:biomass:415`` -> ``biomass``) while the server's methods and its
published pairing table use full ids (``R0:biomass``). Every place the
two meet is covered here, because getting it wrong fails quietly: the
server refuses with a message only its own log sees.
"""

from __future__ import annotations

import pytest

from reactors_czlab.gui.components.pairing import (
    device_id,
    paired_actuators,
    short_name,
)


class TestDeviceId:
    """Turning a book key into what the server's methods want."""

    def test_builds_the_full_id(self) -> None:
        """Regression: the form sent "biomass", not "R0:biomass".

        set_pairing validates against sampling.sensors, which holds full
        ids, so the short name was refused with "biomass is not a sensor
        of R0" - logged server-side, returned to the client as a bare
        False. Found by clicking Pair against a live server.
        """
        assert device_id("R0", "biomass") == "R0:biomass"
        assert device_id("R2", "pwm3") == "R2:pwm3"


class TestShortName:
    """Turning a published full id back into a book key."""

    def test_strips_the_reactor(self) -> None:
        """What the address book and the selectors are keyed by."""
        assert short_name("R0:biomass") == "biomass"
        assert short_name("R0:pwm0") == "pwm0"

    def test_a_bare_name_is_left_alone(self) -> None:
        """Idempotent, so applying it twice cannot mangle a name."""
        assert short_name("pwm0") == "pwm0"

    def test_round_trips_with_device_id(self) -> None:
        """The two are inverses for every id the server publishes."""
        for reactor in ("R0", "R1", "R2"):
            for name in ("ph", "do", "biomass", "pwm0", "mfc"):
                full = device_id(reactor, name)
                assert short_name(full) == name


class TestPairedActuators:
    """Which actuators the add form may still offer."""

    def test_returns_short_names(self) -> None:
        """Regression: full ids were compared against book keys.

        paired_actuators returned {"R0:pwm0"} while the actuator list
        held {"pwm0"}, so the difference never removed anything and an
        already-paired pump stayed on offer. set_pairing then refused
        it, which reads to an operator as the button being broken.
        """
        rows = [
            {"sensor": "R0:ph", "actuator": "R0:pwm0", "channel": 0},
            {"sensor": "R0:do", "actuator": "R0:pwm1", "channel": 0},
        ]
        assert paired_actuators(rows) == {"pwm0", "pwm1"}

    def test_filters_the_offered_actuators(self) -> None:
        """The set difference the add form takes actually removes."""
        rows = [{"sensor": "R0:ph", "actuator": "R0:pwm0", "channel": 0}]
        available = ["pwm0", "pwm1", "pwm2"]

        free = [a for a in available if a not in paired_actuators(rows)]

        assert free == ["pwm1", "pwm2"]

    def test_nothing_paired_offers_everything(self) -> None:
        """The starting state."""
        assert paired_actuators([]) == set()


@pytest.mark.parametrize(
    ("reactor", "name"),
    [("R0", "ph"), ("R1", "do"), ("R2", "biomass")],
)
def test_the_pair_call_uses_full_ids(reactor: str, name: str) -> None:
    """Both arguments set_pairing validates are full ids."""
    assert device_id(reactor, name).startswith(f"{reactor}:")
    assert ":" in device_id(reactor, name)
