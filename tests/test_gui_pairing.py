"""Tests for the pairing panel.

read_pairings and STATE.call are stubbed: what is under test is that the
panel renders the published table, passes the ChannelIndex value rather
than a channel position, and refuses what the server would refuse.

The panel's initial row list is populated by the async ``read_pairings``
fetch that ``pairing_panel`` (an async ``@ui.refreshable``) awaits
directly, so every test waits for it with ``should_see`` before
interacting - ``user.find`` itself does not retry.

The fake ``call``/``read_pairings`` pair below is stateful - ``call``
mutates a shared table and ``read_pairings`` returns it - so a test that
sees a row appear or disappear is pinning that the panel actually
re-reads the server's table after a mutation, not just updating its own
local copy.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from reactors_czlab.gui.components import pairing


class _Recorded(list):
    """The ``(method, args)`` log, plus a handle on the shared table.

    Subclassing ``list`` rather than returning a tuple keeps every
    existing ``assert (...) in calls`` in this file working unchanged;
    the ``table`` attribute is extra, for tests that need to simulate
    another OPC client mutating the published table directly.
    """

    table: list[dict]


@pytest.fixture
def calls(gui_state, monkeypatch: pytest.MonkeyPatch) -> list:
    """Record OPC method calls; report one existing pairing.

    ``call`` and ``read_pairings`` share one table, mimicking the real
    server: a successful ``set_pairing``/``unpair`` mutates it before
    ``call`` returns, and ``read_pairings`` always reflects the current
    state of that table, never a stale snapshot. A test that asserts a
    row appeared or vanished after a mutation is therefore only genuine
    if the panel re-reads the table rather than trusting its own
    in-place bookkeeping.
    """
    recorded = _Recorded()
    table = [
        {"sensor": "R0:ph", "actuator": "R0:pwm0", "channel": 0},
    ]
    recorded.table = table

    async def call(reactor, owner, method, *args):
        recorded.append((method, args))
        if method == "set_pairing":
            sid, aid, channel = args
            table.append(
                {"sensor": sid, "actuator": aid, "channel": channel},
            )
        elif method == "unpair":
            sid, aid, channel = args
            table[:] = [
                row
                for row in table
                if not (
                    row["sensor"] == sid
                    and row["actuator"] == aid
                    and row["channel"] == channel
                )
            ]
        return True

    async def read_pairings(reactor):
        return list(table)

    async def read_channel_indices(reactor, sensor):
        return {"pH": 0, "oC": 1}

    def reading(reactor, name, channel):
        return (0.0, None)

    monkeypatch.setattr(gui_state, "call", call)
    monkeypatch.setattr(gui_state, "reading", reading)
    monkeypatch.setattr(pairing, "read_pairings", read_pairings)
    monkeypatch.setattr(
        pairing,
        "read_channel_indices",
        read_channel_indices,
    )
    return recorded


def _select(user: User, label: str) -> ui.select:
    """The one ``ui.select`` carrying this label.

    The panel's own "ph"/"oC"/"pwm1" option text also appears elsewhere
    on the dashboard (sensor and actuator card titles and channel
    labels), and a ``ui.select`` only exposes its option text to content
    matching while its popup is showing (see
    ``ElementFilter.__iter__``). Simulating a click through that popup
    would therefore just as likely hit the unrelated same-text label
    instead, since ``UserInteraction.click`` picks whichever matching
    element has the lowest id, and the sensor and actuator panels render
    - and so get their ids - before this one does. Driving the selects
    directly by their own unambiguous "Sensor"/"Channel"/"Actuator"
    label and setting ``.value`` is what the brief's escape hatch allows
    when a ``ui.select`` proves hard to drive through the ``user``
    fixture; it exercises the same ``on_value_change`` path a real click
    would.
    """
    (element,) = user.find(kind=ui.select, content=label).elements
    return element


def _click_button(user: User, text: str) -> None:
    """Click the one button whose visible text is exactly ``text``.

    ``find``'s content matching is substring-based, so ``find("Pair")``
    also matches the "Pairings" section heading (dashboard.py) and the
    "Unpair" button - both contain "Pair" - and whichever of those
    happens to have the lower element id wins under ``find(...).click()``,
    not the actual Pair button. Matching the button's own ``.text``
    exactly finds the right one.
    """
    (element,) = [
        e for e in user.find(kind=ui.button).elements if e.text == text
    ]
    UserInteraction(user, {element}, text).click()


async def _wait_for(
    condition: Callable[[], bool],
    *,
    timeout: float = 1.0,
) -> None:
    """Poll a condition driven by a background task's side effect."""
    elapsed = 0.0
    step = 0.02
    while not condition():
        await asyncio.sleep(step)
        elapsed += step
        if elapsed > timeout:
            error_message = "condition not met in time"
            raise AssertionError(error_message)


