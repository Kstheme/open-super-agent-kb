from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from openkb.ingest.claims import compliance_claim
from openkb.ingest.ledger import EvidenceValidator
from openkb.ingest.sqlite import SqliteStore
from openkb.profiles.word_accessibility import find_coverage
from openkb.server.cli import main


WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REQUEST = "请基于已入库的 Section 508 资料，为非网页 Word 文档生成一份无障碍自查清单。"


class DeliverCliTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.database = self.root / "ledger.db"

    def command(self, *args, expected_exit=0):
        with redirect_stdout(io.StringIO()) as output:
            status = main([*args, "--database", str(self.database)])
        self.assertEqual(status, expected_exit)
        return json.loads(output.getvalue())

    def scope_version(self):
        path = self.root / "508-applicability-checklist.docx"
        quote = "2.4.1 Bypass Blocks (not required for non-web documents and software)"
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                f'<w:document xmlns:w="{WORD}"><w:body>'
                f'<w:p><w:r><w:t>☐{quote}</w:t></w:r></w:p>'
                '</w:body></w:document>',
            )
        return self.command("ingest", "add", str(path))["id"], quote

    def test_budget_pause_persists_three_gaps_and_a_locatable_scope(self):
        version, quote = self.scope_version()
        result = self.command(
            "deliver", "run", REQUEST, "--source", version, "--max-steps", "4",
            expected_exit=1,
        )

        self.assertEqual(result["kind"], "deliver")
        self.assertEqual(result["state"], "paused")
        self.assertEqual(result["pause_reason"], "step budget exhausted")
        self.assertEqual(result["budget"]["steps"], 4)
        self.assertEqual([slot["title"] for slot in result["skeleton"]],
                         ["适用范围", "标题结构", "数据表格", "图片说明"])
        self.assertEqual([slot["title"] for slot in result["gaps"]], ["标题结构", "数据表格", "图片说明"])
        self.assertIsNone(result["artifact"])
        scope, = result["covered"]
        self.assertEqual(scope["slot_id"], "scope")
        claim, = self.command("claims", "list", version)["claims"]
        self.assertEqual(scope["claim_id"], claim["id"])
        evidence, = scope["evidence"]
        self.assertEqual(evidence["id"], claim["evidence"][0]["id"])
        located = self.command("ingest", "evidence-show", evidence["id"])
        self.assertEqual(located["version_id"], version)
        self.assertEqual(located["quote"], quote)
        self.assertEqual(located["locator"]["paragraph"], 1)
        self.assertEqual(self.command("deliver", "show", result["id"]), result)

    def test_pending_proposal_does_not_change_current_or_next_skeleton(self):
        version, _ = self.scope_version()
        args = ("deliver", "run", REQUEST, "--source", version, "--max-steps", "1")
        first = self.command(*args, expected_exit=1)

        proposed = self.command("deliver", "propose", first["id"], "文字对比度")
        self.assertEqual(proposed["state"], "proposed")
        self.assertEqual(proposed["title"], "文字对比度")
        reloaded = self.command("deliver", "show", first["id"])
        self.assertEqual(reloaded["skeleton_proposals"], [proposed])
        self.assertEqual(reloaded["skeleton"], first["skeleton"])
        self.assertEqual(reloaded["gaps"], first["gaps"])

        next_task = self.command(*args, expected_exit=1)
        self.assertNotEqual(first["id"], next_task["id"])
        self.assertEqual(next_task["skeleton"], first["skeleton"])
        self.assertEqual(next_task["gaps"], first["gaps"])
        self.assertEqual(next_task["skeleton_proposals"], [])

    def word_version(self):
        path = self.root / "section508-word.docx"
        sections = (
            ("Headings", (
                "The document uses MS Word heading styles.",
                "Headings in the \"Navigation pane\" match the document's visual outline.",
            )),
            ("Data Tables", ("Tables repeat their header row.",)),
            ("Images and other Objects", ("Images/objects have Alt Text or described in context.",)),
        )
        body = ""
        for title, quotes in sections:
            body += (
                '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                f"<w:r><w:t>{title}</w:t></w:r></w:p><w:tbl>"
            )
            for quote in quotes:
                body += f"<w:tr><w:tc><w:p><w:r><w:t>{quote}</w:t></w:r></w:p></w:tc></w:tr>"
            body += "</w:tbl>"
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                f'<w:document xmlns:w="{WORD}"><w:body>{body}</w:body></w:document>',
            )
        return self.command("ingest", "add", str(path))["id"]

    def test_delivery_persists_four_supported_checklist_items(self):
        scope, scope_quote = self.scope_version()
        word = self.word_version()
        store = SqliteStore(self.database)
        scope_block, = store.version(scope).blocks
        proof = EvidenceValidator(store.version(scope)).validate(scope_block.id, "☐" + scope_quote)
        existing = compliance_claim(
            scope, "2.4.1 Bypass Blocks is not required for non-web documents and software.", (proof,),
        )
        store.save_claims((existing,))

        result = self.command(
            "deliver", "run", REQUEST, "--source", scope, "--source", word, "--max-steps", "4",
        )
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["gaps"], [])
        artifact = result["artifact"]
        self.assertEqual(artifact["kind"], "checklist")
        self.assertEqual(self.command("deliver", "show", result["id"]), result)
        scope_item, headings, tables, images = artifact["items"]
        self.assertEqual([item["title"] for item in artifact["items"]],
                         ["适用范围", "标题结构", "数据表格", "图片说明"])
        self.assertEqual(scope_item["claim_id"], existing.id)
        self.assertEqual(len(self.command("claims", "list", scope)["claims"]), 1)
        self.assertIn("2.4.1 Bypass Blocks", scope_item["check"])
        self.assertIn("导航窗格", headings["method"])
        self.assertIn("不适用", tables["method"])
        self.assertIn("不适用", images["method"])
        anchors = (
            (scope_item, scope, {(None, None, "☐" + scope_quote)}),
            (headings, word, {
                (1, 1, "The document uses MS Word heading styles."),
                (1, 2, "Headings in the \"Navigation pane\" match the document's visual outline."),
            }),
            (tables, word, {(2, 1, "Tables repeat their header row.")}),
            (images, word, {(3, 1, "Images/objects have Alt Text or described in context.")}),
        )
        for item, version_id, expected in anchors:
            self.assertTrue(item["check"])
            self.assertTrue(item["method"])
            saved_ids = {claim["id"] for claim in self.command("claims", "list", version_id)["claims"]}
            self.assertIn(item["claim_id"], saved_ids)
            locations = set()
            for reference in item["evidence"]:
                located = self.command("ingest", "evidence-show", reference["id"])
                self.assertEqual(located, reference)
                self.assertEqual(located["version_id"], version_id)
                locations.add((located["locator"]["table"], located["locator"]["row"], located["quote"]))
            self.assertEqual(locations, expected)


    def test_coverage_found_after_wall_budget_is_not_delivered(self):
        scope, _ = self.scope_version()
        word = self.word_version()
        clock = 0

        def slow_last_lookup(slot, **kwargs):
            nonlocal clock
            coverage = find_coverage(slot, **kwargs)
            if slot.id == "image_descriptions":
                clock = 601
            return coverage

        with (
            patch("openkb.core.delivery.monotonic", side_effect=lambda: clock),
            patch("openkb.server.delivery.find_coverage", side_effect=slow_last_lookup),
        ):
            report = self.command(
                "deliver", "run", REQUEST, "--source", scope, "--source", word, "--max-steps", "4",
                expected_exit=1,
            )

        self.assertEqual(report["state"], "paused")
        self.assertEqual(report["pause_reason"], "wall clock budget exhausted")
        self.assertEqual(report["gaps"], [{"id": "image_descriptions", "title": "图片说明"}])
        self.assertEqual(report["budget"]["steps"], 4)
        self.assertIsNone(report["artifact"])
        self.assertEqual(self.command("deliver", "show", report["id"]), report)

    def test_unopenable_ledger_reports_a_cli_error(self):
        invalid_path = self.root / "no-parent" / "ledger.db"
        with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as failed:
            main(["deliver", "show", "task", "--database", str(invalid_path)])

        self.assertEqual(failed.exception.code, 2)
        self.assertIn("unable to open database file", error.getvalue())
        self.assertNotIn("Traceback", error.getvalue())
