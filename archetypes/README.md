# Archetype Rules & Classification Engine

This directory contains declarative YAML rule files used by MODOMeta's rule-based classification engine to map decklists to specific archetypes across Magic: The Gathering Online formats.

When decks are ingested or reclassified, the engine tests candidate decks against these rules. If no explicit rule matches, MODOMeta falls back to an automated posture deduction combining the deck's deduced mana-base colors with its strategic speed (e.g., *Mono-Red Aggro*, *Dimir Control*, *Grixis Midrange*).

---

## 1. Rule Schema & Specification

Each YAML file is a list of archetype rules specifying the signature and mandatory cards for an archetype as well as cards that aren't present in the archetype.

---

## 2. Core Concepts & Best Practices

### "Named Decks" Should Have Mandatory Cards
If a deck is named after a specific card or mechanic, **that card must be listed under `mandatory`**. A deck should never be classified as a named archetype if it doesn't play its namesake card:

- **Vintage Oath** needs `oath of druids`. Without *Oath of Druids*, it's not "Oath".
- **Modern Amulet Titan** needs both `amulet of vigor` and `primeval titan`.
- **Legacy Sneak and Show** isn't Sneak and Show without `show and tell` and `sneak attack`.


### Mainboard and Sideboard Aggregation
Card quantities for `mandatory`, `signatures`, and `anti_signatures` are evaluated against the **combined mainboard and sideboard**:
- **Companions**: Archetypes defined by an 8th card (e.g. *Yorion Death & Taxes*, *Esper Lurrus*, *Jegantha Humans*) should list the companion in `mandatory`.
- **Sideboard Disqualification**: If an anti-signature card is brought in the sideboard (e.g. sideboard *Snuff Out*  in a Delver list), it properly disqualifies the deck from being categorized as pure *Izzet Delver*.

### Prioritizing Specialized Builds Over General Builds
When an archetype is a specialized variant of another (e.g. *Yorion Death & Taxes* vs. 60 card *Death & Taxes*):
1. Give the more specific archetype a **higher priority** (e.g. `110` vs `100`).
2. Use `anti_signatures` to cleanly divide archetypes that share overlapping colors and staple cards.

---

## 3. Concrete Examples

### Example: Vintage Oath of Druids

```yaml
- name: "Oath"
  # Category isn't currently used for anything
  # on ModoMeta but could be in the future
  category: "Combo"
  priority: 95
  # If at least 2 oaths aren't in the deck
  # it won't be classified as Vintage "Oath"
  mandatory:
    - card: "oath of druids"
      min: 2
  signatures:
    - "forbidden orchard"
    - "atraxa, grand unifier"
    - "show and tell"
    - "flash"
    - "gaea's blessing"
  # At least 2 of the signature cards above are required
  min_signatures: 2
  anti_signatures:
    - "doomsday"
    - "paradoxical outcome"
```

### Example: Legacy Izzet Delver

```yaml
- name: "Izzet Delver"
  category: "Tempo"
  priority: 100
  mandatory:
    # Handles specifying just the front side
    # Could also do "delver of secrets // insectile aberation"
    - "delver of secrets"
    # No quantity listed means minimum of 1
    - "daze"
  signatures:
    - "murktide regent"
    - "dragon's rage channeler"
    - "lightning bolt"
    - "force of will"
    - "volcanic island"
  min_signatures: 2
  anti_signatures:
    # Make sure this isn't "Grixis Delver"
    - "Snuff Out"
    - "underground sea"
    # Not Temur Delver either
    - "tropical island"
```

### Example: Distinguishing Mutually Exclusive Variants (Vintage Shops)
Both *Jewel Shops* and *Raker Shops* are built around *Mishra's Workshop*, but they have completely different gameplans.

```yaml
- name: "Jewel Shops"
  category: "Prison"
  priority: 95
  mandatory:
    - "coveted jewel"
    - card: "mishra's workshop"
      min: 3
  signatures:
    - "Tinker"
    - "trinisphere"
    - "The One Ring"
    - "Phyrexian Metamorph"
    - "Transmute Artifact"
  min_signatures: 2

- name: "Raker Shops"
  category: "Combo"
  priority: 95
  mandatory:
    - card: "mishra's workshop"
      min: 3
    - "Glaring Fleshraker"
  signatures:
    - "Sensei's Divining Top"
    - "patchwork automaton"
    - "Tezzeret, Cruel Captain"
    - "The One Ring"
  min_signatures: 2
  # If a deck contains Jewel,
  # it won't be classified as Raker Shops
  anti_signatures:
    - "coveted jewel"
```

