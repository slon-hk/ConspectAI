"""Outbox dispatcher placeholder.

Durable outbox delivery is intentionally not enabled yet because the current
stage does not introduce an outbox table. This module marks the worker boundary
for the next migration step.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_outbox_dispatcher(*, poll_interval_seconds: float = 1.0) -> None:
    while True:
        logger.debug("Outbox dispatcher is not configured yet")
        await asyncio.sleep(poll_interval_seconds)
