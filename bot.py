#!/usr/bin/env python3
"""
Dr_NewPaper Telegram Bot — Rich display + parallel search + MiniMax M2.7.
"""
import os
import re
import sys
import asyncio
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from dispatcher import RechercheDispatcher, ALL_SOURCES
import config
import deep_research
import meta_analysis as meta_mod
from telegram_sender import build_message, send_results
import pdf_sender

# ── Env loading ───────────────────────────────────────────────────────────

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# The operator chat allowed to drive this bot. No default: this file is public,
# and a baked-in id would both leak a personal identifier and hand the bot to
# whoever cloned it. main() refuses to start when it is unset.
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
_log = logging.getLogger(__name__)

# ── Config defaults ─────────────────────────────────────────────────────────

# Canonical defaults live in config.py so the bot, TUI and CLI agree on what a
# default search is. Values are unchanged from the bot's previous hardcoded ones.
DEFAULT_MAX = config.DEFAULT_MAX_RESULTS
DEFAULT_LANG = config.DEFAULT_LANG
AVAILABLE_SOURCES = ALL_SOURCES  # from dispatcher

# ── User session state ─────────────────────────────────────────────────────

_user_settings: dict[int, dict] = {}
_processed_updates: set[int] = set()
MAX_UPDATE_CACHE = 500
MAX_USER_SETTINGS = 2000


def get_settings(user_id: int) -> dict:
    if user_id not in _user_settings:
        _user_settings[user_id] = {
            "lang": DEFAULT_LANG,
            "sources": list(config.DEFAULT_SOURCES),
            "max": DEFAULT_MAX,
        }
        # Bound memory in long-running deployments: drop the oldest-inserted user
        # (dicts preserve insertion order); their settings simply reset to defaults.
        if len(_user_settings) > MAX_USER_SETTINGS:
            _user_settings.pop(next(iter(_user_settings)))
    return _user_settings[user_id]


# ── Unified flag parser ─────────────────────────────────────────────────────

_DASH_CHARS = re.compile(r'[\u2014\u2013\u00ad\u058a\u05be\u1806\u2010\-]+')


def _norm(s: str) -> str:
    """Normalize all dash variants to a single ASCII hyphen."""
    return _DASH_CHARS.sub('-', s)


def _strip_dashes(s: str) -> str:
    """Remove all leading dashes."""
    return s.lstrip('-')


def parse_meta_args(raw: str) -> dict | None:
    """
    Parse: /meta <topic> [--N] [--fr|--en|--ar] [--deep]
    Handles em-dash (—), en-dash (–), and regular hyphens (--/-).
    """
    raw = raw.strip()
    if not raw:
        return None

    tokens = raw.split()
    flag_tokens = []
    topic_tokens = []

    for tok in tokens:
        normalized = _norm(tok)
        if normalized.startswith('-'):
            flag_tokens.append(normalized)
        elif normalized in ('deep', 'd'):
            flag_tokens.append(normalized)
        else:
            topic_tokens.append(tok)

    topic = ' '.join(topic_tokens)
    if not topic:
        return None

    n_articles = 5
    lang = 'fr'
    deep_mode = False

    for f in flag_tokens:
        stripped = _strip_dashes(f)
        if stripped in ('fr', 'en', 'ar'):
            lang = stripped
        elif stripped in ('deep', 'd'):
            deep_mode = True
        elif stripped.isdigit():
            n_articles = int(stripped)

    return {
        'topic': topic,
        'n_articles': n_articles,
        'lang': lang,
        'deep_mode': deep_mode,
    }


