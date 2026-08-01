"""Tests for the bot's access control *as wired in bot.py*, not just its helpers.

`test_security_defaults.py` covers `config.is_authorized` and `config.scihub_enabled`
in isolation. That is not enough: deleting every `filters=only_operator` argument
in `main()` — the change that actually opens the bot to the world — leaves all of
those tests green. The gate lives in the handler registration and in
`button_callback`, so that is what these tests exercise.

Two layers, on purpose:

* `HandlerWiringTests` / `MainStartupGuardTests` drive the real
  `python-telegram-bot` objects, asking each registered handler's filter the same
  question the dispatcher asks at runtime. They need the declared dependency
  installed, and skip loudly when it is missing.
* `HandlerRegistrationSourceTests` parses `bot.py` with `ast` and needs no
  dependency at all, so the "every command handler carries a filter" invariant is
  still enforced on a bare checkout where the layer above would skip.
"""
import ast
import asyncio
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent
BOT_SOURCE = (REPO / "bot.py").read_text(encoding="utf-8")

try:
    import telegram
    from telegram import Chat, Message, MessageEntity, Update, User
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    import bot as bot_module
    import config
    HAVE_TELEGRAM = True
except ImportError as exc:  # pragma: no cover - depends on the environment
    HAVE_TELEGRAM = False
    _IMPORT_ERROR = exc

needs_telegram = unittest.skipUnless(
    HAVE_TELEGRAM,
    "python-telegram-bot is not installed — the handler-wiring tests cannot run. "
    "Install requirements.txt to exercise them (the ast-based tests below still do).",
)

OPERATOR_CHAT = 4242
FOREIGN_CHAT = 9999


def _message_update(chat_id, text="bonjour"):
    """A minimal but *real* Update, enough for a filter to judge it.

    Commands carry a ``bot_command`` entity: that entity, not the leading slash,
    is what ``filters.COMMAND`` looks at, so a fixture without it would describe
    a message Telegram never sends.
    """
    chat = Chat(id=chat_id, type=Chat.PRIVATE)
    user = User(id=chat_id, first_name="Test", is_bot=False)
    entities = ()
    if text.startswith("/"):
        entities = (MessageEntity(
            type=MessageEntity.BOT_COMMAND, offset=0, length=len(text.split()[0])),)
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
    )
    return Update(update_id=1, message=message)


class _RecordingApp:
    """Stands in for the Application so main() registers handlers without a network."""

    def __init__(self):
        self.handlers = []
        self.polled = False

    def add_handler(self, handler):
        self.handlers.append(handler)

    def run_polling(self, *args, **kwargs):
        self.polled = True


def _run_main_capturing_handlers(chat_id_value, token="123456:TESTTOKEN"):
    """Run bot.main() against a recording Application; returns the fake app."""
    app = _RecordingApp()
    builder = mock.MagicMock()
    builder.token.return_value.build.return_value = app
    with mock.patch.object(bot_module, "CHAT_ID", chat_id_value), \
         mock.patch.object(bot_module, "BOT_TOKEN", token), \
         mock.patch.object(bot_module, "Application") as application:
        application.builder.return_value = builder
        bot_module.main()
    return app


@needs_telegram
class MainStartupGuardTests(unittest.TestCase):
    """An unconfigured bot must refuse to run rather than answer everybody."""

    def test_missing_chat_id_aborts_before_any_handler_is_registered(self):
        # Patched here rather than through the helper: the helper patches
        # Application itself, so asserting on an outer mock would assert on an
        # object main() could never have reached — a test that cannot fail.
        with mock.patch.object(bot_module, "CHAT_ID", ""), \
             mock.patch.object(bot_module, "BOT_TOKEN", "123456:TESTTOKEN"), \
             mock.patch.object(bot_module, "Application") as application:
            with self.assertRaises(SystemExit) as caught:
                bot_module.main()
            self.assertEqual(caught.exception.code, 1)
            application.builder.assert_not_called()

    def test_the_refusal_tells_the_operator_what_to_set(self):
        # int("") would abort on its own, so the dedicated guard earns its keep
        # only through its message: someone who has never heard of a chat id has
        # to learn the variable's name and why it is refused, from this line.
        with mock.patch.object(bot_module, "CHAT_ID", ""), \
             mock.patch.object(bot_module, "BOT_TOKEN", "123456:TESTTOKEN"), \
             mock.patch.object(bot_module, "Application"), \
             self.assertLogs(bot_module._log, level="ERROR") as logged:
            with self.assertRaises(SystemExit):
                bot_module.main()
        message = "\n".join(logged.output)
        self.assertIn("TELEGRAM_CHAT_ID", message)
        self.assertRegex(message, r"(?i)refus|answer anyone")

    def test_whitespace_only_chat_id_aborts(self):
        with self.assertRaises(SystemExit):
            _run_main_capturing_handlers("   ")

    def test_placeholder_zero_chat_id_aborts(self):
        # "0" is truthy and parses, so it slips past both earlier checks; the bot
        # would then poll while matching no chat at all.
        with self.assertRaises(SystemExit):
            _run_main_capturing_handlers("0")

    def test_non_numeric_chat_id_aborts(self):
        for bad in ("moi", "abc123", "12.5", ""):
            with self.subTest(chat_id=bad), self.assertRaises(SystemExit):
                _run_main_capturing_handlers(bad)

    def test_missing_token_aborts(self):
        with self.assertRaises(SystemExit):
            _run_main_capturing_handlers(str(OPERATOR_CHAT), token="")

    def test_a_configured_bot_does_start_polling(self):
        app = _run_main_capturing_handlers(str(OPERATOR_CHAT))
        self.assertTrue(app.polled)
        self.assertTrue(app.handlers)


