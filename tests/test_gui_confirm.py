"""Tests for the action in-flight lock."""

from __future__ import annotations

from reactors_czlab.gui.components import confirm as confirm_module


class _Control:
    """A button-shaped object recording lock state."""

    def __init__(self) -> None:
        self.enabled = True
        self.loading = False

    def disable(self) -> None:
        """Disable the fake button."""
        self.enabled = False

    def enable(self) -> None:
        """Enable the fake button."""
        self.enabled = True

    def props(
        self,
        add: str | None = None,
        *,
        remove: str | None = None,
    ) -> None:
        """Track the Quasar loading prop."""
        if add == "loading":
            self.loading = True
        if remove == "loading":
            self.loading = False


def test_in_flight_blocks_double_activation(monkeypatch) -> None:
    """The trigger stays blocked until the outstanding call returns."""
    control = _Control()
    monkeypatch.setattr(confirm_module.STATE, "client", object())
    monkeypatch.setattr(confirm_module.STATE, "book", object())
    monkeypatch.setattr(
        type(confirm_module.STATE),
        "connected",
        property(lambda self: True),
    )

    with confirm_module.in_flight(control):
        assert control.enabled is False
        assert control.loading is True

    assert control.enabled is True
    assert control.loading is False
