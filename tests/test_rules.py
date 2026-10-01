from unittest.mock import MagicMock

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from core.engine.knn import DeckKNNIndex
from core.models.card import expand_card_counts
from core.models.card import expand_card_names
from core.rules.engine import ArchetypeEngine
from core.rules.engine import ClassificationResult
from core.rules.engine import ClassificationType
from core.rules.engine import FallbackPostureResult
from core.rules.engine import KNNMatchResult


@pytest.fixture
def engine(test_rules_dir):
    return ArchetypeEngine(archetypes_dir=test_rules_dir)


def test_fallback_posture(engine):
    """An unknown deck without matched rules falls back to Colors + Tactical Posture."""
    cards = [
        "Mountain",
        "Goblin Guide",
        "Jackal Pup",
        "Lightning Bolt",
        "Lava Spike",
    ]
    res = engine.classify(cards, "legacy")
    assert "Mono-Red" in res.archetype_name
    assert res.is_fallback is True
    assert res.is_rule is False
    assert res.classification_type == ClassificationType.FALLBACK
    assert "Aggro" in res.archetype_name or "Tempo" in res.archetype_name


def test_expand_card_names():
    cards = [
        "Delver of Secrets // Insectile Aberration",
        "Lightning Bolt",
        "Brazen Borrower // Petty Theft",
        "Fire // Ice",
    ]
    expanded = expand_card_names(cards)

    # Full canonical names
    assert "delver of secrets // insectile aberration" in expanded
    assert "brazen borrower // petty theft" in expanded
    assert "fire // ice" in expanded
    assert "lightning bolt" in expanded

    # Front and back faces
    assert "delver of secrets" in expanded
    assert "insectile aberration" in expanded
    assert "brazen borrower" in expanded
    assert "petty theft" in expanded
    assert "fire" in expanded
    assert "ice" in expanded


def test_sideboard_companion_matches_mandatory(engine):
    """Sideboard companion satisfies mandatory rule for companion archetype."""
    mainboard = [
        "Plains",
        "Swords to Plowshares",
    ]

    # Without Yorion in sideboard -> does not match Test Companion
    res_no_sb = engine.classify(mainboard, "legacy")
    assert res_no_sb.archetype_name != "Test Companion"

    # With Yorion in sideboard -> matches Test Companion
    res_sb = engine.classify(
        mainboard, "legacy", sideboard_cards=["Yorion, Sky Nomad", "Rest in Peace"]
    )
    assert res_sb.archetype_name == "Test Companion"
    assert res_sb.is_fallback is False
    assert res_sb.is_rule is True
    # Mana base only produces White, so deck colors remain Mono-White
    assert res_sb.colors_code == "W"


def test_expand_card_counts():
    """Test expand_card_counts handles dicts, tuples, quantity strings, and multi-face cards."""
    cards = [
        {"card": "Delver of Secrets // Insectile Aberration", "count": 4},
        {"name": "Dragon's Rage Channeler", "count": 2},
        "4 Lightning Bolt",
        "2x Brainstorm",
        ("Force of Will", 4),
        "Ponder",
    ]
    counts = expand_card_counts(cards)

    assert counts["delver of secrets // insectile aberration"] == 4
    assert counts["delver of secrets"] == 4
    assert counts["insectile aberration"] == 4
    assert counts["dragons rage channeler"] == 2
    assert counts["lightning bolt"] == 4
    assert counts["brainstorm"] == 2
    assert counts["force of will"] == 4
    assert counts["ponder"] == 1


def test_knn_closeness_fallback_matches_at_85_threshold(monkeypatch):
    """When explicit rules don't match, kNN match with similarity >= 0.85 classifies the deck."""
    mock_knn = MagicMock()
    mock_knn.find_closest_classified.return_value = (
        "Stoneblade",
        "stoneblade",
        0.87,
        "deck_123",
    )
    monkeypatch.setattr("core.rules.engine.get_global_knn_index", lambda: mock_knn)

    engine = ArchetypeEngine(enable_knn=True, knn_threshold=0.85)
    cards = ["Plains", "Island", "Swords to Plowshares"]
    res = engine.classify(cards, "legacy")

    assert res.archetype_name == "Stoneblade"
    assert res.archetype_slug == "stoneblade"
    assert res.is_fallback is False
    assert res.is_knn is True
    assert res.is_rule is False
    assert res.classification_type == ClassificationType.KNN
    assert res.debug_info.get("matched_knn") == "Stoneblade"
    assert res.debug_info.get("similarity") == 0.87
    assert res.debug_info.get("classification_method") == "knn"


