# Stage 1: Build Tailwind CSS
FROM node:24-slim AS node-builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY core/ ./core/
RUN npm run build:css

# Stage 2: Final Python application image
FROM python:3.14-slim AS final

# Install Astral UV binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install runtime system dependencies:
# - curl: for health checks & fetching database from R2
# - zstd: for decompressing zstd-compressed databases
# - ca-certificates: for secure TLS connections
# - sqlite3: for database inspection/WAL management
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        curl \
        ca-certificates \
        zstd \
        sqlite3 && \
    rm -rf /var/lib/apt/lists/*

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Copy dependency specifications and project metadata for caching
COPY pyproject.toml uv.lock README.md ./

# Install project dependencies without dev packages
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source code
COPY archetypes/ ./archetypes/
COPY config/ ./config/
COPY core/ ./core/
COPY docker-entrypoint.sh ./

# Copy compiled Tailwind CSS from node-builder
COPY --from=node-builder /app/core/static/css/protocol.css ./core/static/css/protocol.css

# Install project package into venv
RUN uv sync --frozen --no-dev

# Collect static assets into STATIC_ROOT using WhiteNoise
RUN DJANGO_SETTINGS_MODULE=config.settings.prod uv run modometa collectstatic --no-input

# Create data directory and non-root user
RUN mkdir -p /app/data && \
    useradd -u 1000 -U -d /app -s /bin/bash appuser && \
    chown -R appuser:appuser /app && \
    chmod +x /app/docker-entrypoint.sh

USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
