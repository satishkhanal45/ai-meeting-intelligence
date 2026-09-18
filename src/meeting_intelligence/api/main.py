from __future__ import annotations

import traceback
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from meeting_intelligence.api.routes import router
from meeting_intelligence.config import STATIC_DIR, settings
from meeting_intelligence.database import init_db
from meeting_intelligence.jobs import registry
from meeting_intelligence.logger import get_logger

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


# ── Static frontend ─────────────────────────────────────────────────────
#
# In the container the built SPA is copied to /app/static and served from the
# same origin as the API, so there is no CORS to configure and one port to
# expose. In development the Vite dev server serves it instead and this
# directory does not exist.

_STATIC_DIR = STATIC_DIR

if _STATIC_DIR.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> Response:
        """Serve a built asset, falling back to index.html for client routes.

        The SPA uses history-mode routing, so a deep link like /people is not a
        file on disk: it has to return index.html and let the router resolve it.
        """
        candidate = (_STATIC_DIR / full_path).resolve()
        # Reject anything that escapes the static directory.
        if _STATIC_DIR in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")

    logger.info("Serving built frontend", extra={"path": str(_STATIC_DIR)})
