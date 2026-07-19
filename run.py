#!/usr/bin/env python3
"""
Recherche CLI — ArXiv, PubMed, Google Scholar, CrossRef, OpenAlex, Sci-Hub
Usage:
  python run.py "quantum computing" --max 10 --output telegram,file --domain physics
  python run.py "cancer treatment" --deep --max 3  (full PDF + deep summaries)
"""
import argparse
import sys
import os
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from dispatcher import RechercheDispatcher, ALL_SOURCES
import deep_research

DOMAINES = [
    "biology", "mathematic", "physics", "games",
    "sociology", "society", "health", "ai", "cs", "chemistry", "other"
]


def main():
    parser = argparse.ArgumentParser(description="Recherche d'articles académiques")
    parser.add_argument("query", help="Requête de recherche")
    parser.add_argument("--max", type=int, default=5, help="Nombre max d'articles (défaut: 5)")
    parser.add_argument("--domain", default="other", choices=DOMAINES, help="Domaine de recherche")
    parser.add_argument("--output", default="file", choices=["telegram", "file", "both"],
                        help="Destination: telegram, file, ou both (défaut: file)")
    parser.add_argument("--sources", default="all",
                        help=f"Sources: all, {', '.join(ALL_SOURCES)} (défaut: all)")
    parser.add_argument("--telegram", action="store_true", help="Envoyer via Telegram")
    parser.add_argument("--lang", default="en", choices=["en", "fr", "ar"],
                        help="Langue des résumés (défaut: en)")
    parser.add_argument("--deep", action="store_true",
                        help="Mode approfondi: récupère PDF, lit l'étude entière, résumé deep")

    args = parser.parse_args()

    # Parse sources
    if args.sources == "all":
        sources = ALL_SOURCES.copy()
    else:
        sources = [s.strip() for s in args.sources.split(",") if s.strip() in ALL_SOURCES]

    print(f"🔍 Recherche: \"{args.query}\"")
    print(f"📂 Domaine: {args.domain}")
    print(f"📡 Sources: {', '.join(sources)}")
    print(f"📊 Max articles: {args.max}")
    print(f"📤 Output: {args.output}")
    if args.deep:
        print(f"🔬 Mode: DEEP (PDF + full-text summary)")
    print("-" * 50)

    # Deep research mode
    if args.deep:
        results = deep_research.deep_research(
            args.query,
            max_articles=args.max,
            lang=args.lang,
            sources=sources,
        )
        # Write to file
        if args.output in ["file", "both"]:
            from file_writer import write_results
            written = write_results(results, args.query)
            print(f"📁 {len(written)} fichier(s) écrit(s)")
        # Print formatted results
        output_text = deep_research.format_deep_results(results, args.query, lang=args.lang)
        print(output_text)
        # Send to Telegram
        if args.telegram or args.output in ["telegram", "both"]:
            from telegram_sender import send_results
            send_results(results, args.query, args.domain)
        print(f"\n✅ {len(results)} article(s) traité(s) en mode deep")
        return 0

    # Normal mode
    dispatcher = RechercheDispatcher(
        output_mode=args.output if not args.telegram else "both",
        lang=args.lang
    )

    results = dispatcher.search(args.query, sources=sources, max_results=args.max)

    print(f"\n✅ {len(results)} article(s) traité(s)")

    for r in results[:3]:
        print(f"  • {r['title'][:60]}...")

    if len(results) > 3:
        print(f"  ... et {len(results) - 3} autres")

    # Send to Telegram if requested
    if args.telegram or args.output in ["telegram", "both"]:
        from telegram_sender import send_results
        send_results(results, args.query, args.domain)

    return 0


if __name__ == "__main__":
    sys.exit(main())