def parse_search_args(raw: str) -> dict | None:
    """Parse: /search <query> [--N] [--fr|--en|--ar] [--deep]"""
    raw = raw.strip()
    if not raw:
        return None

    tokens = raw.split()
    flag_tokens = []
    query_tokens = []

    for tok in tokens:
        normalized = _norm(tok)
        if normalized.startswith('-'):
            flag_tokens.append(normalized)
        elif normalized in ('deep', 'd'):
            flag_tokens.append(normalized)
        else:
            query_tokens.append(tok)

    query = ' '.join(query_tokens)
    if not query:
        return None

    max_results = config.DEFAULT_MAX_RESULTS
    lang = 'fr'
    sources = None
    deep = False

    for f in flag_tokens:
        stripped = _strip_dashes(f)
        if stripped in ('fr', 'en', 'ar'):
            lang = stripped
        elif stripped in ('deep', 'd'):
            deep = True
        elif stripped.isdigit():
            max_results = int(stripped)

    return {
        'query': query,
        'max_results': max_results,
        'lang': lang,
        'sources': sources,
        'deep': deep,
    }


# ── Helpers ─────────────────────────────────────────────────────────────────


def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _esc(text: str) -> str:
    """Escape Telegram Markdown reserved characters."""
    if not text:
        return text
    for ch in ['_', '*', '`', '[', ']', '(', ')', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, '\\' + ch)
    return text


async def _send_long_message(update: Update, text: str, max_len=4096, parse_mode="Markdown"):
    if len(text) <= max_len:
        await update.message.reply_text(text, parse_mode=parse_mode)
        return
    parts = []
    for i in range(0, len(text), max_len - 100):
        parts.append(text[i:i + max_len - 100])
    for part in parts:
        await update.message.reply_text(part, parse_mode=parse_mode)
        await asyncio.sleep(0.3)


# ── Handlers ─────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧪 *Dr\\_NewPaper* — Veille scientifique automatisée\n\n"
        "Commandes disponibles :\n"
        "• /search <query> — Recherche simple\n"
        "• /deep <query> — Recherche deep (toutes sources)\n"
        "• /meta <query> — Méta-analyse complète\n"
        "• /pdf <url> — Récupère un PDF\n"
        "• /status — État du système\n"
        "• /sources — Liste des sources\n"
        "• /help — Cette aide",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    raw = update.message.text.replace("/search", "", 1).strip()

    args = parse_search_args(raw)
    if not args or not args["query"]:
        await update.message.reply_text(
            "Usage: `/search <sujet>`\n"
            "Ex: `/search cancer immunotherapy --10`\n"
            "Options: `--N` (nombre), `--fr` `--en` `--ar` (langue)\n",
            parse_mode="Markdown",
        )
        return

    s = get_settings(user_id)
    await _do_search(
        update,
        query=args["query"],
        lang=args.get("lang", s["lang"]),
        sources=s["sources"],
        max_results=args.get("max_results", s["max"]),
    )


async def cmd_meta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    raw = update.message.text.replace("/meta", "", 1).strip()

    parsed = parse_meta_args(raw)
    if not parsed:
        await update.message.reply_text(
            "Usage: `/meta <sujet> [--N] [--fr|--en|--ar] [--deep]`\n\n"
            "Exemples:\n"
            "`/meta cancer` — 5 études\n"
            "`/meta cancer --10` — 10 études\n"
            "`/meta cancer --en` — résultat en anglais\n"
            "`/meta cancer --deep` — avec PDFs",
            parse_mode="Markdown",
        )
        return

    topic = parsed["topic"]
    n = parsed["n_articles"]
    lang = parsed["lang"]
    deep_mode = parsed["deep_mode"]

    await update.message.reply_text(
        f"📊 Méta-analyse en cours...\n"
        f"   Sujet: {topic}\n"
        f"   Études: {n}\n"
        f"   Langue: {lang}\n"
        f"   Deep: {deep_mode}\n"
        f"   Cela peut prendre 1-2 minutes.",
        parse_mode="Markdown",
    )

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            meta_mod.perform_meta_analysis,
            topic,
            n,
            deep_mode,
            lang,
        )
        n_studies = result.get("n_studies", 0)
        if n_studies == 0:
            await update.message.reply_text("❌ Aucun article trouvé.")
            return

        summary = result.get("summary", "Analyse indisponible.")
        articles = result.get("articles", [])

        # Envoi header + summary
        header = f"📊 *Méta-Analyse* : {topic}\n━━━━━━━━━━━━━━━━━━━━\n"
        await _send_long_message(update, header + summary, max_len=4096, parse_mode="Markdown")

        # Envoi sources
        if articles:
            lines = ["📚 *Sources :*\n"]
            for i, a in enumerate(articles[:8], 1):
                title = (_esc(a.get("title") or "—"))[:70]
                doi = a.get("doi") or ""
                doi_str = f"DOI: `{doi[:30]}...`" if doi else ""
                lines.append(f"{i}. {title}\n   {doi_str}")
            await _send_long_message(update, "\n".join(lines), max_len=4096, parse_mode="Markdown")

    except Exception as e:
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Erreur: {e}")


