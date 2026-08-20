"""Per-channel PWM carrier frequency on the PLC actuator adapter."""

from __future__ import annotations

import pytest

from reactors_czlab.core import hardware
from reactors_czlab.core.data import Channel, PhysicalInfo, PlcOutput
from reactors_czlab.drivers import plc
from reactors_czlab.drivers.plc import PlcActuator, resolve_pwm_frequency
from reactors_czlab.server_info import ANALOG_ACTUATORS


class _FakeRpiplc:
    """Records the pin calls the adapter makes so a test can inspect them."""

    OUTPUT = "OUTPUT"

    def __init__(self) -> None:
        self.modes: list[tuple[str, str]] = []
        self.frequencies: list[tuple[str, int]] = []

    def pin_mode(self, pin: str, mode: str) -> None:
        self.modes.append((pin, mode))

    def analog_write_set_frequency(self, pin: str, frequency: int) -> None:
        self.frequencies.append((pin, frequency))


def _pwm_info(frequency: object) -> PhysicalInfo:
    """A one-channel PWM actuator config carrying ``frequency``."""
    return PhysicalInfo(
        model="pwm",
        address=0,
        type=PlcOutput.pwm,
        channels=[
            Channel("pwm0", "pwm", pin="Q2.7", pwm_frequency_hz=frequency),
        ],
    )


def test_every_configured_pwm_channel_is_explicitly_24_hz() -> None:
    """The whole installed fleet runs at the documented 24 Hz."""
    for reactor in ANALOG_ACTUATORS.values():
        for info in reactor.values():
            for channel in info.channels:
                assert channel.type is PlcOutput.pwm
                assert channel.pwm_frequency_hz == 24


def test_pwm_pin_is_initialized_with_its_own_frequency(monkeypatch) -> None:
    """The adapter installs each channel's frequency on its pin."""
    fake = _FakeRpiplc()
    monkeypatch.setattr(hardware, "IN_RASPBERRYPI", True)
    monkeypatch.setattr(hardware, "rpiplc", fake)

    PlcActuator("R0:pwm0", _pwm_info(24))

    assert fake.modes == [("Q2.7", "OUTPUT")]
    assert fake.frequencies == [("Q2.7", 24)]


def test_non_pwm_output_configures_no_pwm_frequency(monkeypatch) -> None:
    """An analog output sets its pin mode but never a PWM frequency."""
    fake = _FakeRpiplc()
    monkeypatch.setattr(hardware, "IN_RASPBERRYPI", True)
    monkeypatch.setattr(hardware, "rpiplc", fake)
    info = PhysicalInfo(
        model="mfc",
        address=0,
        type=PlcOutput.analog,
        channels=[Channel("lpm", "liters_per_minute", pin="A0.0", type=PlcOutput.analog)],
    )

    PlcActuator("R0:mfc", info)

    assert fake.modes == [("A0.0", "OUTPUT")]
    assert fake.frequencies == []


@pytest.mark.parametrize("frequency", [None, 0, -5, 24.0, True])
def test_invalid_pwm_frequency_fails_clearly(frequency: object) -> None:
    """A missing or non-positive-integer frequency is rejected at build time."""
    with pytest.raises(ValueError, match="pwm_frequency_hz"):
        resolve_pwm_frequency(_pwm_info(frequency).channels[0])
    with pytest.raises(ValueError, match="pwm_frequency_hz"):
        PlcActuator("R0:pwm0", _pwm_info(frequency))


def test_module_exposes_no_shared_frequency_constant() -> None:
    """Frequency is per channel now, not one module-wide default."""
    assert not hasattr(plc, "PWM_FREQUENCY_HZ")
