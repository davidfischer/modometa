"""WSGI config for Modometa."""

import logging
import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
application = get_wsgi_application()

# Pre-warm kNN index on server startup so initial deck view requests do not incur cold-load penalty
try:
    from core.engine.knn import get_global_knn_index

    get_global_knn_index()
except Exception as exc:
    logging.getLogger("core.knn").warning(
        "Failed to pre-warm kNN index on WSGI startup: %s", exc
    )
