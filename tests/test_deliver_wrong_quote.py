from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from xml.sax.saxutils import escape
from zipfile import ZipFile

from openkb.ingest.claims import compliance_claim
from openkb.ingest.sqlite import SqliteStore
from openkb.server.cli import main


WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
HEADING_QUOTE = "The document uses MS Word heading styles."
REQUEST = "请基于已入库的 Section 508 资料，为非网页 Word 文档生成一份无障碍自查清单。"


def _paragraph(text, *, heading=False):
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    return f"<w:p>{style}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def _section(title, *quotes):
    rows = "".join(f"<w:tr><w:tc>{_paragraph(quote)}</w:tc></w:tr>" for quote in quotes)
    return _paragraph(title, heading=True) + f"<w:tbl>{rows}</w:tbl>"


def _write_docx(path, body):
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{WORD_NAMESPACE}"><w:body>{body}</w:body></w:document>',
        )


class WrongQuoteDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "ledger.sqlite"

    def command(self, *args, expected_exit=0):
        with redirect_stdout(io.StringIO()) as output:
            status = main([*args, "--database", str(self.database)])
        report = json.loads(output.getvalue())
        self.assertEqual(status, expected_exit, report.get("state"))
        return report

    def test_a_locatable_heading_quote_cannot_resolve_the_missing_table_rule(self):
        scope = self.root / "508-applicability-checklist.docx"
        word = self.root / "section508-word.docx"
        _write_docx(scope, _paragraph("☐2.4.1 Bypass Blocks (not required for non-web documents and software)"))
        _write_docx(
            word,
            _section(
                "Headings",
                HEADING_QUOTE,
                'Headings in the "Navigation pane" match the document\'s visual outline.',
            )
            + _section("Data Tables", "Tables are not pictures.")
            + _section("Images and other Objects", "Images/objects have Alt Text or described in context."),
        )
        scope_id = self.command("ingest", "add", str(scope))["id"]
        word_id = self.command("ingest", "add", str(word))["id"]
        word_version = self.command("ingest", "show", word_id)
        heading_block, = (block for block in word_version["blocks"] if block["text"] == HEADING_QUOTE)
        stored = self.command("ingest", "evidence-add", word_id, heading_block["id"], "--quote", HEADING_QUOTE)
        store = SqliteStore(self.database)
        wrong_claim = compliance_claim(
            word_id, "有数据表格时，表头行应设置为重复。", (store.evidence(stored["id"]),),
        )
        store.save_claims((wrong_claim,))
        saved, = self.command("claims", "list", word_id)["claims"]
        self.assertEqual(saved["id"], wrong_claim.id)
        located = self.command("ingest", "evidence-show", stored["id"])
        self.assertEqual(located["version_id"], word_id)
        self.assertEqual(located["quote"], HEADING_QUOTE)
        self.assertEqual((located["locator"]["table"], located["locator"]["row"]), (1, 1))

        result = self.command(
            "deliver", "run", REQUEST, "--source", scope_id, "--source", word_id,
            "--max-steps", "6", expected_exit=1,
        )

        self.assertEqual(result["state"], "paused")
        self.assertEqual(result["pause_reason"], "step budget exhausted")
        self.assertEqual(result["budget"]["steps"], 6)
        self.assertEqual(result["gaps"], [{"id": "data_tables", "title": "数据表格"}])
        self.assertEqual([item["slot_id"] for item in result["covered"]],
                         ["scope", "headings", "image_descriptions"])
        self.assertNotIn(wrong_claim.id, [item["claim_id"] for item in result["covered"]])
        self.assertIsNone(result["artifact"])
        self.assertEqual(self.command("deliver", "show", result["id"]), result)