@needs_telegram
class HandlerWiringTests(unittest.TestCase):
    """Every registered entry point must reject a chat that is not the operator."""

    @classmethod
    def setUpClass(cls):
        cls.app = _run_main_capturing_handlers(str(OPERATOR_CHAT))
        cls.operator_update = _message_update(OPERATOR_CHAT)
        cls.foreign_update = _message_update(FOREIGN_CHAT)

    def _handlers_of(self, cls_):
        return [h for h in self.app.handlers if isinstance(h, cls_)]

    def test_every_command_handler_is_filtered_to_the_operator(self):
        handlers = self._handlers_of(CommandHandler)
        self.assertGreaterEqual(len(handlers), 8, "expected the eight bot commands")
        for handler in handlers:
            command = sorted(handler.commands)
            with self.subTest(command=command):
                self.assertIsNotNone(handler.filters, f"/{command} has no chat filter")
                self.assertFalse(
                    handler.filters.check_update(self.foreign_update),
                    f"/{command} accepts a foreign chat",
                )
                self.assertTrue(
                    handler.filters.check_update(self.operator_update),
                    f"/{command} rejects the operator",
                )

    def test_the_free_text_handler_is_filtered_to_the_operator(self):
        handlers = self._handlers_of(MessageHandler)
        self.assertEqual(len(handlers), 1)
        filters_ = handlers[0].filters
        self.assertFalse(filters_.check_update(self.foreign_update))
        self.assertTrue(filters_.check_update(self.operator_update))

    def test_the_free_text_handler_still_ignores_commands(self):
        # The chat filter must not have swallowed the original TEXT & ~COMMAND
        # condition, or every slash command would be handled twice.
        filters_ = self._handlers_of(MessageHandler)[0].filters
        self.assertFalse(filters_.check_update(_message_update(OPERATOR_CHAT, "/search x")))

    def test_no_handler_accepts_a_channel_post(self):
        # Passing filters= to CommandHandler replaces its default
        # UpdateType.MESSAGES instead of narrowing it, so gating on the chat can
        # widen the surface: a channel post has update.message = None and every
        # handler dereferences it. Uses the operator's own id, since a channel
        # the operator points at is precisely the reachable case.
        chat = Chat(id=OPERATOR_CHAT, type=Chat.CHANNEL)
        entities = (MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=7),)
        post = Message(message_id=1, date=datetime.now(timezone.utc), chat=chat,
                       text="/status x", entities=entities)
        update = Update(update_id=1, channel_post=post)
        self.assertIsNone(update.message, "fixture is not a channel post")
        for handler in self.app.handlers:
            if not hasattr(handler, "filters") or handler.filters is None:
                continue
            with self.subTest(handler=type(handler).__name__):
                self.assertFalse(handler.filters.check_update(update))

    def test_the_callback_handler_exists_and_carries_the_in_code_guard(self):
        # CallbackQueryHandler accepts no filter, which is exactly why
        # button_callback has to check authorization itself (tested below).
        self.assertEqual(len(self._handlers_of(CallbackQueryHandler)), 1)


