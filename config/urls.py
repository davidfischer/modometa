"""URL configuration for Modometa."""

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include
from django.urls import path
from django.views.generic import RedirectView
from django.views.generic import TemplateView


admin_path = getattr(settings, "DJANGO_ADMIN_PATH", "")
if admin_path and not admin_path.endswith("/"):
    admin_path = f"{admin_path}/"
admin_path = admin_path.lstrip("/")

urlpatterns = [
    path(
        "robots.txt",
        TemplateView.as_view(template_name="robots.txt", content_type="text/plain"),
        name="robots_txt",
    ),
    path(
        "favicon.ico",
        RedirectView.as_view(
            url=f"/{settings.STATIC_URL.lstrip('/')}img/favicon.ico", permanent=True
        ),
        name="favicon",
    ),
    path("healthz", lambda request: HttpResponse("OK"), name="healthz"),
    path(f"admin/{admin_path}", admin.site.urls),
    path("", include("core.urls")),
]

# Enable Django Debug Toolbar in development only
if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
        *urlpatterns,
    ]
