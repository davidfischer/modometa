# MODOMeta — MTGO Metagame Analyzer 🎴

A high-performance Magic the Gathering Online ([MTGO](https://www.mtgo.com)) metagame analyzer focused on tracking archetype appearance rates, Challenge Top 8 shares, League 5-0 shares, and conversion rates across all major MTGO formats:
- **Vintage**
- **Legacy**
- **Modern** (WIP)
- **Pioneer** (WIP)
- **Standard** (WIP)
- **Premodern** (WIP)
- **Pauper** (WIP)

---

## Key Features

1. **Archetype Classification**:
   - Declarative rules in `archetypes/{format}.yaml` with `mandatory` cards, `signatures`, `anti_signatures`, and `priority`. See the README under `archetypes/` for more details.
   - Posture (Tempo, Aggro, Control, etc.) and mana-base fallback ("Grixis", etc.)
     ensures no deck is left unlabeled.
2. **True Color Combination Deduction**:
   - Deduces deck colors from mana-producing lands/sources and non-alternate cost spells.
   - Ignores pseudo-colors from Phyrexian mana (*Surgical Extraction*, *Dismember*, *Mutagenic Growth*) or pitch/exile effects (*Faerie Macabre*, *Simian Spirit Guide*, *Leylines*).
3. **Pipeable CLI Tooling**:
   - Classify decks one-by-one directly via pipes:
     ```bash
     cat mydeck.txt | uv run modometa classify_deck --format legacy
     ```
   - Discover unclassified archetype clusters using TF-IDF and clustering:
     ```bash
     uv run modometa discover_archetypes --format modern --min-cluster 5
     ```
4. **k-Nearest Neighbors (kNN) Similarity**:
   - Within-format and cross-format deck retrieval powered by TF-IDF (penalizing format staples) and a 1-year half-life exponential recency decay.
5. **Legality & Banlist Engine**:
   - Uses [Scryfall bulk data](https://scryfall.com/docs/api/bulk-data) for legality.
   - Highlights whether historical decks remain legal under current format banlists.

---

## Quick Start

### 1. Requirements
- Python >= 3.14
- [`uv`](https://docs.astral.sh/uv/)
- Node.js >= 24 (for Tailwind CSS compilation)

### 2. Setup Environment
```bash
uv sync
npm install
npm run build:css
```

### 3. Database Initialization & Data Sync
```bash
# Apply Django migrations
uv run modometa migrate

# Ingest Scryfall Oracle cards bulk data
uv run modometa sync_scryfall

# Ingest MTGO tournaments from local cache (supports --format and --limit)
# For all formats, takes ~1-2 minutes per year of tournaments
# You must have pulled https://github.com/fbettega/MTG_decklistcache already
uv run modometa ingest_tournaments
```

### 4. Run Development Server
```bash
uv run modometa runserver
```
Navigate to `http://localhost:8000` to view the metagame analyzer or `http://localhost:8000/admin/` for the backoffice.

---

## CLI Usage

### Classify a Deck via Stdin or File
```bash
cat deck.txt | uv run modometa classify_deck
```

### Discover Archetypes from Tournament Data
```bash
uv run modometa discover_archetypes --format modern --min-cluster 4
```

### Reclassify Decks in Database after Archetype Rules Changes

For more details on archetype classification, see the README under `archetypes/`.

After changing any `archetypes/*.yaml` files:

```bash
# Reclassify all formats in-place
# Changes archetype and deck color combination
uv run modometa reclassify_decks

# Or target a single format
uv run modometa reclassify_decks --format modern

# Reclassify only fallback/unclassified decks
uv run modometa reclassify_decks --format modern --unclassified

# Dry run preview without updating database
uv run modometa reclassify_decks --format modern --dry-run
```

### Compile Tailwind Styles
```bash
npm run build:css   # Production minified build
npm run watch:css   # Development watch mode
```

### Running Tests
```bash
uv run pytest
```
