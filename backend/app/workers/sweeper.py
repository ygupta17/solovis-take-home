"""Periodic hold-expiry sweep. Pure liveness/UX — see app/db/protocol.py's
module docstring for why correctness never depends on this running, or on
its timing. Safe to run from every app instance concurrently; each hold's
release is guarded by `WHERE status = 'ACTIVE'` so redundant sweeps are
no-ops, not races.
"""

import asyncio
import logging

import asyncpg

from app.config import settings
from app.db.protocol import sweep_expired_holds

logger = logging.getLogger(__name__)


async def run_sweeper(pool: asyncpg.Pool, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            swept = await sweep_expired_holds(pool)
            if swept:
                logger.info("sweeper released %d expired hold(s)", swept)
        except Exception:
            logger.exception("sweeper cycle failed, will retry next interval")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.sweep_interval_seconds)
        except TimeoutError:
            pass