def test_knn_closeness_fallback_rejected_below_85_threshold(monkeypatch):
    """When kNN closest match is below 0.85, falls back to tactical posture."""
    mock_knn = MagicMock()
    mock_knn.find_closest_classified.return_value = None
    monkeypatch.setattr("core.rules.engine.get_global_knn_index", lambda: mock_knn)

    engine = ArchetypeEngine(enable_knn=True, knn_threshold=0.85)
    cards = ["Mountain", "Goblin Guide", "Lightning Bolt", "Lava Spike"]
    res = engine.classify(cards, "legacy")

    assert res.is_fallback is True
    assert res.is_knn is False
    assert res.is_rule is False
    assert res.classification_type == ClassificationType.FALLBACK
    assert "Mono-Red" in res.archetype_name
    assert "Aggro" in res.archetype_name or "Tempo" in res.archetype_name
    assert res.debug_info.get("matched_rule") is None
    assert res.debug_info.get("classification_method") == "fallback"
    assert res.debug_info.get("fallback_posture") in (
        "Aggro",
        "Tempo",
        "Midrange",
        "Control",
        "Combo",
    )


def test_knn_closeness_disabled_when_enable_knn_false(monkeypatch):
    """When enable_knn=False, skips kNN check even if index is present."""
    mock_knn = MagicMock()
    mock_knn.find_closest_classified.return_value = (
        "Stoneblade",
        "stoneblade",
        0.95,
        "deck_123",
    )
    monkeypatch.setattr("core.rules.engine.get_global_knn_index", lambda: mock_knn)

    engine = ArchetypeEngine(enable_knn=False)
    cards = ["Mountain", "Goblin Guide", "Lightning Bolt", "Lava Spike"]
    res = engine.classify(cards, "legacy")

    assert res.is_fallback is True
    assert res.is_knn is False
    assert res.is_rule is False
    assert res.classification_type == ClassificationType.FALLBACK
    assert "Mono-Red" in res.archetype_name
    assert res.debug_info.get("classification_method") == "fallback"
    mock_knn.find_closest_classified.assert_not_called()


def test_rule_classification_method_debug_info(engine):
    """Explicit YAML rule match reports classification_method='rule'."""
    cards = [
        "Delver of Secrets",
        "Lightning Bolt",
        "Volcanic Island",
    ]
    res = engine.classify(cards, "legacy")
    assert res.is_fallback is False
    assert res.is_rule is True
    assert res.classification_type == ClassificationType.RULE
    assert res.debug_info.get("classification_method") == "rule"
    assert res.debug_info.get("matched_rule") == "Test Tempo"


def test_knn_find_closest_classified_only_rule_classified():
    """kNN index only matches against decks with classification_method='rule'."""
    index = DeckKNNIndex(lazy=True)
    index.deck_ids = np.array(["deck_knn", "deck_rule", "deck_fallback"])
    index.deck_formats = np.array(["legacy", "legacy", "legacy"])
    index.deck_archetypes = np.array(["Temur Delver", "Temur Delver", "Temur Delver"])
    index.deck_colors = np.array(["U", "URG", "U"])
    index.deck_color_names = np.array(["Mono-Blue", "Temur", "Mono-Blue"])
    index.deck_classification_methods = np.array(["knn", "rule", "fallback"])
    index.deck_players = np.array(["P1", "P2", "P3"])
    index.deck_dates = np.array(["2026-01-01", "2026-01-02", "2026-01-03"])

    # Create dummy tfidf matrix: 3 decks, 2 features
    # Deck 0 (knn): [1.0, 0.0] -> dot query [1.0, 0.0] = 1.0 similarity
    # Deck 1 (rule): [0.9, 0.1] -> dot query [1.0, 0.0] = 0.9 similarity
    # Deck 2 (fallback): [0.99, 0.0] -> dot query [1.0, 0.0] = 0.99 similarity
    data = np.array([1.0, 0.9, 0.1, 0.99], dtype=np.float32)
    indices = np.array([0, 0, 1, 0], dtype=np.int32)
    indptr = np.array([0, 1, 3, 4], dtype=np.int32)
    index.tfidf_matrix = csr_matrix(
        (data, indices, indptr), shape=(3, 2), dtype=np.float32
    )

    query_vec = csr_matrix(np.array([[1.0, 0.0]], dtype=np.float32), shape=(1, 2))

    # When only_rule_classified=True, it MUST skip deck_knn (sim 1.0) and deck_fallback (sim 0.99),
    # and match deck_rule (sim 0.9)
    result = index.find_closest_classified(
        query_vec,
        format_filter="legacy",
        known_archetypes={"Temur Delver"},
        min_similarity=0.85,
        only_rule_classified=True,
    )
    assert result is not None
    arch_name, slug, sim, deck_id = result
    assert arch_name == "Temur Delver"
    assert deck_id == "deck_rule"
    assert round(sim, 2) == 0.90

    # If the rule-classified deck does not meet the threshold (e.g. min_similarity=0.95),
    # it must return None even though deck_knn (1.0) and deck_fallback (0.99) exist
    result_high_threshold = index.find_closest_classified(
        query_vec,
        format_filter="legacy",
        known_archetypes={"Temur Delver"},
        min_similarity=0.95,
        only_rule_classified=True,
    )
    assert result_high_threshold is None


