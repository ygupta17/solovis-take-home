import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.pool import create_pool
from app.realtime.notify import run_listener
from app.routers import events, holds, waitlist, ws
from app.workers.sweeper import run_sweeper

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    stop_event = asyncio.Event()
    tasks = [
        asyncio.create_task(run_sweeper(app.state.pool, stop_event)),
        asyncio.create_task(run_listener(stop_event)),
    ]
    try:
        yield
    finally:
        stop_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.state.pool.close()


app = FastAPI(title="Seat Reservation Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local take-home; would be locked down per environment in prod
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events.router)
app.include_router(holds.router)
app.include_router(waitlist.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
