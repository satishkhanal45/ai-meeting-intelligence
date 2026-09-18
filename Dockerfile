# ── Stage 1: build the frontend ─────────────────────────────────────────
FROM node:20-alpine AS frontend

WORKDIR /build
# Copy manifests first so the dependency layer is cached independently of
# source changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ── Stage 2: Python dependencies ────────────────────────────────────────
FROM python:3.12-slim AS deps

WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .


# ── Stage 3: runtime ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# curl is needed by the healthcheck below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Run as an unprivileged user rather than root.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

COPY --chown=app:app src/ ./src/
COPY --chown=app:app meetings/ ./meetings/
# The built SPA is served by the API, so there is one origin and no CORS.
COPY --from=frontend --chown=app:app /build/dist ./static

# data/ is a mount point for the SQLite database and logs.
RUN mkdir -p /app/data && chown -R app:app /app/data
VOLUME ["/app/data"]

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/api/health" || exit 1

CMD ["uvicorn", "meeting_intelligence.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