async def _do_search(update: Update, query: str, lang: str,
                    sources: list, max_results: int):
    """Full rich display — one message per result with summary + PDF button."""
    await update.message.reply_text(
        f"🔍 Recherche en cours...\n"
        f"   Sujet: {query[:50]}\n"
        f"   Sources: {', '.join(sources) if sources else 'toutes'}\n"
        f"   Max: {max_results}",
    )

    try:
        dispatcher = RechercheDispatcher(output_mode="file", lang=lang)
        valid = set(AVAILABLE_SOURCES)
        filtered = [src for src in sources if src in valid] if sources else list(valid)
        results = dispatcher.search(query, sources=filtered, max_results=max_results)

        if not results:
            await update.message.reply_text("❌ Aucun résultat trouvé.")
            return

        # Send each result with rich formatting + PDF button
        for i, r in enumerate(results):
            try:
                if not isinstance(r, dict):
                    continue
                title = (r.get("title") or "Unknown")[:85]
                source = r.get("source", "") or ""
                date = r.get("date", "") or ""
                authors = (r.get("authors") or [])[:3]
                doi = r.get("doi", "") or ""
                journal = r.get("journal", "") or ""
                summary = r.get("summary") or ""
                oa_pdf_url = r.get("oa_pdf_url", "") or ""

                # Build result text
                lines = [f"*{i+1}. {title}*"]
                meta_parts = [p for p in [source, date, journal] if p]
                if meta_parts:
                    lines.append(f"   📖 {' | '.join(meta_parts)}")
                if authors:
                    lines.append(f"   👥 {', '.join(authors)}")
                if doi:
                    lines.append(f"   🔑 DOI: `{doi}`")
                if summary:
                    lines.append(f"\n{summary[:300]}{'...' if len(summary) > 300 else ''}")
                elif r.get("abstract"):
                    abstract = re.sub(r"<[^>]+>", "", r.get("abstract", ""))
                    lines.append(f"\n_{abstract[:200]}..._")

                text = "\n".join(lines)

                # Build inline PDF button
                keyboard = []
                # Telegram caps callback_data at 64 bytes; skip the button for
                # over-long DOIs rather than shipping one that fails silently.
                if doi and len(f"pdf:{doi}".encode("utf-8")) <= 64:
                    keyboard.append(
                        [InlineKeyboardButton("📄 Télécharger PDF", callback_data=f"pdf:{doi}")]
                    )
                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

                await update.message.reply_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )

                await asyncio.sleep(0.8)

            except Exception as ex:
                _log.error(f"Error on result[{i}]: {ex}")
                continue

    except Exception as e:
        import traceback
        traceback.print_exc()
        _log.error(f"Search error: {e}")
        await update.message.reply_text(f"❌ Erreur: {e}")


