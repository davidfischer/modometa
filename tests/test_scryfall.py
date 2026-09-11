from core.pipeline.scryfall import _calculate_print_rank


def test_print_rank_alpha_vs_beta():
    """Beta printings should be preferred over Alpha, and both over later sets."""
    alpha_underground_sea = {
        "id": "alpha-ugsea",
        "name": "Underground Sea",
        "set": "lea",
        "released_at": "1993-08-05",
        "reprint": False,
        "collector_number": "286",
    }
    beta_underground_sea = {
        "id": "beta-ugsea",
        "name": "Underground Sea",
        "set": "leb",
        "released_at": "1993-10-04",
        "reprint": True,
        "collector_number": "286",
    }
    unlimited_underground_sea = {
        "id": "2ed-ugsea",
        "name": "Underground Sea",
        "set": "2ed",
        "released_at": "1993-12-01",
        "reprint": True,
        "collector_number": "286",
    }

    alpha_swords = {
        "id": "alpha-stp",
        "name": "Swords to Plowshares",
        "set": "lea",
        "released_at": "1993-08-05",
        "reprint": False,
        "collector_number": "41",
    }
    beta_swords = {
        "id": "beta-stp",
        "name": "Swords to Plowshares",
        "set": "leb",
        "released_at": "1993-10-04",
        "reprint": True,
        "collector_number": "41",
    }

    # Beta beats Alpha
    assert _calculate_print_rank(beta_underground_sea) < _calculate_print_rank(
        alpha_underground_sea
    )
    assert _calculate_print_rank(beta_swords) < _calculate_print_rank(alpha_swords)

    # Alpha beats Unlimited
    assert _calculate_print_rank(alpha_underground_sea) < _calculate_print_rank(
        unlimited_underground_sea
    )


def test_print_rank_regular_vs_variants():
    """Regular main set printing should be preferred over promo, showcase, and extended art."""
    # Guide of Souls: regular vs extended art vs prerelease promo
    guide_regular = {
        "id": "guide-reg",
        "name": "Guide of Souls",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "29",
        "booster": True,
        "border_color": "black",
    }
    guide_extended = {
        "id": "guide-ea",
        "name": "Guide of Souls",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "448",
        "booster": True,
        "frame_effects": ["extendedart"],
        "border_color": "borderless",
    }
    guide_promo = {
        "id": "guide-promo",
        "name": "Guide of Souls",
        "set": "pmh3",
        "released_at": "2024-06-07",  # Prerelease date earlier than main set
        "reprint": False,
        "collector_number": "29s",
        "promo": True,
        "promo_types": ["prerelease"],
    }

    assert _calculate_print_rank(guide_regular) < _calculate_print_rank(guide_extended)
    assert _calculate_print_rank(guide_regular) < _calculate_print_rank(guide_promo)

    # Voice of Victory: regular vs showcase
    voice_regular = {
        "id": "voice-reg",
        "name": "Voice of Victory",
        "set": "tdm",
        "released_at": "2025-04-11",
        "reprint": False,
        "collector_number": "33",
        "booster": True,
    }
    voice_showcase = {
        "id": "voice-sc",
        "name": "Voice of Victory",
        "set": "tdm",
        "released_at": "2025-04-11",
        "reprint": False,
        "collector_number": "331",
        "booster": True,
        "frame_effects": ["showcase"],
    }
    assert _calculate_print_rank(voice_regular) < _calculate_print_rank(voice_showcase)

    # Tamiyo, Inquisitive Student: regular vs bundle promo
    tamiyo_regular = {
        "id": "tamiyo-reg",
        "name": "Tamiyo, Inquisitive Student",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "242",
        "booster": True,
    }
    tamiyo_bundle = {
        "id": "tamiyo-bundle",
        "name": "Tamiyo, Inquisitive Student",
        "set": "mh3",
        "released_at": "2024-06-14",
        "reprint": False,
        "collector_number": "443",
        "promo_types": ["bundle"],
    }
    assert _calculate_print_rank(tamiyo_regular) < _calculate_print_rank(tamiyo_bundle)


def test_print_rank_non_standard_layout():
    """Normal playable layout should beat art series, tokens, and reversible cards."""
    normal_card = {
        "id": "normal-id",
        "name": "Murktide Regent",
        "set": "mh2",
        "layout": "normal",
        "released_at": "2021-06-18",
        "reprint": False,
        "collector_number": "52",
    }
    art_series_card = {
        "id": "art-id",
        "name": "Murktide Regent",
        "set": "amh2",
        "layout": "art_series",
        "released_at": "2021-06-18",
        "reprint": False,
        "collector_number": "1",
    }
    assert _calculate_print_rank(normal_card) < _calculate_print_rank(art_series_card)
