"""Shared utilities for MODOMeta."""

import os

from django.conf import settings


DEFAULT_USER_AGENT = os.environ.get(
    "MODOMETA_USER_AGENT", "Modometa/1.0 (https://github.com/modometa/modometa)"
)


def get_user_agent() -> str:
    """Return configured MODOMeta User-Agent string.

    Safely checks if Django settings are configured before querying settings.USER_AGENT,
    falling back to DEFAULT_USER_AGENT if imported outside a Django context.
    """
    if settings.configured:
        return getattr(settings, "USER_AGENT", DEFAULT_USER_AGENT)
    return DEFAULT_USER_AGENT
