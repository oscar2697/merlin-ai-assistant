"""Lightweight pub/sub event queue used to notify GUI/CLI of node activity."""
from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MerlinEvent:
    type: str    # "status" | "user_input" | "merlin_response" | "error" | "info"
    data: Any


_event_queue: "queue.Queue[MerlinEvent]" = queue.Queue()
_subscribers: list = []


def post_event(type_: str, data: Any = "") -> None:
    _event_queue.put(MerlinEvent(type=type_, data=data))


def next_event(timeout: Optional[float] = None) -> Optional[MerlinEvent]:
    try:
        return _event_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def drain_events() -> list:
    events = []
    while True:
        try:
            events.append(_event_queue.get_nowait())
        except queue.Empty:
            return events