def test_knn_index_save_and_load_classification_methods(tmp_path):
    """kNN index save and load preserves deck_classification_methods."""
    index = DeckKNNIndex(lazy=True)
    index.deck_ids = np.array(["d1", "d2"])
    index.deck_formats = np.array(["legacy", "modern"])
    index.deck_players = np.array(["P1", "P2"])
    index.deck_archetypes = np.array(["Arch1", "Arch2"])
    index.deck_colors = np.array(["U", "R"])
    index.deck_color_names = np.array(["Mono-Blue", "Mono-Red"])
    index.deck_classification_methods = np.array(["rule", "knn"])
    index.deck_dates = np.array(["2026-01-01", "2026-01-02"])
    index.inv_vocab = np.array(["Brainstorm", "Lightning Bolt"])
    index.vocab = {"Brainstorm": 0, "Lightning Bolt": 1}

    index.tfidf_matrix = csr_matrix(
        np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    )
    index.transformer = MagicMock()
    index.transformer.idf_ = np.array([1.0, 1.0], dtype=np.float32)

    save_path = tmp_path / "test_knn.npz"
    index.save(save_path)

    loaded = DeckKNNIndex.load(save_path)
    assert len(loaded.deck_classification_methods) == 2
    assert loaded.deck_classification_methods[0] == "rule"
    assert loaded.deck_classification_methods[1] == "knn"


def test_classification_result_dataclass():
    """ClassificationResult provides structured attributes, properties, and backward-compatible tuple unpacking."""
    res = ClassificationResult(
        archetype_name="Izzet Delver",
        archetype_slug="izzet-delver",
        colors_code="UR",
        color_display_name="Izzet",
        classification_type=ClassificationType.RULE,
        debug_info={"matched_rule": "Izzet Delver", "classification_method": "rule"},
    )
    # Direct attribute access
    assert res.archetype_name == "Izzet Delver"
    assert res.archetype_slug == "izzet-delver"
    assert res.colors_code == "UR"
    assert res.color_display_name == "Izzet"
    assert res.classification_type == ClassificationType.RULE
    assert res.is_rule is True
    assert res.is_knn is False
    assert res.is_fallback is False
    assert res.classification_method == "rule"

    # Convenience alias properties
    assert res.name == "Izzet Delver"
    assert res.slug == "izzet-delver"
    assert res.colors == "UR"
    assert res.color_name == "Izzet"

    # Backward-compatible indexing and unpacking
    name, slug, colors, color_name, clf_type, debug = res
    assert name == "Izzet Delver"
    assert slug == "izzet-delver"
    assert colors == "UR"
    assert color_name == "Izzet"
    assert clf_type == ClassificationType.RULE
    assert debug["classification_method"] == "rule"
    assert res[0] == "Izzet Delver"
    assert res[4] == ClassificationType.RULE


def test_knn_match_result_dataclass():
    """KNNMatchResult provides structured attributes, indexing, and unpacking."""
    match = KNNMatchResult(
        archetype_name="Grixis Delver",
        archetype_slug="grixis-delver",
        similarity=0.91,
        matched_deck_id="deck_123",
    )
    assert match.archetype_name == "Grixis Delver"
    assert match.similarity == 0.91
    assert match.matched_deck_id == "deck_123"

    # Unpacking
    name, slug, sim, deck_id = match
    assert name == "Grixis Delver"
    assert match[2] == 0.91


def test_fallback_posture_result_dataclass():
    """FallbackPostureResult provides structured posture and archetype attributes."""
    posture_res = FallbackPostureResult(
        posture="Control",
        archetype_name="Dimir Control",
        archetype_slug="dimir-control",
    )
    assert posture_res.posture == "Control"
    assert posture_res.archetype_name == "Dimir Control"

    # Unpacking
    p, name, slug = posture_res
    assert p == "Control"
    assert name == "Dimir Control"
    assert slug == "dimir-control"


def test_engine_enable_posture_false():
    """When enable_posture=False, unclassified decks return None for archetype name and is_fallback=True."""
    engine = ArchetypeEngine(enable_knn=False, enable_posture=False)
    # Random cards that don't match any explicit rule
    cards = ["Plains", "Island", "Healing Salve", "Sea Eagle"]
    res = engine.classify(cards, "legacy")

    assert isinstance(res, ClassificationResult)
    assert res.archetype_name is None
    assert res.archetype_slug is None
    assert res.is_fallback is True
    assert res.is_rule is False
    assert res.is_knn is False
    assert res.classification_type is None
    assert res.classification_method is None
    assert res.colors_code == "WU"
    assert res.color_display_name == "Azorius"


def test_engine_get_fallback_posture(engine):
    """get_fallback_posture correctly deduces tactical posture and names."""
    # Aggro cards
    aggro_cards = ["Mountain", "Goblin Guide", "Lava Spike", "Jackal Pup"]
    res = engine.get_fallback_posture(aggro_cards, "Mono-Red")
    assert isinstance(res, FallbackPostureResult)
    assert res.posture == "Aggro"
    assert res.archetype_name == "Mono-Red Aggro"
    assert res.archetype_slug == "mono-red-aggro"

    # Control cards
    control_cards = [
        "Island",
        "Plains",
        "Swords to Plowshares",
        "Supreme Verdict",
        "Force of Will",
    ]
    res_ctrl = engine.get_fallback_posture(control_cards, "Azorius")
    assert res_ctrl.posture == "Control"
    assert res_ctrl.archetype_name == "Azorius Control"
