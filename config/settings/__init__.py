"""Settings package for Modometa."""

import os


env = os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings.dev")
if env.endswith(".prod"):
    from .prod import *  # noqa: F403
else:
    from .dev import *  # noqa: F403
