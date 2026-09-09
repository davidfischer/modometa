"""Production settings for Modometa."""

import os
from pathlib import Path

from whitenoise.storage import CompressedManifestStaticFilesStorage

from .base import *  # noqa: F403
from .base import BASE_DIR


DEBUG = False

# In production, allow all hosts by default if not explicitly provided (e.g. *.fly.dev)
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",") if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CSRF_TRUSTED_ORIGINS", "https://*.fly.dev,http://*.fly.dev"
    ).split(",")
    if o.strip()
]

# Zero-write cookie sessions (no database writes needed for visitors)
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"

# In-memory application cache
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "modometa-prod-cache",
    }
}


class ModometaStaticFilesStorage(CompressedManifestStaticFilesStorage):
    def post_process(self, paths, dry_run=False, **options):
        # Filter out raw Tailwind source files that contain uncompiled build directives
        paths = {k: v for k, v in paths.items() if not k.endswith("input.css")}
        return super().post_process(paths, dry_run=dry_run, **options)


# WhiteNoise static file compression and caching
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "config.settings.prod.ModometaStaticFilesStorage",
    },
}

# Configurable database path
DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "modometa.db"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATABASE_PATH,
        "OPTIONS": {
            "timeout": 30,
        },
    }
}

# Configurable kNN index path
KNN_INDEX_PATH = Path(
    os.environ.get("KNN_INDEX_PATH", str(BASE_DIR / "data" / "knn_index.npz"))
)