async def _do_deep(update: Update, query: str, lang: str,
                  sources: list, max_results: int):
    """Deep search — all sources, PDF download, full-text summary."""
    await update.message.reply_text(
        f"🔬 Deep search en cours...\n"
        f"   Sujet: {query[:50]}\n"
        f"   Toutes sources, PDFs, deep summaries\n"
        f"   Cela peut prendre 2-5 minutes...",
    )

    try:
        import time as _time
        t0 = _time.time()

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            deep_research.deep_research,
            query,
            max_results,
            lang,
            sources,
        )

        elapsed = _time.time() - t0
        if not results:
            await update.message.reply_text("❌ Aucun résultat deep trouvé.")
            return

        await update.message.reply_text(
            f"✅ {len(results)} résultats deep en {elapsed:.0f}s.\n"
            f"Envoi en cours...",
        )

        for i, r in enumerate(results[:5]):
            if not isinstance(r, dict):
                continue
            title = (r.get("title") or "Unknown")[:85]
            source = r.get("source", "") or ""
            date = r.get("date", "") or ""
            method = r.get("summary_method", "abstract") or "abstract"
            pdf_tag = "📄" if r.get("pdf_available") else "🔒"

            lines = [
                f"*{i+1}. {title}*",
                f"   {pdf_tag} {source} | 📅 {date}",
                f"   📊 Méthode: {method}",
            ]
            # Send the FULL deep summary (the 7-section critical appraisal +
            # verdict runs 300-550 words ≈ 2-3.5k chars). Truncating to 250 here
            # would drop the entire evaluation the user asked for, so chunk it
            # across Telegram's 4096-char limit via _send_long_message instead.
            body = r.get("deep_summary") or r.get("summary") or ""
            if body:
                lines.append(f"\n{body}")

            text = "\n".join(lines)
            await _send_long_message(update, text, parse_mode="Markdown")
            await asyncio.sleep(0.5)

    except Exception as e:
        import traceback
        traceback.print_exc()
        await update.message.reply_text(f"❌ Erreur deep: {e}")


async def cmd_deep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    raw = update.message.text.replace("/deep", "", 1).strip()

    args = parse_search_args(raw)
    if not args or not args["query"]:
        await update.message.reply_text("Usage: /deep <sujet> [--N] [--fr|--en|--ar]")
        return

    s = get_settings(user_id)
    await _do_deep(
        update,
        query=args["query"],
        lang=args.get("lang", s["lang"]),
        sources=ALL_SOURCES,
        max_results=args.get("max_results", config.DEFAULT_MAX_RESULTS),
    )


async def cmd_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = "📡 *Sources actives :*\n" + "\n".join(f"• {s}" for s in AVAILABLE_SOURCES)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *Dr\\_NewPaper* — Opérationnel\n"
        "Polling Telegram actif.\n"
        f"Sources: {len(AVAILABLE_SOURCES)}\n"
        f"Defaut lang: fr",
        parse_mode="Markdown",
    )


async def cmd_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = " ".join(ctx.args).strip()
    if not args:
        await update.message.reply_text("Usage: /pdf <doi>\nEx: /pdf 10.1038/s41591-024-03018-2")
        return
    # Placeholder — call pdf_sender directly
    await update.message.reply_text(f"📄 PDF pour DOI: {args}\n(Téléchargement en cours...)")
    try:
        loop = asyncio.get_event_loop()
        dl_result = await loop.run_in_executor(None, pdf_sender.download_pdf, args)
        if dl_result.get("success") and dl_result.get("path"):
            await update.message.reply_text(f"✅ PDF prêt: {dl_result['path']}")
        elif dl_result.get("error"):
            await update.message.reply_text(f"❌ Erreur PDF: {dl_result['error']}")
        else:
            await update.message.reply_text("❌ PDF non disponible.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur: {e}")


