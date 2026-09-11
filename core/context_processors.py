from django.conf import settings

from core.formats import FORMATS


def modometa_globals(request):
    """Provide global format choices and default timeframe to all templates."""
    active_slugs = getattr(settings, "ACTIVE_FORMAT_SLUGS", settings.MODOMETA_FORMATS)
    formats = [
        {"slug": f.slug, "name": f.name}
        for slug in active_slugs
        if (f := FORMATS.get(slug))
    ]
    # Current active format from URL path or query
    path = request.path.strip("/").split("/")
    active_format = path[0] if path and path[0] in settings.MODOMETA_FORMATS else None

    # Timeframe (30 or 90 days)
    timeframe = request.GET.get("days", "90")
    if timeframe not in ("30", "90"):
        timeframe = "90"

    return {
        "MODOMETA_FORMATS": formats,
        "ACTIVE_FORMAT": active_format,
        "ACTIVE_FORMAT_SLUGS": active_slugs,
        "TIMEFRAME": timeframe,
        "SITE_NAME": getattr(settings, "SITE_NAME", "MODOMeta"),
    }
