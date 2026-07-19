"""Google Scholar source — uses scholarly library."""

from typing import List, Dict
import time

def search_scholar(query: str, max_results: int = 5) -> List[Dict]:
    """Search Google Scholar by query, return list of article dicts."""
    try:
        from scholarly import scholarly
    except ImportError:
        return [{"error": "scholarly not installed: pip install scholarly"}]

    results = []
    try:
        search = scholarly.search_pubs(query)
        for i, pub in enumerate(search):
            if i >= max_results:
                break
            results.append({
                "source": "Google Scholar",
                "title": pub.get("bib", {}).get("title", "Unknown"),
                "abstract": pub.get("bib", {}).get("abstract", ""),
                "date": pub.get("bib", {}).get("pub_year", ""),
                "url": pub.get("pub_url", "") or f"https://scholar.google.com/scholar?q={query}",
                "authors": pub.get("bib", {}).get("author", [])[:3],
                "doi": pub.get("bib", {}).get("doi", ""),
            })
            time.sleep(1)  # be polite
    except Exception as e:
        return [{"error": f"Scholar search failed: {e}"}]

    return results
