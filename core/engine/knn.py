"""k-Nearest Neighbors (kNN) Deck Similarity Engine using TF-IDF and Recency Decay."""

import logging
import warnings
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from django.conf import settings
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfTransformer

from core.models.card import normalize_card_name
from core.models.deck import Deck


logger = logging.getLogger(__name__)

_GLOBAL_INDEX: "DeckKNNIndex | None" = None
_GLOBAL_INDEX_LOADED: bool = False


class DeckKNNIndex:
    """In-memory TF-IDF index for fast nearest-deck search with 1-year exponential decay."""

    def __init__(
        self,
        format_filter: str | None = None,
        half_life_days: float = 365.0,
        lazy: bool = False,
    ):
        self.format_filter = format_filter
        self.half_life_days = half_life_days

        self.deck_ids: list[str] | np.ndarray = []
        self.deck_dates: list[Any] | np.ndarray = []
        self.deck_players: list[str] | np.ndarray = []
        self.deck_archetypes: list[str] | np.ndarray = []
        self.deck_formats: list[str] | np.ndarray = []
        self.deck_colors: list[str] | np.ndarray = []
        self.deck_color_names: list[str] | np.ndarray = []

        self.vocab: dict[str, int] = {}
        self.inv_vocab: list[str] | np.ndarray = []
        self.tfidf_matrix: csr_matrix | None = None
        self.transformer: TfidfTransformer | None = None

        if not lazy:
            self._build_index()

    def _build_index(self) -> None:
        """Build TF-IDF sparse matrix from database decks."""
        qs = Deck.objects.select_related("tournament").all()
        if self.format_filter:
            qs = qs.filter(format=self.format_filter)

        decks_data = list(
            qs.values(
                "id",
                "format",
                "player",
                "archetype",
                "colors",
                "color_name",
                "tournament__date",
                "mainboard",
                "sideboard",
            )
        )

        if not decks_data:
            return

        vocab_map: dict[str, int] = {}
        rows, cols, data = [], [], []

        for row_idx, d in enumerate(decks_data):
            self.deck_ids.append(d["id"])
            self.deck_formats.append(d["format"])
            self.deck_players.append(d["player"])
            self.deck_archetypes.append(d["archetype"])
            self.deck_colors.append(d.get("colors", "") or "")
            self.deck_color_names.append(d.get("color_name", "") or "")
            self.deck_dates.append(d["tournament__date"])

            # Count cards: mainboard 1.0, sideboard 0.5
            counts: dict[str, float] = {}
            for item in d["mainboard"]:
                c = normalize_card_name(item.get("card", ""))
                if c:
                    counts[c] = counts.get(c, 0.0) + float(item.get("count", 1))

            for item in d["sideboard"]:
                c = normalize_card_name(item.get("card", ""))
                if c:
                    counts[c] = counts.get(c, 0.0) + 0.5 * float(item.get("count", 1))

            for card, cnt in counts.items():
                if card not in vocab_map:
                    vocab_map[card] = len(vocab_map)
                col_idx = vocab_map[card]
                rows.append(row_idx)
                cols.append(col_idx)
                data.append(cnt)

        self.vocab = vocab_map
        self.inv_vocab = [None] * len(vocab_map)  # type: ignore
        for card, idx in vocab_map.items():
            self.inv_vocab[idx] = card

        n_decks = len(decks_data)
        n_features = len(vocab_map)
        term_matrix = csr_matrix(
            (data, (rows, cols)), shape=(n_decks, n_features), dtype=np.float32
        )

        self.transformer = TfidfTransformer(norm="l2", sublinear_tf=True)
        self.tfidf_matrix = self.transformer.fit_transform(term_matrix)

    def save(self, file_path: Path | str) -> Path:
        """Save the fitted index to a native NumPy/SciPy compressed .npz archive."""
        if self.tfidf_matrix is None or self.transformer is None:
            raise ValueError("Cannot save an unbuilt or empty index.")

        dest = Path(file_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            dest,
            data=self.tfidf_matrix.data,
            indices=self.tfidf_matrix.indices,
            indptr=self.tfidf_matrix.indptr,
            shape=self.tfidf_matrix.shape,
            idf=self.transformer.idf_,
            deck_ids=np.array(self.deck_ids),
            deck_formats=np.array(self.deck_formats),
            deck_players=np.array(self.deck_players),
            deck_archetypes=np.array(self.deck_archetypes),
            deck_colors=np.array(self.deck_colors),
            deck_color_names=np.array(self.deck_color_names),
            deck_dates=np.array(
                [
                    d.isoformat() if hasattr(d, "isoformat") else str(d)
                    for d in self.deck_dates
                ]
            ),
            vocab=np.array(self.inv_vocab),
        )
        return dest

    @classmethod
    def load(
        cls, file_path: Path | str, half_life_days: float = 365.0
    ) -> "DeckKNNIndex":
        """Load a fitted index from a native NumPy/SciPy compressed .npz archive."""
        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"kNN index file not found at: {src}")

        instance = cls(format_filter=None, half_life_days=half_life_days, lazy=True)
        with np.load(src, allow_pickle=False) as f:
            instance.tfidf_matrix = csr_matrix(
                (f["data"], f["indices"], f["indptr"]), shape=f["shape"]
            )
            instance.deck_ids = f["deck_ids"]
            instance.deck_formats = f["deck_formats"]
            instance.deck_players = f["deck_players"]
            instance.deck_archetypes = f["deck_archetypes"]
            instance.deck_colors = (
                f["deck_colors"]
                if "deck_colors" in f
                else np.array([""] * len(instance.deck_ids))
            )
            instance.deck_color_names = (
                f["deck_color_names"]
                if "deck_color_names" in f
                else np.array([""] * len(instance.deck_ids))
            )
            instance.deck_dates = f["deck_dates"]
            instance.inv_vocab = f["vocab"]
            instance.vocab = {card: idx for idx, card in enumerate(instance.inv_vocab)}

            idf = f["idf"]
            transformer = TfidfTransformer(norm="l2", sublinear_tf=True)
            transformer.idf_ = idf
            instance.transformer = transformer

        return instance

    def vector_from_decklist(
        self, mainboard: list[dict[str, Any]], sideboard: list[dict[str, Any]]
    ) -> csr_matrix:
        """Convert an arbitrary decklist to a normalized TF-IDF vector in this index's space."""
        if not self.vocab or self.transformer is None:
            return csr_matrix((1, max(1, len(self.vocab))), dtype=np.float32)

        cols, data = [], []
        counts: dict[str, float] = {}
        for item in mainboard:
            c = normalize_card_name(item.get("card", ""))
            if c:
                counts[c] = counts.get(c, 0.0) + float(item.get("count", 1))

        for item in sideboard:
            c = normalize_card_name(item.get("card", ""))
            if c:
                counts[c] = counts.get(c, 0.0) + 0.5 * float(item.get("count", 1))

        for card, cnt in counts.items():
            if card in self.vocab:
                cols.append(self.vocab[card])
                data.append(cnt)

        rows = [0] * len(cols)
        raw_vec = csr_matrix(
            (data, (rows, cols)), shape=(1, len(self.vocab)), dtype=np.float32
        )
        return self.transformer.transform(raw_vec)

    def query(
        self,
        query_vec: csr_matrix,
        reference_date: date | None = None,
        top_k: int = 5,
        exclude_deck_id: str | None = None,
        format_filter: str | None = None,
        exclude_format: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find the top_k closest decks using cosine similarity and 1-year recency decay."""
        if self.tfidf_matrix is None or self.tfidf_matrix.shape[0] == 0:
            return []

        # Cosine similarity is dot product because rows are L2-normalized
        similarities = (self.tfidf_matrix @ query_vec.T).toarray().ravel()

        # Fast vector filtering for candidate matches (sim > 0.01)
        candidate_indices = np.where(similarities > 0.01)[0]
        if len(candidate_indices) == 0:
            return []

        ref_d = reference_date or date.today()
        results = []

        for i in candidate_indices:
            deck_id = self.deck_ids[i]
            if exclude_deck_id and deck_id == exclude_deck_id:
                continue

            deck_fmt = self.deck_formats[i]
            if format_filter and deck_fmt != format_filter:
                continue
            if exclude_format and deck_fmt == exclude_format:
                continue

            base_sim = float(similarities[i])

            raw_d = self.deck_dates[i]
            if isinstance(raw_d, date):
                d_date = raw_d
            else:
                d_date = date.fromisoformat(str(raw_d))

            days_diff = abs((ref_d - d_date).days)
            recency_weight = 0.5 ** (days_diff / self.half_life_days)
            final_score = base_sim * recency_weight

            deck_colors = str(self.deck_colors[i]) if len(self.deck_colors) > i else ""
            deck_color_name = (
                str(self.deck_color_names[i]) if len(self.deck_color_names) > i else ""
            )

            results.append(
                {
                    "deck_id": deck_id,
                    "player": self.deck_players[i],
                    "archetype": self.deck_archetypes[i],
                    "colors": deck_colors,
                    "color_name": deck_color_name,
                    "format": deck_fmt,
                    "date": d_date,
                    "raw_similarity": base_sim,
                    "recency_weight": recency_weight,
                    "score": final_score,
                }
            )

        # Sort by final score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]


def build_knn_index(
    dest_path: Path | str | None = None,
    stdout: Any = None,
) -> tuple[Path, int, int, int]:
    """Build the global TF-IDF kNN index for all tournaments and save as compressed .npz.

    Returns:
        (saved_path, deck_count, vocab_size, non_zeros)
    """
    if dest_path is None:
        dest_path = getattr(
            settings,
            "KNN_INDEX_PATH",
            Path(settings.BASE_DIR) / "data" / "knn_index.npz",
        )
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if stdout:
        stdout.write("Fetching all tournament decks from database...")

    qs = (
        Deck.objects.select_related("tournament")
        .all()
        .order_by("tournament__date", "id")
    )
    deck_ids, deck_formats, deck_players, deck_archetypes, deck_dates = (
        [],
        [],
        [],
        [],
        [],
    )
    vocab_map: dict[str, int] = {}
    rows, cols, data = [], [], []

    for row_idx, d in enumerate(
        qs.values(
            "id",
            "format",
            "player",
            "archetype",
            "tournament__date",
            "mainboard",
            "sideboard",
        ).iterator(chunk_size=10000)
    ):
        deck_ids.append(d["id"])
        deck_formats.append(d["format"])
        deck_players.append(d["player"])
        deck_archetypes.append(d["archetype"])
        d_date = d["tournament__date"]
        deck_dates.append(
            d_date.isoformat() if hasattr(d_date, "isoformat") else str(d_date)
        )

        counts: dict[str, float] = {}
        for item in d.get("mainboard", []):
            c = normalize_card_name(item.get("card", ""))
            if c:
                counts[c] = counts.get(c, 0.0) + float(item.get("count", 1))

        for item in d.get("sideboard", []):
            c = normalize_card_name(item.get("card", ""))
            if c:
                counts[c] = counts.get(c, 0.0) + 0.5 * float(item.get("count", 1))

        for card, cnt in counts.items():
            idx = vocab_map.get(card)
            if idx is None:
                idx = len(vocab_map)
                vocab_map[card] = idx
            rows.append(row_idx)
            cols.append(idx)
            data.append(cnt)

        if stdout and (row_idx + 1) % 100000 == 0:
            stdout.write(f"Processed {row_idx + 1} decks...")

    n_decks = len(deck_ids)
    n_features = len(vocab_map)
    non_zeros = len(data)

    if stdout:
        stdout.write(
            f"Building sparse matrix for {n_decks} decks with {n_features} unique cards..."
        )

    term_matrix = csr_matrix(
        (data, (rows, cols)), shape=(n_decks, n_features), dtype=np.float32
    )

    transformer = TfidfTransformer(norm="l2", sublinear_tf=True)
    tfidf_matrix = transformer.fit_transform(term_matrix)

    inv_vocab = [None] * n_features
    for card, idx in vocab_map.items():
        inv_vocab[idx] = card

    if stdout:
        stdout.write(f"Compressing and saving to {dest}...")

    np.savez_compressed(
        dest,
        data=tfidf_matrix.data,
        indices=tfidf_matrix.indices,
        indptr=tfidf_matrix.indptr,
        shape=tfidf_matrix.shape,
        idf=transformer.idf_,
        deck_ids=np.array(deck_ids),
        deck_formats=np.array(deck_formats),
        deck_players=np.array(deck_players),
        deck_archetypes=np.array(deck_archetypes),
        deck_dates=np.array(deck_dates),
        vocab=np.array(inv_vocab),
    )

    loaded = DeckKNNIndex.load(dest)
    set_global_knn_index(loaded)

    return dest, n_decks, n_features, non_zeros


_GLOBAL_INDEX: "DeckKNNIndex | None" = None
_GLOBAL_INDEX_LOADED: bool = False
_GLOBAL_INDEX_MTIME: float = 0.0


def get_global_knn_index(force_reload: bool = False) -> DeckKNNIndex | None:
    """Retrieve the in-memory global kNN index singleton, loading it from disk if needed."""
    global _GLOBAL_INDEX, _GLOBAL_INDEX_LOADED, _GLOBAL_INDEX_MTIME

    index_path = getattr(
        settings, "KNN_INDEX_PATH", Path(settings.BASE_DIR) / "data" / "knn_index.npz"
    )
    p = Path(index_path)
    if not p.exists():
        if not _GLOBAL_INDEX_LOADED:
            msg = (
                f"kNN index file not found at '{p}'. Deck similarity neighbors will not be available. "
                "Run 'uv run modometa build_knn' to generate it."
            )
            logger.warning(msg)
            warnings.warn(msg, UserWarning, stacklevel=2)
            _GLOBAL_INDEX_LOADED = True
            _GLOBAL_INDEX = None
            _GLOBAL_INDEX_MTIME = 0.0
        return None

    try:
        current_mtime = p.stat().st_mtime
    except OSError:
        current_mtime = 0.0

    if (
        _GLOBAL_INDEX_LOADED
        and not force_reload
        and current_mtime <= _GLOBAL_INDEX_MTIME
    ):
        return _GLOBAL_INDEX

    try:
        logger.info("Loading kNN index from %s...", p)
        _GLOBAL_INDEX = DeckKNNIndex.load(p)
        _GLOBAL_INDEX_LOADED = True
        _GLOBAL_INDEX_MTIME = current_mtime
        return _GLOBAL_INDEX
    except Exception as exc:
        logger.error("Failed to load kNN index from %s: %s", p, exc)
        _GLOBAL_INDEX_LOADED = True
        _GLOBAL_INDEX = None
        _GLOBAL_INDEX_MTIME = 0.0
        return None


def set_global_knn_index(index: DeckKNNIndex | None, loaded: bool = True) -> None:
    """Set the global in-memory kNN index singleton."""
    global _GLOBAL_INDEX, _GLOBAL_INDEX_LOADED, _GLOBAL_INDEX_MTIME
    _GLOBAL_INDEX = index
    _GLOBAL_INDEX_LOADED = loaded
    if index is not None:
        index_path = getattr(
            settings,
            "KNN_INDEX_PATH",
            Path(settings.BASE_DIR) / "data" / "knn_index.npz",
        )
        p = Path(index_path)
        try:
            _GLOBAL_INDEX_MTIME = p.stat().st_mtime if p.exists() else 0.0
        except OSError:
            _GLOBAL_INDEX_MTIME = 0.0
    else:
        _GLOBAL_INDEX_MTIME = 0.0
