"""Headless event engine inspired by vn.py's ``vnpy.event`` package.

QIS keeps this small and dependency-free because its primary interface is a
local web application rather than vn.py's Qt desktop workstation.  The public
contract intentionally follows vn.py: typed events are placed on a queue and
dispatched to both event-specific and general handlers, with an optional timer.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
import logging
from queue import Queue
from threading import Event as ThreadEvent
from threading import RLock, Thread
from typing import Any


EVENT_TIMER = "eTimer"


@dataclass(frozen=True, slots=True)
class Event:
    """A message distributed by :class:`EventEngine`."""

    type: str
    data: Any = None
    source: str = ""


HandlerType = Callable[[Event], None]
ErrorHandlerType = Callable[[Exception, Event, HandlerType], None]

_STOP = object()


class EventEngine:
    """Thread-safe event dispatcher with an optional periodic timer.

    Handler exceptions are isolated so one application cannot terminate the
    platform event thread.  ``start`` and ``stop`` are idempotent and the
    engine may be restarted, which makes CLI commands and tests easy to own.
    """

    def __init__(
        self,
        interval: float = 1.0,
        error_handler: ErrorHandlerType | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("event interval must be positive")
        self._interval = float(interval)
        self._error_handler = error_handler
        self._queue: Queue[Event | object] = Queue()
        self._handlers: defaultdict[str, list[HandlerType]] = defaultdict(list)
        self._general_handlers: list[HandlerType] = []
        self._lock = RLock()
        self._stop_event = ThreadEvent()
        self._thread: Thread | None = None
        self._timer: Thread | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, *, timer: bool = True) -> None:
        """Start event dispatch and, by default, timer event generation."""

        with self._lock:
            if self._active:
                return
            self._active = True
            self._stop_event.clear()
            self._thread = Thread(
                target=self._run,
                daemon=True,
                name="qis-event-engine",
            )
            self._thread.start()
            if timer:
                self._timer = Thread(
                    target=self._run_timer,
                    daemon=True,
                    name="qis-event-timer",
                )
                self._timer.start()
            else:
                self._timer = None

    def stop(self) -> None:
        """Drain queued events and stop worker threads."""

        with self._lock:
            if not self._active:
                return
            self._active = False
            self._stop_event.set()
            self._queue.put(_STOP)
            thread = self._thread
            timer = self._timer
        if timer is not None:
            timer.join()
        if thread is not None:
            thread.join()
        with self._lock:
            self._thread = None
            self._timer = None

    def put(self, event: Event) -> None:
        """Queue an event for asynchronous distribution."""

        if not isinstance(event, Event):
            raise TypeError("event must be an Event instance")
        self._queue.put(event)

    def register(self, event_type: str, handler: HandlerType) -> None:
        """Register a handler once for a specific event type."""

        with self._lock:
            handlers = self._handlers[event_type]
            if handler not in handlers:
                handlers.append(handler)

    def unregister(self, event_type: str, handler: HandlerType) -> None:
        """Remove a previously registered event-specific handler."""

        with self._lock:
            handlers = self._handlers.get(event_type)
            if not handlers:
                return
            if handler in handlers:
                handlers.remove(handler)
            if not handlers:
                self._handlers.pop(event_type, None)

    def register_general(self, handler: HandlerType) -> None:
        """Register a handler that receives every event."""

        with self._lock:
            if handler not in self._general_handlers:
                self._general_handlers.append(handler)

    def unregister_general(self, handler: HandlerType) -> None:
        """Remove a previously registered general handler."""

        with self._lock:
            if handler in self._general_handlers:
                self._general_handlers.remove(handler)

    def process(self, event: Event) -> None:
        """Distribute one event synchronously.

        Production flows normally use :meth:`put`; this method is useful for
        deterministic bootstrap work and unit tests.
        """

        with self._lock:
            handlers = tuple(self._handlers.get(event.type, ()))
            general_handlers = tuple(self._general_handlers)
        for handler in handlers + general_handlers:
            try:
                handler(event)
            except Exception as exc:  # pragma: no branch - deliberate boundary
                self._handle_error(exc, event, handler)

    def _run(self) -> None:
        while True:
            queued = self._queue.get()
            try:
                if queued is _STOP:
                    return
                self.process(queued)  # type: ignore[arg-type]
            finally:
                self._queue.task_done()

    def _run_timer(self) -> None:
        while not self._stop_event.wait(self._interval):
            self.put(Event(EVENT_TIMER, source="EventEngine"))

    def _handle_error(
        self,
        exc: Exception,
        event: Event,
        handler: HandlerType,
    ) -> None:
        if self._error_handler is not None:
            self._error_handler(exc, event, handler)
            return
        logging.getLogger(__name__).exception(
            "QIS event handler failed: type=%s handler=%r",
            event.type,
            handler,
            exc_info=exc,
        )
