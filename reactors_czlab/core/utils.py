"""Define the DictList class."""

from __future__ import annotations

import logging
from time import perf_counter

_logger = logging.getLogger("server.utils")


class Timer:
    """A simple timer.

    Parameters
    ----------
    interval: float
        The time interval required for the timer to activate
    last_time: float
        The time value since the timer was last active

    """

    def __init__(self, interval: float) -> None:
        """Initialize the timer."""
        self.interval = interval
        self.last_time = perf_counter()
        self._subscribers = []

    @property
    def interval(self) -> float:
        """Get the time interval."""
        return self._interval

    @interval.setter
    def interval(self, interval: float) -> None:
        """Set the time interval and reset the timer."""
        if not isinstance(interval, float | int):
            raise TypeError
        self.last_time = perf_counter()
        self._interval = interval
        _logger.debug(f"New interval: {self.interval}")

    def add_suscriber(self, subscriber: object) -> None:
        self._subscribers.append(subscriber)

    def remove_suscriber(self, subscriber: object) -> None:
        # I'm pretty sure there is a bug here
        # How does the method remove() find the object?
        # Does it use the special method __eq__?
        self._subscribers.remove(subscriber)

    def is_elapsed(self) -> None:
        """Evaluate if the elapsed time is higher than the interval."""
        this_time = perf_counter()
        self.elapsed_time = this_time - self.last_time
        _logger.debug(
            f"elapsed_time: {self.elapsed_time}, interval: {self.interval}"
        )
        print(f"elapsed_time: {self.elapsed_time}, interval: {self.interval}")
        if self.elapsed_time > self.interval:
            self.last_time = this_time
            for subscriber in self._subscribers:
                subscriber.on_timer_callback()