async def test_the_published_table_is_rendered(
    user: User,
    calls: list,
) -> None:
    """Regression: pairings were server-side Python state only, so no
    client could show what was paired or recover after a restart.
    """
    await user.open("/reactor/R0")
    await user.should_see("R0:ph ch0 -> R0:pwm0")


async def test_unpair_calls_the_method_with_the_published_row(
    user: User,
    calls: list,
) -> None:
    """The row carries exactly the arguments unpair needs."""
    await user.open("/reactor/R0")
    await user.should_see("R0:ph ch0 -> R0:pwm0")
    user.find("Unpair").click()
    await user.should_see("Nothing paired")

    assert ("unpair", ("R0:ph", "R0:pwm0", 0)) in calls


async def test_an_already_paired_actuator_is_not_offered(
    user: User,
    calls: list,
) -> None:
    """Pre-validating what the server checks means a False is a bug.

    pwm0 is paired in the fixture, so only pwm1 may be selected.
    """
    await user.open("/reactor/R0")
    # Wait for the initial reload before reading the select's options -
    # otherwise this would pass vacuously by checking before the async
    # fetch has populated anything.
    await user.should_see("R0:ph ch0 -> R0:pwm0")

    actuator_select = _select(user, "Actuator")
    assert actuator_select.options == ["pwm1"]


async def test_pairing_sends_the_channel_index_not_its_position(
    user: User,
    calls: list,
) -> None:
    """Regression: set_pairing takes an index and OPC gives names, so
    the panel reads the ChannelIndex property rather than trusting
    browse order, which asyncua does not guarantee.
    """
    await user.open("/reactor/R0")
    # Wait for the initial reload: it is what populates the Actuator
    # select's options (pwm1 only, since pwm0 is already paired).
    await user.should_see("R0:ph ch0 -> R0:pwm0")

    sensor_select = _select(user, "Sensor")
    channel_select = _select(user, "Channel")
    actuator_select = _select(user, "Actuator")

    sensor_select.value = "ph"
    # on_sensor runs as a background task off the value-change event,
    # not synchronously with the assignment above.
    await _wait_for(lambda: "oC" in (channel_select.options or []))
    channel_select.value = "oC"
    actuator_select.value = "pwm1"

    _click_button(user, "Pair")
    await user.should_see("follows")

    assert ("set_pairing", ("R0:ph", "R0:pwm1", 1)) in calls


async def test_a_successful_pair_reconciles_with_the_published_table(
    user: User,
    calls: list,
) -> None:
    """Regression: an update-in-place panel only shows the row it added
    itself, missing a pairing another OPC client made in between - it
    never re-reads the published table after its own mutation succeeds.
    A panel that reconciles on success sees both rows.
    """
    await user.open("/reactor/R0")
    await user.should_see("R0:ph ch0 -> R0:pwm0")

    sensor_select = _select(user, "Sensor")
    channel_select = _select(user, "Channel")
    actuator_select = _select(user, "Actuator")

    sensor_select.value = "ph"
    await _wait_for(lambda: "oC" in (channel_select.options or []))
    channel_select.value = "oC"
    actuator_select.value = "pwm1"

    # Another OPC client pairs behind this panel's back, between its
    # initial read and its own mutation below.
    calls.table.append(
        {"sensor": "R0:ph", "actuator": "R0:pwm2", "channel": 0},
    )

    _click_button(user, "Pair")
    await user.should_see("follows")

    await user.should_see("R0:ph ch0 -> R0:pwm2")
