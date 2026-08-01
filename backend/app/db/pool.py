import asyncpg

from app.config import settings


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=20,
        # Waiting on a *connection* has its own bounded queue, separate from
        # the in-Postgres row-lock wait queue. See DECISIONS.md.
        max_inactive_connection_lifetime=300,
    )
