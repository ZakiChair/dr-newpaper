"""ArXiv source — uses official export.arxiv.org API."""

import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Dict

ARXIV_API = "http://export.arxiv.org/api/query"

def search_arxiv(query: str, max_results: int = 5) -> List[Dict]:
    """Search ArXiv by query, return list of article dicts."""
    params = {
        "search_query": f"all:{query}",
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read().decode("utf-8")
    except Exception as e:
        return [{"error": f"ArXiv API failed: {e}"}]

    root = ET.fromstring(data)
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

    results = []
    for entry in root.findall("atom:entry", ns):
        title = entry.find("atom:title", ns)
        summary = entry.find("atom:summary", ns)
        published = entry.find("atom:published", ns)
        link = entry.find("atom:id", ns)
        authors = [a.find("atom:name", ns).text for a in entry.findall("atom:author", ns) if a.find("atom:name", ns) is not None]

        results.append({
            "source": "arXiv",
            "title": title.text.strip().replace("\n", " ") if title is not None else "Unknown",
            "abstract": summary.text.strip() if summary is not None else "",
            "date": published.text[:10] if published is not None else "",
            "url": link.text if link is not None else "",
            "authors": authors[:3],
            "doi": "",
        })

    return results
