import importlib
import sys

import pytest
from django.conf import settings
from django.test import override_settings
from django.urls import clear_url_caches
from django.urls import reverse


def reload_urls():
    clear_url_caches()
    if "config.urls" in sys.modules:
        importlib.reload(sys.modules["config.urls"])


@pytest.fixture(autouse=True)
def _reset_urls():
    try:
        yield
    finally:
        reload_urls()


@pytest.mark.django_db
def test_admin_path_default(client):
    """By default, admin is at /admin/."""
    assert settings.DJANGO_ADMIN_PATH == ""
    reload_urls()
    assert reverse("admin:index") == "/admin/"
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.url == "/admin/login/?next=/admin/"


@pytest.mark.django_db
def test_admin_path_custom(client):
    """When DJANGO_ADMIN_PATH is set, admin is at /admin/<custom>/."""
    with override_settings(DJANGO_ADMIN_PATH="abcdefg/"):
        reload_urls()
        assert reverse("admin:index") == "/admin/abcdefg/"
        response = client.get("/admin/abcdefg/")
        assert response.status_code == 302
        assert response.url == "/admin/abcdefg/login/?next=/admin/abcdefg/"

        # Old /admin/ should now 404
        resp_old = client.get("/admin/")
        assert resp_old.status_code == 404
    reload_urls()


@pytest.mark.django_db
def test_admin_path_normalization(client):
    """DJANGO_ADMIN_PATH handles missing trailing slashes and leading slashes."""
    with override_settings(DJANGO_ADMIN_PATH="secret_admin"):
        reload_urls()
        assert reverse("admin:index") == "/admin/secret_admin/"
    reload_urls()

    with override_settings(DJANGO_ADMIN_PATH="/leading_slash/"):
        reload_urls()
        assert reverse("admin:index") == "/admin/leading_slash/"
    reload_urls()
