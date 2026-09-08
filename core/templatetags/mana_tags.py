"""Template tags and filters for rendering MTG mana symbols and costs using Mana font."""

import html
import re

from django import template
from django.utils.safestring import mark_safe


register = template.Library()

# Canonical MTG color order
COLOR_ORDER = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4, "C": 5}

SPECIAL_SYMBOLS = {
    "t": "tap",
    "q": "untap",
}


def _format_symbol(sym: str) -> str:
    """Format a single mana or tap code into an HTML <i> tag using Mana font classes."""
    code = sym.lower().replace("/", "")
    code = SPECIAL_SYMBOLS.get(code, code)
    escaped_code = html.escape(code)
    return f'<i class="ms ms-{escaped_code} ms-cost ms-shadow"></i>'


@register.filter(name="mana_symbols", is_safe=True)
def mana_symbols_filter(colors, title: str | None = None) -> str:
    """Render a color string (e.g. 'WUBG', 'UR', 'C') as colored mana symbol icons.

    Usage in templates:
        {{ a.colors|mana_symbols }}
        {{ a.colors|mana_symbols:a.color_name }}
    """
    if not colors:
        return mark_safe('<span class="text-zinc-600 text-xs">—</span>')

    valid_chars = [c.upper() for c in str(colors) if c.upper() in COLOR_ORDER]
    if not valid_chars:
        return mark_safe('<span class="text-zinc-600 text-xs">—</span>')

    valid_chars.sort(key=lambda c: COLOR_ORDER.get(c, 99))

    icons = "".join(_format_symbol(c) for c in valid_chars)
    title_attr = f' title="{html.escape(str(title))}"' if title else ""
    output = f'<span class="inline-flex items-center gap-0.5 align-middle"{title_attr}>{icons}</span>'
    return mark_safe(output)  # noqa: S308


@register.simple_tag(name="mana_symbols")
def mana_symbols_tag(colors, title: str = "") -> str:
    """Template tag wrapper for rendering colored mana symbols.

    Usage in templates:
        {% mana_symbols a.colors title=a.color_name %}
        {% mana_symbols a.colors %}
    """
    return mana_symbols_filter(colors, title=title)


@register.filter(name="mana_cost", is_safe=True)
def mana_cost_filter(cost_str) -> str:
    """Convert a card mana cost string like '{1}{U}{B}' into Mana font icons.

    Usage in templates:
        {{ item.mana_cost|mana_cost }}
    """
    if not cost_str:
        return ""

    def _replace_sym(match: re.Match) -> str:
        return _format_symbol(match.group(1))

    parts = str(cost_str).split(" // ")
    formatted_parts = []
    for part in parts:
        formatted = re.sub(r"\{([^}]+)\}", _replace_sym, part)
        formatted_parts.append(
            f'<span class="inline-flex items-center gap-0.5 align-middle">{formatted}</span>'
        )

    separator = ' <span class="text-zinc-500 font-sans mx-1 text-2xs">//</span> '
    return mark_safe(separator.join(formatted_parts))  # noqa: S308