@needs_telegram
class ButtonCallbackAuthorizationTests(unittest.TestCase):
    """Inline buttons are the one entry point a filter cannot guard."""

    def _fire(self, chat_id, data="pdf:10.1234/abc"):
        query = mock.AsyncMock()
        query.data = data
        query.message = SimpleNamespace(chat_id=chat_id)
        update = SimpleNamespace(
            callback_query=query,
            effective_chat=SimpleNamespace(id=chat_id) if chat_id is not None else None,
        )
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": str(OPERATOR_CHAT)}), \
             mock.patch.object(bot_module.pdf_sender, "download_pdf") as download:
            asyncio.run(bot_module.button_callback(update, mock.AsyncMock()))
        return query, download

    def test_a_foreign_chat_is_refused_and_downloads_nothing(self):
        query, download = self._fire(FOREIGN_CHAT)
        download.assert_not_called()
        query.edit_message_text.assert_not_called()
        query.answer.assert_awaited_once()
        self.assertIn("refus", str(query.answer.await_args).lower())

    def test_a_callback_without_a_chat_is_refused(self):
        query, download = self._fire(None)
        download.assert_not_called()

    def test_the_operator_is_let_through(self):
        query, download = self._fire(OPERATOR_CHAT)
        download.assert_called_once()

    def test_an_unconfigured_bot_privileges_nobody(self):
        # No chat is special by birth: with TELEGRAM_CHAT_ID unset there is no
        # operator to serve, so every caller — including whichever id the code
        # once defaulted to — must be turned away. Deliberately no literal id
        # here: test_security_defaults.py scans the tree for exactly that, and a
        # test proving an identifier is gone must not ship it back.
        query = mock.AsyncMock()
        query.data = "pdf:10.1234/abc"
        query.message = SimpleNamespace(chat_id=OPERATOR_CHAT)
        update = SimpleNamespace(
            callback_query=query,
            effective_chat=SimpleNamespace(id=OPERATOR_CHAT),
        )
        with mock.patch.dict(os.environ):
            os.environ.pop("TELEGRAM_CHAT_ID", None)
            with mock.patch.object(bot_module.pdf_sender, "download_pdf") as download:
                asyncio.run(bot_module.button_callback(update, mock.AsyncMock()))
        download.assert_not_called()


@needs_telegram
class AllowListAgreementTests(unittest.TestCase):
    """The allow-list is written twice; the two spellings must decide alike.

    `main()` parses TELEGRAM_CHAT_ID with `int()` and hands the result to
    `filters.Chat`, which compares numbers. `config.is_authorized` compares the
    raw strings. Any value where those disagree yields a half-open bot: the
    commands answer while the inline buttons say "Accès refusé", or the reverse
    — a failure that looks like a Telegram glitch, not a configuration error.
    """

    CONFUSABLE = ["123", " 123 ", "+123", "0123", "-1001234", "1_23"]

    def test_the_filter_and_is_authorized_agree_on_every_accepted_value(self):
        for raw in self.CONFUSABLE:
            with self.subTest(chat_id=raw):
                try:
                    parsed = int(raw)
                except ValueError:
                    continue  # main() exits on these; nothing to agree about
                filter_verdict = bool(
                    filters.Chat(chat_id=parsed).check_update(_message_update(parsed)))
                with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": raw}):
                    helper_verdict = config.is_authorized(parsed)
                self.assertEqual(
                    filter_verdict, helper_verdict,
                    f"TELEGRAM_CHAT_ID={raw!r}: commands say {filter_verdict}, "
                    f"buttons say {helper_verdict}",
                )

    def test_a_different_chat_is_refused_whatever_the_spelling(self):
        for raw in self.CONFUSABLE:
            with self.subTest(chat_id=raw):
                with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": raw}):
                    self.assertFalse(config.is_authorized(FOREIGN_CHAT))


class HandlerRegistrationSourceTests(unittest.TestCase):
    """Dependency-free backstop: the filter arguments must be present in the source.

    This duplicates part of HandlerWiringTests on purpose — that class skips when
    python-telegram-bot is absent, and a security invariant that only holds on a
    fully-provisioned machine is one nobody notices losing.
    """

    @staticmethod
    def _main_function():
        tree = ast.parse(BOT_SOURCE)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return node
        raise AssertionError("bot.py has no main()")

    def _add_handler_arguments(self):
        """The single argument of each app.add_handler(...) call inside main()."""
        for node in ast.walk(self._main_function()):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_handler"
                    and node.args):
                yield node.args[0]

    def test_every_command_handler_is_registered_with_a_filter(self):
        registered = [c for c in self._add_handler_arguments()
                      if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "CommandHandler"]
        self.assertGreaterEqual(len(registered), 8)
        for call in registered:
            command = call.args[0].value if call.args else "?"
            with self.subTest(command=command):
                self.assertIn("filters", [kw.arg for kw in call.keywords],
                              f"CommandHandler({command!r}) registered without filters=")

    def test_the_message_handler_is_registered_with_a_chat_filter(self):
        registered = [c for c in self._add_handler_arguments()
                      if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "MessageHandler"]
        self.assertEqual(len(registered), 1)
        expression = ast.unparse(registered[0].args[0])
        self.assertIn("only_operator", expression,
                      f"MessageHandler filter does not mention the operator chat: {expression}")

    def test_main_refuses_to_start_without_a_chat_id(self):
        source = ast.unparse(self._main_function())
        self.assertIn("if not CHAT_ID", source)
        self.assertIn("sys.exit(1)", source)

    def test_button_callback_checks_authorization_before_acting_on_the_payload(self):
        tree = ast.parse(BOT_SOURCE)
        func = next(n for n in tree.body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "button_callback")
        lines = ast.unparse(func).splitlines()
        guard = next(i for i, line in enumerate(lines) if "is_authorized" in line)
        payload = next(i for i, line in enumerate(lines) if "query.data" in line)
        self.assertLess(guard, payload,
                        "the authorization check must precede any use of the callback payload")


if __name__ == "__main__":
    unittest.main()
