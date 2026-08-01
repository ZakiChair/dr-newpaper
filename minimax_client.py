"""MiniMax client for article summarization.

Single, robust entrypoint (:func:`chat`) shared by the summarizer, the
meta-analysis and the deep-research pipelines.

Why this module is careful about "thinking":
    MiniMax M-series models (M1/M2/M3) emit a `<think>...</think>` reasoning
    trace that is billed against ``max_tokens``. With a small budget the whole
    budget is spent reasoning, ``finish_reason`` comes back as ``length`` and the
    *visible* answer is empty — which used to surface as "meta-analysis doesn't
    work" / "deep summary not generated". The only knob that actually disables
    the trace (verified against the live API) is ``thinking={"type":"disabled"}``;
    ``thinking_token`` and ``reasoning_effort`` are silently ignored. So every
    payload we build defaults to thinking disabled, and we still strip any stray
    `<think>` / `『』` wrapper defensively.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import json
import time
import logging
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Any

import config

logger = logging.getLogger(__name__)

MINIMAX_API = "https://api.minimax.io/v1/chat/completions"
# The model every call here defaults to. Taken from config rather than repeated:
# this constant is what actually reaches the API payload, so while it was a
# separate literal, DR_NEWPAPER_MODEL could only ever change the name in
# send_digest's payload — the summaries and meta-analyses kept calling M3.
DEFAULT_MODEL = config.DEFAULT_MODEL

PROMPT_TEMPLATE = """Résume cet article académique en 3 phrases maximum. Focus sur : contribution principale, méthode, et résultats clés.

Title: {title}
Authors: {authors}
Source: {source}
Date: {date}
Abstract: {abstract}

Summary (en français):"""

PROMPT_TEMPLATE_EN = """Summarize this academic article in 3 sentences maximum. Focus on: main contribution, method, and key findings.

Title: {title}
Authors: {authors}
Source: {source}
Date: {date}
Abstract: {abstract}