---

## 4. CLI Commands & Workflow

MODOMeta includes three dedicated CLI commands for discovering, testing, and applying archetype rules.

### Discover Undiscovered Archetypes (`discover_archetypes`)

Uses unsupervised machine learning (TF-IDF vectorization and DBSCAN clustering on cosine distance) to identify clusters of similar decks in the database that currently lack an explicit YAML rule.

```bash
# Analyze unclassified decks in Modern forming clusters of at least 4 decks
uv run modometa discover_archetypes --format modern --min-cluster-size 4

# Fine-tune cluster tightness with --eps (lower is tighter, default: 0.42)
uv run modometa discover_archetypes --format vintage --eps 0.38 --min-cluster 3

# Analyze all decks in the format (not just unclassified fallback decks)
uv run modometa discover_archetypes --format pioneer --all
```

#### What It Does:
1. **Validates YAML Card Names**: First checks every card name in `archetypes/{format}.yaml` against the Scryfall database. If you made a typo (e.g. `"delver of secret"`), it warns you immediately.
2. **Clusters Decks**: Filters out basic lands, vectorizes mainboards with TF-IDF, and clusters with DBSCAN.
3. **Outputs Ready-to-Use YAML**: For every discovered cluster, prints the candidate archetype name, cluster size, top characteristic cards, and a template YAML snippet you can copy directly into your format file.

---

### Reclassify Stored Decks (`reclassify_decks`)

Whenever you add, modify, or delete rules in `archetypes/*.yaml`, run `reclassify_decks` to re-evaluate decks in the database in bulk.

```bash
# Reclassify decks across ALL formats
uv run modometa reclassify_decks

# Target a single format
uv run modometa reclassify_decks --format modern

# Reclassify ONLY unclassified / fallback posture decks (fastest)
uv run modometa reclassify_decks --format legacy --unclassified

# Dry-run mode: test rules and print statistics without writing changes to the DB
uv run modometa reclassify_decks --format legacy --dry-run

# Skip rebuilding the kNN deck similarity index (saves time during rapid testing)
uv run modometa reclassify_decks --format vintage --skip-knn
```

#### What It Does:
- **Recalculates Archetypes and Color Combinations**: Evaluates mainboard and sideboard cards against the latest YAML rules and automatically re-determines true color combinations based on mana bases.
- **Provides Transition Metrics**: Outputs a detailed report showing:
  - Total decks evaluated and updated.
  - Decks promoted from fallback posture to recognized archetypes: "123x: Golgari Midrange -> Hogaak"
  - Decks demoted from recognized archetypes to fallback posture: "123x: Hogaak -> Abzan Midrange"
  - Breakdown of specific archetype-to-archetype transitions: eg. "123x: Mystic Forge -> Tron"
- **Rebuilds kNN Vector Index**: Automatically recalculates TF-IDF index vectors for deck similarity unless `--skip-knn` is passed.

---

### Classify an Ad-Hoc Decklist (`classify_deck`)

Quickly test how the rules engine classifies an individual decklist from a file or directly via terminal pipes.

```bash
# Classify from a text file or stdin (auto-detects format from card pool and legality)
uv run modometa classify_deck path/to/decklist.txt

# Specify format explicitly
cat deck.txt | uv run modometa classify_deck --format legacy
```

#### Output Information:
- **Format**: Explicit or autodetected based on format card pools.
- **Archetype & Rule Match**: The matched archetype and rule score, or the fallback posture (`Mono-Red Aggro`, `Dimir Control`, etc.) if no rule matched.
- **Colors**: True color combination code and display name (e.g. `Jund (BRG)`).
- **Legality & Banlist Check**: Reports whether all cards are legal in the specified format and lists any banned or illegal cards.
- **k-Nearest Historical Decks**: Displays the top 5 most similar tournament decks in the database with recency weighting and match scores.

---

## 5. Typical Curation Workflow

When updating or expanding archetype rules for a format:

```bash
# Step 1: Run discover_archetypes to find unclassified clusters in the metagame
uv run modometa discover_archetypes --format legacy --min-cluster 4

# Step 2: Add or update the rule in archetypes/legacy.yaml (ensuring named decks have mandatory cards)

# Step 3: Run a dry run to review the impact of your rule changes
uv run modometa reclassify_decks --format legacy --dry-run

# Step 4: Run the test suite to ensure syntax and engine rules pass
uv run pytest tests/test_rules.py

# Step 5: Commit changes to the database
uv run modometa reclassify_decks --format legacy
```
