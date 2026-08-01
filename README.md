# Dr_NewPaper — Research Desk / Bloomberg Terminal scientifique

Dr_NewPaper est un outil de veille et de synthèse de littérature scientifique. Il conserve le bot Telegram existant et ajoute un terminal local orienté chercheurs : ingestion multi-sources, stockage SQLite, déduplication, scoring, watchlists, fiches articles et exports.

## Installation

```bash
git clone https://github.com/ZakiChair/dr-newpaper.git
cd dr-newpaper
pip install -r requirements.txt
```

## Configuration

Créer/compléter `.env` :

```bash
MINIMAX_API_KEY=...
TELEGRAM_BOT_TOKEN=...
# Obligatoire : seul ce chat peut piloter le bot. Sans lui, bot.py refuse de démarrer.
TELEGRAM_CHAT_ID=...
# Obligatoire uniquement si le chat ci-dessus est un groupe ou un canal (id négatif) :
# les identifiants d'utilisateurs autorisés à piloter le bot, séparés par des virgules.
# TELEGRAM_ALLOWED_USERS=123456789,987654321
# Optionnel : modèle MiniMax, pour TOUS les appels — résumés, analyses
# approfondies, méta-analyses et digests.
DR_NEWPAPER_MODEL=MiniMax-M3
```

Le fichier est lu par les entrées Python (`config.parse_env_line`) et par les
scripts cron (`lib.sh`), qui appliquent les **mêmes règles** : découpe au
premier `=`, espaces retirés de part et d'autre, puis une paire de guillemets
englobants si elle est présente. Une valeur peut donc contenir des espaces, un
`=` ou un `#`. Une variable **déjà exportée l'emporte** sur le fichier, ce qui
permet d'essayer une valeur le temps d'une commande.

### Contrôle d'accès du bot

`TELEGRAM_CHAT_ID` n'est pas seulement la destination des messages : c'est la
**liste d'autorisation**. Les bots Telegram se trouvent par leur nom, et chaque
commande dépense votre quota MiniMax et sort par votre adresse IP — un bot
ouvert est un bot que des inconnus font travailler à vos frais. Les commandes
venues d'un autre chat sont ignorées, et les boutons en ligne répondent
« Accès refusé ».

Un chat désigne un **lieu**, et un groupe est un lieu où d'autres peuvent
entrer. En conversation privée la distinction est vide : Telegram donne au chat
l'identifiant de son unique occupant, donc rien de plus n'est requis.

Si `TELEGRAM_CHAT_ID` désigne un groupe ou un canal — tentant, puisque la même
variable sert aussi de destination aux digests, et leurs identifiants sont
négatifs — il faut alors nommer les personnes autorisées :

```bash
TELEGRAM_ALLOWED_USERS=123456789,987654321
```

Sans cette liste, le bot **refuse de démarrer** sur un chat partagé plutôt que
de se laisser piloter par quiconque y est ajouté, aujourd'hui ou plus tard.
La variable est ignorée pour un chat privé.

### Récupération des PDF

Les PDF sont cherchés dans les sources open-access (Unpaywall, Europe PMC,
CORE, bioRxiv, arXiv). Ce chemin est le seul actif par défaut.

Le code contient aussi un repli Sci-Hub, **désactivé par défaut et laissé à la
responsabilité de l'opérateur** : y télécharger un article sous paywall
enfreint le droit d'auteur dans la plupart des juridictions. Il ne s'active que
par un geste explicite :

```bash
DR_NEWPAPER_ALLOW_SCIHUB=1
```

Toute autre valeur — y compris une faute de frappe ou une variable vide — le
laisse désactivé.

Le réglage ne gouverne que les *téléchargements*. Les PDF déjà récupérés
restent dans le cache `/tmp/drnewpaper_pdfs` et sont resservis sans passer par
cette garde : si vous désactivez Sci-Hub après vous en être servi, videz le
cache pour ne plus en voir les fichiers.

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

**Modifier un message déjà envoyé ne relance rien** : renvoyez la commande.
Telegram traite une correction comme un événement à part, et la rejouer ferait
repartir une recherche `/deep` — donc dépenser le quota MiniMax — pour une
faute de frappe.

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

- `config.py` — configuration centralisée, modèle `MiniMax-M3`, chemin DB, racine unique de la bibliothèque (`DOSSIER_BASE`), liste d'autorisation du bot (`is_authorized()`), résolveur Sci-Hub (`scihub_enabled()`, désactivé par défaut).
- `lib.sh` — préambule partagé des scripts cron : lecture de `.env` (mêmes règles que les chargeurs Python).
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

Articles, PDF, méta-analyses et dossiers exportés atterrissent sous `Dossier/`
**dans le dépôt**, quel que soit le répertoire d'où vous lancez l'outil. Trois
échappatoires : `--output-dir` sur le CLI, et les variables
`DR_NEWPAPER_PDF_DIR` / `DR_NEWPAPER_META_DIR`. Le cache de téléchargement des
PDF reste dans `/tmp`, où il est censé être volatil.

## Tests

```bash
python3 -m unittest discover -v
python3 -m py_compile *.py sources/*.py
```

Sans les dépendances de `requirements.txt`, la suite passe au vert en
**ignorant** les tests qui pilotent le bot (ceux de `test_bot_authorization.py`
qui importent `python-telegram-bot`). Un contrôle d'accès non exécuté est un
contrôle d'accès non vérifié : installez les dépendances avant de conclure que
la suite valide le bot. Le reste du fichier repose sur `ast` et s'exécute
partout.

Tests ajoutés :

- `test_security_defaults.py` — Sci-Hub opt-in, aucun identifiant personnel en dur, chemins internes au dépôt
- `test_bot_authorization.py` — contrôle d'accès tel qu'il est câblé dans `bot.py`
- `test_research_desk_core.py`
- `test_research_terminal_commands.py`
- conservation des tests existants `test_research_terminal.py`, `test_minimax_config.py`

## Notes produit

La valeur de Dr_NewPaper n’est pas seulement de trouver des papiers. La cible est un poste de pilotage de la littérature : qu’est-ce qui est nouveau, important, fiable, risqué, contradictoire, exportable et actionnable par un chercheur.
