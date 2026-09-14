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

    # Timeframe (30, 90, 180, 365 days; default 90)
    timeframe = request.GET.get("days", "90")
    if timeframe not in ("30", "90", "180", "365"):
        timeframe = "90"

    timeframe_query = f"?days={timeframe}" if timeframe != "90" else ""
    timeframe_query_param = timeframe if timeframe != "90" else None

    return {
        "MODOMETA_FORMATS": formats,
        "ACTIVE_FORMAT": active_format,
        "ACTIVE_FORMAT_SLUGS": active_slugs,
        "TIMEFRAME": timeframe,
        "TIMEFRAME_QUERY": timeframe_query,
        "TIMEFRAME_QUERY_PARAM": timeframe_query_param,
        "SITE_NAME": getattr(settings, "SITE_NAME", "MODOMeta"),
    }
