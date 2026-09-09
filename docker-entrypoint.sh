#!/bin/sh
set -e

DATABASE_PATH="${DATABASE_PATH:-/app/modometa.db}"
KNN_INDEX_PATH="${KNN_INDEX_PATH:-/app/data/knn_index.npz}"
DOWNLOAD_URL="${R2_PUBLIC_URL:-${DATA_DOWNLOAD_URL:-https://data.modometa.com}}"

# If arguments were passed to docker run (e.g. bash, custom command), execute them directly
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Ensure directories exist
mkdir -p "$(dirname "$DATABASE_PATH")"
mkdir -p "$(dirname "$KNN_INDEX_PATH")"

if [ -n "$DOWNLOAD_URL" ]; then
    # Strip trailing slash if present
    DOWNLOAD_URL="${DOWNLOAD_URL%/}"

    # Fetch SQLite database if not present locally
    if [ ! -f "$DATABASE_PATH" ]; then
        echo "[entrypoint] Database not found at $DATABASE_PATH. Attempting download from $DOWNLOAD_URL..."

        # Check for zstd compressed database first
        if curl -fsSL -I "${DOWNLOAD_URL}/modometa.db.zst" >/dev/null 2>&1; then
            echo "[entrypoint] Downloading and decompressing modometa.db.zst..."
            if curl -fSL --progress-bar -o "${DATABASE_PATH}.zst" "${DOWNLOAD_URL}/modometa.db.zst"; then
                zstd -d --rm -f "${DATABASE_PATH}.zst" -o "$DATABASE_PATH"
                echo "[entrypoint] Database decompressed successfully."
            else
                echo "[entrypoint] Warning: Failed to download modometa.db.zst"
                rm -f "${DATABASE_PATH}.zst"
            fi
        elif curl -fsSL -I "${DOWNLOAD_URL}/modometa.db" >/dev/null 2>&1; then
            echo "[entrypoint] Downloading uncompressed modometa.db..."
            if curl -fSL --progress-bar -o "$DATABASE_PATH" "${DOWNLOAD_URL}/modometa.db"; then
                echo "[entrypoint] Database downloaded successfully."
            else
                echo "[entrypoint] Warning: Failed to download modometa.db"
                rm -f "$DATABASE_PATH"
            fi
        else
            echo "[entrypoint] Database file not found at $DOWNLOAD_URL"
        fi
    else
        echo "[entrypoint] Database found at $DATABASE_PATH."
    fi

    # Fetch kNN index if not present locally
    if [ ! -f "$KNN_INDEX_PATH" ]; then
        echo "[entrypoint] kNN index not found at $KNN_INDEX_PATH. Checking remote..."
        if curl -fsSL -I "${DOWNLOAD_URL}/knn_index.npz" >/dev/null 2>&1; then
            echo "[entrypoint] Downloading knn_index.npz..."
            curl -fSL --progress-bar -o "$KNN_INDEX_PATH" "${DOWNLOAD_URL}/knn_index.npz" || rm -f "$KNN_INDEX_PATH"
        elif curl -fsSL -I "${DOWNLOAD_URL}/knn_index.npz.zst" >/dev/null 2>&1; then
            echo "[entrypoint] Downloading and decompressing knn_index.npz.zst..."
            if curl -fSL --progress-bar -o "${KNN_INDEX_PATH}.zst" "${DOWNLOAD_URL}/knn_index.npz.zst"; then
                zstd -d --rm -f "${KNN_INDEX_PATH}.zst" -o "$KNN_INDEX_PATH"
            else
                rm -f "${KNN_INDEX_PATH}.zst"
            fi
        else
            echo "[entrypoint] Notice: No remote kNN index found. Deck similarity queries will fall back to rule-based matches."
        fi
    else
        echo "[entrypoint] kNN index found at $KNN_INDEX_PATH."
    fi
fi

# Fallback: if database still does not exist, run initial migrations so app can start
if [ ! -f "$DATABASE_PATH" ]; then
    echo "[entrypoint] Warning: No database found at $DATABASE_PATH. Running migrations to initialize an empty database..."
    modometa migrate --no-input
fi

PORT="${PORT:-8000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-1}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-60}"
GUNICORN_MAX_REQUESTS="${GUNICORN_MAX_REQUESTS:-1000}"
GUNICORN_MAX_REQUESTS_JITTER="${GUNICORN_MAX_REQUESTS_JITTER:-100}"

echo "[entrypoint] Starting Gunicorn on port $PORT (workers=$GUNICORN_WORKERS, threads=$GUNICORN_THREADS, max_requests=$GUNICORN_MAX_REQUESTS)..."

exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "${GUNICORN_WORKERS}" \
    --threads "${GUNICORN_THREADS}" \
    --timeout "${GUNICORN_TIMEOUT}" \
    --max-requests "${GUNICORN_MAX_REQUESTS}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER}" \
    --access-logfile - \
    --error-logfile - \
    --log-level info