Summary (in English):"""


def _get_key():
    """Return the existing MiniMax API key without replacing or hardcoding it.

    The .env fallback that used to live here is gone: importing config now reads
    the file into the environment before this module is even defined, so the
    branch could never fire again. It also stripped quotes one at a time rather
    than as a matching pair, which made it a fourth reader of the format.
    """
    return os.getenv("MINIMAX_API_KEY", "").strip()


def _strip_think(content: str) -> str:
    """Remove MiniMax reasoning wrappers (both `『』` and `<think>` styles)."""
    content = re.sub(r"『[^』]*』", "", content or "")
    content = re.sub(r"<think>[\s\S]*?</think>\s*", "", content)
    # A truncated response can leave a dangling, never-closed <think> — drop it.
    content = re.sub(r"<think>[\s\S]*$", "", content)
    return content.strip()


def _build_payload(prompt: str, model: str = DEFAULT_MODEL,
                   temperature: float = 0.3, max_tokens: int = 1000,
                   disable_thinking: bool = True) -> Dict:
    """Build a MiniMax chat payload for Dr_NewPaper summaries.

    ``disable_thinking`` keeps the model from spending the whole token budget on
    its `<think>` trace (see the module docstring).
    """
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "bot_setting": [
            {
                "bot_name": "Dr_NewPaper",
                "content": "You are a rigorous medical and academic research assistant. Summarize only from the provided study text; never invent statistics.",
            }
        ],
    }
    if disable_thinking:
        payload["thinking"] = {"type": "disabled"}
    return payload


def _post(payload: Dict, timeout: int = 90) -> Dict[str, Any]:
    """POST a payload to MiniMax. Returns {"content": str} or {"error": str}.

    Unlike the old code paths this *reads the HTTP error body* and inspects
    MiniMax's ``base_resp`` (which carries logical errors — bad key, balance,
    rate limit — even on HTTP 200), so failures stop being silent.
    """
    key = _get_key()
    if not key:
        return {"error": "MINIMAX_API_KEY non configurée"}

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(MINIMAX_API, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return {"error": f"HTTP {exc.code}: {detail}".strip(), "status": exc.code}
    except Exception as exc:  # URLError, timeout, socket errors
        return {"error": f"{type(exc).__name__}: {exc}"}

    if not body:
        return {"error": "réponse vide (HTTP)"}
    try:
        result = json.loads(body)
    except Exception as exc:
        return {"error": f"réponse non-JSON: {exc}"}

    # MiniMax reports logical errors here even when HTTP is 200.
    base = result.get("base_resp") or {}
    code = base.get("status_code", 0)
    if code not in (0, None):
        return {"error": f"MiniMax {code}: {base.get('status_msg', 'erreur')}"}

    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        return {"error": "aucune réponse du modèle (choices vide)"}
    first = choices[0]
    if not isinstance(first, dict):
        return {"error": "structure de réponse invalide"}
    msg = first.get("message") or {}
    raw = msg.get("content") or msg.get("reasoning_content") or ""
    content = _strip_think(raw)
    if not content:
        # Thinking-only response (no visible answer) — usually a too-small budget.
        return {"error": "réponse sans contenu visible (budget épuisé par le raisonnement ?)",
                "finish_reason": first.get("finish_reason", "")}
    return {"content": content}


# Transient conditions worth retrying (rate-limit / server / network hiccups).
_RETRYABLE = ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
              "TimeoutError", "URLError", "timed out", "Connection")


def chat(user: str, system: Optional[str] = None, *,
         model: str = DEFAULT_MODEL, temperature: float = 0.3,
         max_tokens: int = 2000, disable_thinking: bool = True,
         bot_setting: Optional[List[Dict]] = None,
         retries: int = 2, backoff: float = 4.0, timeout: int = 90) -> str:
    """Single MiniMax chat entrypoint.

    Returns the clean assistant text, or a ``"[Erreur: ...]"`` string the UI can
    show verbatim. Retries transient failures with a linear backoff.
    """
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if disable_thinking:
        payload["thinking"] = {"type": "disabled"}
    if bot_setting:
        payload["bot_setting"] = bot_setting

    last_error = ""
    for attempt in range(retries + 1):
        res = _post(payload, timeout=timeout)
        if "content" in res:
            return res["content"]
        last_error = res.get("error", "erreur inconnue")
        if attempt < retries and any(tok in last_error for tok in _RETRYABLE):
            logger.warning("MiniMax transient error (retry %d): %s", attempt + 1, last_error)
            time.sleep(backoff * (attempt + 1))
            continue
        break
    return f"[Erreur MiniMax: {last_error}]"


def summarize_article(article: Dict, lang: str = "en", model: str = DEFAULT_MODEL) -> str:
    """Summarize a single article using MiniMax M3 by default."""
    if not _get_key():
        return "[MiniMax API key not configured — set MINIMAX_API_KEY]"

    if not isinstance(article, dict):
        return "[Invalid article: not a dict]"

    if article.get("error"):
        return f"[Error: {article['error']}]"

    if not article.get("abstract"):
        return "Summary: Abstract not available."

    title_val = article.get("title") or "Unknown"
    authors_val = article.get("authors") or []
    if isinstance(authors_val, list):
        authors_val = ", ".join(authors_val)
    source_val = article.get("source") or "Unknown"
    date_val = article.get("date") or "Unknown"
    abstract_val = article.get("abstract") or ""
    if not isinstance(abstract_val, str):
        abstract_val = str(abstract_val)

    template = PROMPT_TEMPLATE if lang == "fr" else PROMPT_TEMPLATE_EN
    prompt = template.format(
        title=title_val,
        authors=authors_val,
        source=source_val,
        date=date_val,
        abstract=abstract_val[:4000],
    )
    payload = _build_payload(prompt, model=model, temperature=0.3, max_tokens=1000)
    res = _post(payload, timeout=60)
    if "content" in res:
        return res["content"]
    return f"[MiniMax error: {res.get('error', 'unknown')}]"


def summarize_batch(articles: List[Dict], lang: str = "en", max_workers: int = 5,
                    model: str = DEFAULT_MODEL) -> List[Dict]:
    """Add summary field to each article using parallel MiniMax calls."""
    if not articles:
        return articles

    def _summarize_one(article: Dict) -> Dict:
        article["summary"] = summarize_article(article, lang, model=model)
        return article

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_summarize_one, a) for a in articles]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                # _summarize_one already swallows API errors into a string, so this
                # is a true unexpected failure — surface it instead of hiding it.
                logger.warning("Article summarization failed in batch", exc_info=True)

    # _summarize_one mutates each article in place, so the original list already
    # holds the summaries in the right order — no O(n²) id() re-matching needed.
    return articles
