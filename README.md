# Dr_NewPaper — Research Desk / Bloomberg Terminal scientifique

Dr_NewPaper est un outil de veille et de synthèse de littérature scientifique. Il conserve le bot Telegram existant et ajoute un terminal local orienté chercheurs : ingestion multi-sources, stockage SQLite, déduplication, scoring, watchlists, fiches articles et exports.

## Installation

```bash
cd /tmp/hermy_repo/Projects/recherche
pip install -r requirements.txt
```

## Configuration

Créer/compléter `.env` :

```bash
MINIMAX_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
# Optionnel : modèle MiniMax centralisé
DR_NEWPAPER_MODEL=MiniMax-M3
# Optionnel : kill-switch opérateur du fallback Sci-Hub (activé par défaut)
# DR_NEWPAPER_ALLOW_SCIHUB=0
```

Par défaut, les PDFs essaient d'abord les sources open-access, puis Sci-Hub en
fallback — toujours actif, indépendamment de la config de recherche (la
profondeur « deepsearch » ne pilote que l'analyse IA MiniMax des études). Un
opérateur peut désactiver globalement Sci-Hub avec `DR_NEWPAPER_ALLOW_SCIHUB=0`.

## Bot Telegram

Le bot reste dans `bot.py` et conserve les commandes :

```text
/search <query> [--N] [--fr|--en|--ar] [--deep]
/deep <query>
/meta <query> [--N] [--fr|--en|--ar]
/pdf <url_or_doi>
/status
/sources
```

## Terminal Research Desk

Le terminal est dans `research_terminal.py`. Les anciennes commandes restent compatibles :

```bash
python3 research_terminal.py "minoxidil" --max 3 --sources pubmed,crossref,openalex --lang fr
python3 research_terminal.py "folliculitis minocycline" --meta --max 5 --lang fr
python3 research_terminal.py "minoxidil" --max 1 --sources pubmed --no-color
```

Nouvelles commandes Bloomberg/Research Desk :

```bash
# Accueil : DB, watchlists, top signals
python3 research_terminal.py home

# Recherche live, affichage terminal, et stockage SQLite
python3 research_terminal.py search "oral minoxidil safety" --max 10 --sources pubmed,openalex,crossref --save

# Flux trié par score
python3 research_terminal.py tape --limit 20
python3 research_terminal.py tape --query minoxidil --limit 10

# Fiche article par id ou DOI
python3 research_terminal.py article 1
python3 research_terminal.py article 10.1080/09546634.2021.1945527

# Watchlists
python3 research_terminal.py watch add dermatology "oral minoxidil" --sources pubmed,openalex --lang fr
python3 research_terminal.py watch list
python3 research_terminal.py watch run dermatology --max 5

# Digest exportable chercheurs
python3 research_terminal.py digest minoxidil --limit 50 --output-dir Dossier/minoxidil_research_dossier

# Comparaison rapide
python3 research_terminal.py compare 1 2 3
```

## Architecture livrée

Modules principaux :

- `config.py` — configuration centralisée, modèle `MiniMax-M3`, chemin DB, résolveur Sci-Hub (`scihub_enabled()`, actif par défaut).
- `normalization.py` — normalisation DOI/titre, clés canoniques, fusion métadonnées.
- `storage.py` — SQLite : articles, summaries, scores, watchlists, hits, pdfs, notes.
- `scoring.py` — score nouveauté / niveau de preuve / pertinence clinique / risque.
- `structured_summary.py` — prompt et parsing JSON pour résumés structurés.
- `research_exports.py` — export dossier Markdown, CSV evidence table, BibTeX, JSON.
- `research_terminal.py` — CLI Research Desk : home/search/tape/article/watch/digest/compare.

## Interface terminal interactive

Pour consulter les articles dans une vraie interface terminal plein écran :

```bash
python3 research_tui.py
```

Raccourcis :

```text
↑/↓ ou j/k  naviguer dans les articles / watchlists
tab         changer le focus articles/watchlists
s           rechercher des articles depuis l'interface
d           rechercher les articles récents dans un domaine
t           suivre un sujet / créer une watchlist
a           évaluer / rescorrer l'article sélectionné
r           recharger la base SQLite
e           exporter un dossier depuis l'interface
q           quitter
```

Prévisualisation non-interactive utile pour logs/tests :

```bash
python3 research_tui.py --demo
```

## Stockage

Par défaut :

```text
research_terminal.db
```

Changer la DB :

```bash
python3 research_terminal.py --db /path/to/research.db home
# ou options globales à la fin aussi acceptées :
python3 research_terminal.py home --db /path/to/research.db --no-color
```

## Exports

`digest` génère :

```text
report.md
evidence_table.csv
bibliography.bib
articles.json
```

## Tests

```bash
python3 -m unittest discover -v
python3 -m py_compile *.py sources/*.py
```

Tests ajoutés :

- `test_research_desk_core.py`
- `test_research_terminal_commands.py`
- conservation des tests existants `test_research_terminal.py`, `test_minimax_config.py`

## Notes produit

La valeur de Dr_NewPaper n’est pas seulement de trouver des papiers. La cible est un poste de pilotage de la littérature : qu’est-ce qui est nouveau, important, fiable, risqué, contradictoire, exportable et actionnable par un chercheur.
