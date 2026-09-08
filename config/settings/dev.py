"""Development settings for Modometa."""

import sys

from .base import *  # noqa: F403


DEBUG = True
ALLOWED_HOSTS = ["*"]

# In-memory caching for speedy development
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "modometa-dev-cache",
    }
}

# Detect if we are running under automated tests (pytest, manage.py test)
IS_TESTING = (
    "pytest" in sys.modules
    or "test" in sys.argv
    or any("pytest" in arg for arg in sys.argv)
)

# Django Debug Toolbar (Development Only, excluded during automated tests)
if not IS_TESTING:
    INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]  # noqa: F405

    MIDDLEWARE = [  # noqa: F405
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        *MIDDLEWARE,  # noqa: F405
    ]

    INTERNAL_IPS = [
        "127.0.0.1",
        "::1",
    ]

    DEBUG_TOOLBAR_CONFIG = {
        # Ensure toolbar displays in dev regardless of host/docker
        "SHOW_TOOLBAR_CALLBACK": lambda request: True,
    }
