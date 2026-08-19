"""Tests for the shared dataclasses."""

from reactors_czlab.core.data import ControlConfig, ControlMethod, OutputUnit


def test_control_config_defaults_to_duty() -> None:
    """Existing callers get today's behaviour with no change."""
    config = ControlConfig(ControlMethod.manual, value=150)

    assert config.output_unit is OutputUnit.duty
