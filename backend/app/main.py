import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


EMAIL_INVALID_MESSAGE = "Please enter a valid email address (e.g. name@example.com)."


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Normalizes Pydantic's default {"detail": [...]} validation-error shape
    # into this app's usual {"error": ..., "detail": ...} convention (see
    # app/errors.py), so the frontend's single error-parsing path handles
    # field validation (e.g. an invalid customer_name) the same way it
    # handles every other domain error, instead of needing a special case.
    first = exc.errors()[0]
    # customer_name's message is our own text (raised in schemas.py) and
    # already reads fine. customer_email's comes straight from the
    # email-validator library's internals ("value is not a valid email
    # address: The part after the @-sign is not valid...") — accurate, but
    # not something to show a user verbatim, so swap in our own wording.
    if first["loc"][-1] == "customer_email":
        msg = EMAIL_INVALID_MESSAGE
    else:
        msg = first["msg"].removeprefix("Value error, ")
    content = {"detail": {"error": "invalid_input", "detail": msg}}
    return JSONResponse(status_code=422, content=content)


@app.get("/health")
async def health():
    return {"status": "ok"}
