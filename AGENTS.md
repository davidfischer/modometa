# AGENTS.md — Agent Operating Guide for MODOMeta

This document serves as the primary technical orientation and operational guide for AI coding agents working on the [MODOMeta](https://modometa.com) codebase.

---

## 1. Project Overview & Architecture

MODOMeta is an MTGO (Magic: The Gathering Online) metagame analyzer that tracks archetype play rates, Challenge Top 8 shares, League 5-0 finishes, conversion rates, and player match histories across MTG formats (Vintage, Legacy, Modern, Pioneer, Standard, Premodern, Pauper).

### Tech Stack
- **Language**: Python >= 3.14 (strict modern typing, PEP 8)
- **Package Manager**: [`uv`](https://docs.astral.sh/uv/)
- **Web Framework**: Django 5.2 / 6.1+
- **Styling**: Tailwind CSS v4 via Node.js (compiled into `core/static/css/protocol.css`) + [Mana font](https://mana.andrewgioia.com/)
- **Database**: SQLite in WAL mode (`modometa.db`), with database distributions via Cloudflare R2
- **Data Science / ML**: `scikit-learn`, `scipy`, `numpy` (TF-IDF vectorization, DBSCAN clustering, kNN deck similarity with 1-year exponential recency decay)
- **Code Quality**: `ruff` (linter and formatter), `pre-commit`, `pytest`, `pytest-django`

### Repository Structure
```text
modometa/
├── archetypes/               # Declarative YAML classification rules per format
│   ├── legacy.yaml
│   ├── vintage.yaml
│   ├── premodern.yaml
│   └── README.md             # Rule specification and curation guide
├── config/                   # Django project configuration
│   ├── settings/
│   │   ├── base.py           # Shared settings, format definitions, app registry
│   │   ├── dev.py            # Development settings (IS_TESTING flag, LocMemCache)
│   │   └── prod.py           # Production settings (WhiteNoise, security headers)
│   ├── urls.py               # Top-level URL routing
│   └── wsgi.py / asgi.py
├── core/                     # Main Django application
│   ├── models/
│   │   ├── card.py           # Card (Scryfall Oracle data) and CardLookup (aliases/DFCs)
│   │   ├── deck.py           # Deck (mainboard/sideboard JSON, rank, result, colors)
│   │   └── tournament.py     # Tournament (event_type, date, format)
│   ├── rules/
│   │   ├── colors.py         # True color combination deduction & pseudo-color filtering
│   │   ├── engine.py         # ArchetypeEngine (rule matching & tactical posture fallback)
│   │   ├── autodetect.py     # FormatAutodetector (identifies format from card legality)
│   │   └── legality.py       # LegalityEngine (verifies format bans & deck construction)
│   ├── pipeline/
│   │   ├── scryfall.py       # Ingests Scryfall bulk data (default_cards), artwork selection
│   │   └── ingest.py         # Ingests MTGO tournament JSON from MTG_decklistcache
│   ├── engine/
│   │   ├── knn.py            # TF-IDF & recency-weighted kNN deck similarity
│   │   └── search_index.py   # Search index generation
│   ├── management/commands/  # Django CLI commands (run via `uv run modometa <command>`)
│   ├── templates/            # Django HTML templates (styled with Tailwind CSS)
│   ├── templatetags/         # Custom template filters/tags (e.g. mana_tags.py)
│   ├── static/               # Static assets (input.css, protocol.css, mana font)
│   └── views/
│       └── __init__.py       # Views (overview, archetype, deck, player, tournament, caching)
├── data/                     # Generated vectors & indices (knn_index.npz, search_index.json)
├── tests/                    # Pytest test suite
├── Makefile                  # Developer workflow targets
└── pyproject.toml            # Project dependencies and tool configurations
```

---

## 2. Strict Agent Rules & Conventions

When modifying or adding code to MODOMeta, all agents MUST follow these core rules:

### 1. Do NOT Stage Files with `git add`
- **Never run `git add` or `git commit`** unless explicitly commanded by the user.
- The user maintains explicit control over what is staged in git.

### 2. Top-of-File Imports Only
- **All Python imports must strictly be placed at the top of the file**, following standard PEP 8 grouping (standard library -> third-party -> first-party / local).
- **Never place inline imports** inside function bodies, methods, or test functions unless required to avoid a circular import.

### 3. Tailwind CSS & Protocol CSS Workflow
- The compiled CSS lives in `core/static/css/protocol.css` and is committed to git.
- If you introduce new Tailwind utility classes in templates or edit `core/static/css/input.css`, you **must** recompile CSS:
  ```bash
  make css
  # which executes: npm run build:css && printf '\n' >> core/static/css/protocol.css
  ```
- Note the `printf '\n'` ensuring a trailing newline so the `end-of-file-fixer` pre-commit hook passes.

### 4. Code Formatting and Linting
- Ruff is configured with single-line imports (`force-single-line = true`) and strict flake8 rules.
- Format and fix:
  ```bash
  make format   # uv run ruff format . && uv run ruff check --fix .
  ```
- Verify before declaring work complete:
  ```bash
  uv run ruff check .
  uv run ruff format --check .
  ```

### 5. Automated Tests
- Run the full suite using:
  ```bash
  uv run pytest
  # or
  make test
  ```
- Always ensure all existing tests pass before concluding your turn.

---

## 3. Core Domain Concepts & Business Logic

### Deck Structure & Legality
- **Mainboard**: Minimum 60 cards, stored as `[{"card": "Lightning Bolt", "count": 4}, ...]`.
- **Sideboard**: Maximum 15 cards.
- **Card Limits**: Maximum 4 copies of any card across mainboard + sideboard, except basic lands and cards with explicit rules exceptions (*Relentless Rats*, *Dragon's Approach*, etc.).
- **Formats Supported**: Standard, Pioneer, Pauper, Modern, Premodern, Legacy, Vintage (`core/formats.py`). Formats not specified in `settings.ACTIVE_FORMAT_SLUGS` are hidden in the UI.

### Archetype Classification Engine (`core/rules/engine.py`)
Declarative rules live in `archetypes/{format}.yaml`. Rules are usually done by hand. See `archetypes/README.md` for more details.


### Caching and `IS_TESTING`
- Views use `@public_cache(cdn_seconds=..., browser_seconds=...)`.
- The decorator caches rendered HTML responses in `LocMemCache` and emits `Cloudflare-CDN-Cache-Control` and `Cache-Control` headers.
- **Testing Guard**: When `settings.IS_TESTING` is True (detected automatically in `config/settings/dev.py` when running pytest), `@public_cache` and cached dataset helpers like `get_dataset_min_date` bypass cache reads/writes to prevent cross-test pollution.

---

## 4. Common CLI Commands

The project uses `core.cli:main` registered as `modometa` in `pyproject.toml`. You can run any command using `uv run modometa <command>`.

| Command | Purpose |
|---|---|
| `uv run modometa runserver` | Run local Django development server at `http://127.0.0.1:8000` |
| `uv run modometa migrate` | Apply database migrations |
| `uv run modometa makemigrations` | Generate new database migrations |
| `uv run modometa sync_scryfall` | Download and sync Scryfall default bulk cards into `Card` & `CardLookup` |
| `uv run modometa ingest_tournaments` | Ingest MTGO tournament results from `MTG_decklistcache` (supports `--format`, `--limit`) |
| `uv run modometa reclassify_decks` | Reclassify decks against YAML rules (supports `--format`, `--unclassified`, `--dry-run`, `--skip-knn`) |
| `uv run modometa classify_deck` | Classify an ad-hoc decklist from file or stdin (pipeable: `cat deck.txt \| uv run modometa classify_deck`) |
| `uv run modometa discover_archetypes` | Unsupervised clustering (TF-IDF + DBSCAN) to find unclassified archetypes |
| `uv run modometa build_knn` | Rebuild TF-IDF vector similarity matrix (`data/knn_index.npz`) |
| `make check` | Run full verification suite (CSS build, linters, formatters, migration check, tests) |
| `make test` | Run pytest suite |
| `make css` | Compile Tailwind CSS into `core/static/css/protocol.css` |

---

## 5. Common Pitfalls & Gotchas

- **Do not introduce inline imports**: Even inside tests or conditional logic, keep all imports at the top of the file.
- **Do not forget to recompile CSS when editing HTML classes**: If you add new Tailwind classes to templates or adjust `input.css`, always run `make css` and make sure `protocol.css` has a trailing newline.
- **Card aliases and DFCs**: Cards can be referenced by front face or full double-faced name. Always use `normalize_card_name()` and check `CardLookup` when resolving card strings.
- **Testing environment cache**: If you test views that use caching, remember that `settings.IS_TESTING` is enabled during pytest runs.
- **Memory usage**: MODOMeta is deployed in production on limited servers with only ~1GB of RAM. Be mindful of memory when introducing new classifications or other features.
