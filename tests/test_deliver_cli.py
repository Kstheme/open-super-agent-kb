from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

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
