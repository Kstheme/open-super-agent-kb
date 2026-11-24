"""Minimal OOXML fixtures for the public M3 review CLI."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

from openkb.agent.model import ModelClient
from openkb.server.cli import main


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REQUEST = "请基于已入库的 Section 508 资料，为非网页 Word 文档生成一份无障碍自查清单。"
TABLE_RULE = "Tables repeat their header row."


def paragraph(text, *, heading=False):
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    return f"<w:p>{style}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def section(title, *quotes):
    rows = "".join(f"<w:tr><w:tc>{paragraph(quote)}</w:tc></w:tr>" for quote in quotes)
    return paragraph(title, heading=True) + f"<w:tbl>{rows}</w:tbl>"


def write_docx(path, body):
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{WORD_NAMESPACE}"><w:body>{body}</w:body></w:document>',
        )
    return path


def write_word(path, table_quotes=(TABLE_RULE,), *, prefix="",
               heading_rule="The document uses MS Word heading styles."):
    body = (
        prefix
        + section(
            "Headings",
            heading_rule,
            'Headings in the "Navigation pane" match the document\'s visual outline.',
        )
        + section("Data Tables", *table_quotes)
        + section("Images and other Objects", "Images/objects have Alt Text or described in context.")
    )
    return write_docx(path, body)


class ReviewCliCase(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "ledger.sqlite"
        self.config = self.root / "config.toml"
        self.config.write_text(
            '[model]\nbase_url = "http://fixture.invalid/v1"\n'
            'api_key = "fixture-key"\nmodel_id = "fixture-model"\n'
            '[prices]\nversion = "fixture-prices"\n',
            encoding="utf-8",
        )
        policy = write_docx(
            self.root / "508-applicability-checklist.docx",
            paragraph("☐2.4.1 Bypass Blocks (not required for non-web documents and software)"),
        )
        word = write_word(self.root / "section508-word.docx")
        self.scope_version = self.command("ingest", "add", policy, "--document", "section508-policy")
        self.word_version = self.command("ingest", "add", word, "--document", "section508-word")
        self.delivery = self.command(
            "deliver", "run", REQUEST,
            "--source", self.scope_version["id"], "--source", self.word_version["id"],
        )
        self.assertEqual(self.delivery["state"], "done")
        self.artifact_id = self.delivery["artifact"]["id"]

    def command(self, *args, expected_exit=0):
        with redirect_stdout(io.StringIO()) as output:
            status = main([*map(str, args), "--database", str(self.database)])
        self.assertEqual(status, expected_exit, output.getvalue())
        return json.loads(output.getvalue())

    def run_review(self, new_file, *args, expected_exit=0):
        return self.command(
            "review", "run", self.artifact_id, self.word_version["id"], new_file,
            *args, expected_exit=expected_exit,
        )

    def model_reply(self, answer):
        def call(client, method, path, payload, timeout):
            if method == "GET" and path == "models":
                return {"data": [{"id": client.config.model_id}]}
            self.assertEqual((method, path), ("POST", "chat/completions"))
            value = answer(payload) if callable(answer) else answer
            return {
                "choices": [{"message": {"content": json.dumps(value)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10},
            }
        return patch.object(ModelClient, "_call", autospec=True, side_effect=call)

    def new_word_block(self, path, text):
        version = self.command("ingest", "add", path, "--document", "section508-word")
        return next(block for block in self.command("ingest", "show", version["id"])["blocks"]
                    if text in block["text"])
