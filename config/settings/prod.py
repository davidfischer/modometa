"""Production settings for Modometa."""

import os

from .base import *  # noqa: F403


DEBUG = False

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# Cached sessions and file-based or database cache
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "modometa-prod-cache",
    }
}