async def button_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # CallbackQueryHandler takes no chat filter, so the allow-list is enforced
    # here — otherwise the inline buttons would be an unguarded way in.
    chat = update.effective_chat
    if not config.is_authorized(chat.id if chat else None):
        _log.warning("Callback refusé pour le chat %s", chat.id if chat else "?")
        await query.answer(text="Accès refusé.", show_alert=True)
        return
    await query.answer()
    data = query.data or ""

    if data.startswith("pdf:"):
        doi = data[4:]
        chat_id = query.message.chat_id if query.message else None
        await query.answer(text="Téléchargement en cours...", show_alert=False)
        try:
            loop = asyncio.get_event_loop()
            dl_result = await loop.run_in_executor(None, pdf_sender.download_pdf, doi)
            if dl_result.get("success") and dl_result.get("path"):
                pdf_path = dl_result["path"]
                method = dl_result.get("method", "unknown")
                with open(pdf_path, "rb") as f:
                    if chat_id:
                        await ctx.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            filename=os.path.basename(pdf_path),
                            caption=f"📄 PDF (via {method})",
                        )
                    else:
                        await query.edit_message_text("✅ PDF prêt mais impossible d'envoyer — chat introuvable.")
            elif dl_result.get("error"):
                await query.edit_message_text(f"❌ PDF indisponible: {dl_result['error']}")
            else:
                await query.edit_message_text("❌ PDF non disponible pour ce DOI.")
        except Exception as e:
            await query.edit_message_text(f"❌ Erreur: {e}")
    else:
        await query.edit_message_text("Action non reconnue.")


async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    uid = update.update_id
    if uid in _processed_updates:
        return
    _processed_updates.add(uid)
    # Telegram update_ids increase monotonically, so the smallest is the oldest;
    # evict it directly instead of sorting the whole set each time.
    while len(_processed_updates) > MAX_UPDATE_CACHE:
        _processed_updates.discard(min(_processed_updates))

    # Natural language → search
    user_id = update.message.from_user.id
    s = get_settings(user_id)
    await _do_search(update, query=text, lang=s["lang"], sources=s["sources"], max_results=s["max"])


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        _log.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    if not CHAT_ID:
        _log.error(
            "TELEGRAM_CHAT_ID not set in .env — refusing to start. Telegram bots "
            "are discoverable by name, so without an operator chat this bot would "
            "answer anyone: every /deep spends your MiniMax quota and every /pdf "
            "downloads from your IP."
        )
        sys.exit(1)
    try:
        operator_chat = int(CHAT_ID)
    except ValueError:
        _log.error("TELEGRAM_CHAT_ID must be a numeric chat id, got %r", CHAT_ID)
        sys.exit(1)
    if operator_chat == 0:
        # Placeholder rather than a chat: no Telegram chat has id 0, so the bot
        # would poll happily and answer nobody — the failure this guard exists
        # to prevent, wearing a running bot's face.
        _log.error("TELEGRAM_CHAT_ID=0 is a placeholder, not a chat id.")
        sys.exit(1)

    _log.info(f"Starting Dr_NewPaper_bot (token: ...{BOT_TOKEN[-10:]})")

    app = Application.builder().token(BOT_TOKEN).build()

    # Every entry point is gated on the operator chat. CallbackQueryHandler
    # accepts no filter, so button_callback checks config.is_authorized itself.
    #
    # UpdateType.MESSAGES is re-stated because passing ``filters=`` to
    # CommandHandler *replaces* its default rather than narrowing it: without
    # this, gating on the chat would also start accepting channel posts, whose
    # update.message is None — every handler below dereferences it.
    only_operator = filters.Chat(chat_id=operator_chat) & filters.UpdateType.MESSAGES
    app.add_handler(CommandHandler("start", cmd_start, filters=only_operator))
    app.add_handler(CommandHandler("help", cmd_help, filters=only_operator))
    app.add_handler(CommandHandler("search", cmd_search, filters=only_operator))
    app.add_handler(CommandHandler("deep", cmd_deep, filters=only_operator))
    app.add_handler(CommandHandler("meta", cmd_meta, filters=only_operator))
    app.add_handler(CommandHandler("sources", cmd_sources, filters=only_operator))
    app.add_handler(CommandHandler("status", cmd_status, filters=only_operator))
    app.add_handler(CommandHandler("pdf", cmd_pdf, filters=only_operator))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & only_operator, message_handler))

    _log.info("Dr_NewPaper initialized. Bot is running.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()