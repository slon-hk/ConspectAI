"""Shared event bus instance for the current process."""

from __future__ import annotations

from .bus import InProcessEventBus

event_bus = InProcessEventBus()

