"""Synchronous in-process bus used by the local test suite.

The staging environment uses a managed broker with at-least-once delivery. This
local stand-in delivers each published event exactly once so tests stay
deterministic and need no network or broker process.
"""

from services.common.events import Event


class InMemoryBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self.published: list[Event] = []

    def subscribe(self, topic: str, handler) -> None:
        self._handlers.setdefault(topic, []).append(handler)

    def publish(self, event: Event) -> None:
        self.published.append(event)
        for handler in list(self._handlers.get(event.topic, [])):
            handler(event)
