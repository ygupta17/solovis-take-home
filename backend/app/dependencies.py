import asyncpg
from fastapi import Header, HTTPException, Request


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def session_token(x_session_token: str = Header(..., alias="X-Session-Token")) -> str:
    """Opaque client-generated identity (a UUID the browser mints and stores
    in localStorage). There is no account system in this project — see
    DECISIONS.md for why that's a deliberate scope cut, not an oversight.
    """
    if not x_session_token.strip():
        raise HTTPException(400, "X-Session-Token header must be non-empty")
    return x_session_token
