"""Regression tests for the audit fixes (bugs found by the multi-agent review)."""
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

import telegram_sender
import research_exports
import sources.unpaywall as unpaywall
import pdf_sender


class AuditFixTests(unittest.TestCase):
    def test_send_results_accepts_domain_argument(self):
        # run.py calls send_results(results, query, domain); previously a TypeError.
        bound = inspect.signature(telegram_sender.send_results).bind([], "q", "dermatology")
        self.assertEqual(bound.args[2] if len(bound.args) > 2 else bound.kwargs.get("domain"), "dermatology")

    def test_bibtex_escapes_latex_specials(self):
        out = tempfile.mkdtemp()
        nasty = "Cost & Effect: 50% _gain_ {x} #1 $z ~t^c \\b"
        research_exports.export_research_dossier(
            out, "q", [{"title": nasty, "doi": "10.1/a_b%c", "authors": ["Aÿ B"]}])
        bib = (Path(out) / "bibliography.bib").read_text(encoding="utf-8")
        for needle in (r"\&", r"\%", r"\_", r"\{", r"\}", r"\#", r"\$",
                       r"\textasciitilde{}", r"\textasciicircum{}", r"\textbackslash{}"):
            self.assertIn(needle, bib)
        # The raw, unescaped "50% " must not survive (its % would comment the line).
        self.assertNotIn("50% ", bib)

    def test_unpaywall_bulk_sends_json_list_not_newline_string(self):
        captured = {}

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"[]"

        def fake_urlopen(req, timeout=30):
            captured["data"] = req.data
            return _FakeResp()

        original = unpaywall.urllib.request.urlopen
        unpaywall.urllib.request.urlopen = fake_urlopen
        try:
            unpaywall.get_oa_for_dois(["10.1/a", "10.2/b"])
        finally:
            unpaywall.urllib.request.urlopen = original
        self.assertEqual(json.loads(captured["data"]), {"dois": ["10.1/a", "10.2/b"]})

    def test_multipart_chat_id_and_caption_have_no_leading_crlf(self):
        # The chr(10) bug prefixed the chat_id / caption *values* with a stray CRLF.
        # Capture the real built body (no network) and assert the values are clean.
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4\n" + b"x" * 2000)  # >1000 bytes to clear the size guard
        tmp.close()
        captured = {}

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true, "result": {"message_id": 1}}'

        def fake_urlopen(req, timeout=120):
            captured["data"] = req.data
            return _FakeResp()

        orig_open = pdf_sender.urllib.request.urlopen
        orig_token = pdf_sender.BOT_TOKEN
        pdf_sender.urllib.request.urlopen = fake_urlopen
        pdf_sender.BOT_TOKEN = "test-token"
        try:
            res = pdf_sender._send_file_to_telegram(tmp.name, "x.pdf", caption="Hello", chat_id="CHAT-XYZ")
        finally:
            pdf_sender.urllib.request.urlopen = orig_open
            pdf_sender.BOT_TOKEN = orig_token
            os.unlink(tmp.name)

        self.assertTrue(res["success"])
        data = captured["data"]
        # The malformed "name=...\n\r\n" form must be gone entirely.
        self.assertNotIn(b'name="chat_id"\n', data)
        self.assertNotIn(b'name="caption"\n', data)
        # chat_id value is exactly the passed-through chat_id, with no leading CRLF.
        chat_val = data.split(b'name="chat_id"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0]
        self.assertEqual(chat_val, b"CHAT-XYZ")
        cap_val = data.split(b'name="caption"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0]
        self.assertEqual(cap_val, b"Hello")


if __name__ == "__main__":
    unittest.main()
