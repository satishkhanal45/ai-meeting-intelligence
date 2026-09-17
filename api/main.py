from __future__ import annotations

import traceback
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from api.routes import router
from config import settings
from database import init_db
from jobs import registry
from logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prepare the database on startup rather than as an import side effect."""
    init_db()
    yield
    # Cancel in-flight jobs so the process can exit without orphaned tasks.
    await registry.shutdown()


app = FastAPI(
    title="AI Meeting Intelligence API",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the failure in full, return an opaque message plus a correlation id.

    The exception text can contain file paths, SQL and raw provider responses,
    so it is never sent to the client.
    """
    error_id = uuid.uuid4().hex[:12]
    logger.error(
        "Unhandled exception",
        extra={
            "error_id": error_id,
            "path": str(request.url),
            "method": request.method,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error. Quote the error id when reporting this.",
            "error_id": error_id,
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Meeting payloads embed full transcripts, which compress well.
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(router)
