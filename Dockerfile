# Multi-stage build for smaller final image
FROM python:3.12-slim AS builder

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev extras in production)
RUN uv sync --frozen --no-dev

# --- Production stage ---
FROM python:3.12-slim

# System dependencies for Piper TTS
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash narrator

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY --chown=narrator:narrator . .

# Create data directory for SQLite
RUN mkdir -p /app/data && chown narrator:narrator /app/data

# Switch to non-root user
USER narrator

# Ensure venv is in PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Railway injects PORT env var
ENV PORT=8000
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:${PORT}/health').raise_for_status()"

# Start server - use shell form to expand $PORT
CMD python -m src.main serve --host 0.0.0.0 --port $PORT
