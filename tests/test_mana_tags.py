from django.template import Context
from django.template import Template

from core.templatetags.mana_tags import mana_cost_filter
from core.templatetags.mana_tags import mana_symbols_filter
from core.templatetags.mana_tags import mana_symbols_tag


def test_mana_symbols_filter_single_color():
    html = mana_symbols_filter("W", title="Mono-White")
    assert 'class="ms ms-w ms-cost ms-shadow"' in html
    assert 'title="Mono-White"' in html


def test_mana_symbols_filter_multicolor_canonical_order():
    html = mana_symbols_filter("GUBR", title="4-Color")
    # Order should be sorted canonically to U, B, R, G
    u_idx = html.index("ms-u")
    b_idx = html.index("ms-b")
    r_idx = html.index("ms-r")
    g_idx = html.index("ms-g")
    assert u_idx < b_idx < r_idx < g_idx
    assert 'title="4-Color"' in html


def test_mana_symbols_filter_colorless():
    html = mana_symbols_filter("C", title="Colorless")
    assert 'class="ms ms-c ms-cost ms-shadow"' in html
    assert 'title="Colorless"' in html


def test_mana_symbols_filter_empty_and_invalid():
    assert "—" in mana_symbols_filter("")
    assert "—" in mana_symbols_filter(None)
    assert "—" in mana_symbols_filter("XYZ")


def test_mana_symbols_tag():
    html = mana_symbols_tag("UR", title="Izzet")
    assert "ms-u" in html
    assert "ms-r" in html
    assert 'title="Izzet"' in html


def test_mana_cost_filter():
    cost = mana_cost_filter("{1}{U}{B}")
    assert "ms-1" in cost
    assert "ms-u" in cost
    assert "ms-b" in cost

    phyrexian = mana_cost_filter("{W/P}")
    assert "ms-wp" in phyrexian

    hybrid = mana_cost_filter("{W/U}")
    assert "ms-wu" in hybrid

    split = mana_cost_filter("{4}{U} // {1}{U}")
    assert "ms-4" in split
    assert "//" in split


def test_mana_cost_filter_empty():
    assert mana_cost_filter("") == ""
    assert mana_cost_filter(None) == ""


def test_template_integration():
    template_str = (
        "{% load mana_tags %}{{ colors|mana_symbols:title }}|{{ cost|mana_cost }}"
    )
    template = Template(template_str)
    rendered = template.render(
        Context({"colors": "WUBG", "title": "4-Color", "cost": "{2}{R}"})
    )
    assert "ms-w" in rendered
    assert "ms-u" in rendered
    assert "ms-b" in rendered
    assert "ms-g" in rendered
    assert "ms-2" in rendered
    assert "ms-r" in rendered
