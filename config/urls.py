"""URL configuration for Modometa."""

from django.conf import settings
from django.contrib import admin
from django.urls import include
from django.urls import path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
]

# Enable Django Debug Toolbar in development only
if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
        *urlpatterns,
    ]
