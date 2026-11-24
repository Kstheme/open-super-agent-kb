from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from openkb.ingest.sqlite import SqliteStore
from openkb.server.cli import main


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REQUEST = "请基于 Section 508 为非网页 Word 文档生成无障碍自查清单。"


def paragraph(text, heading=False):
    style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
    return f"<w:p>{style}<w:r><w:t>{text}</w:t></w:r></w:p>"


def section(title, *quotes):
    rows = "".join(f"<w:tr><w:tc>{paragraph(quote)}</w:tc></w:tr>" for quote in quotes)
    return paragraph(title, heading=True) + f"<w:tbl>{rows}</w:tbl>"


class DeliverConflictTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = self.root / "ledger.sqlite"
        self.scope = self.ingest(
            "508-applicability-checklist.docx",
            paragraph("2.4.1 Bypass Blocks (not required for non-web documents and software)"),
        )
        self.word_text = (
            section(
                "Headings",
                "The document uses MS Word heading styles.",
                'Headings in the "Navigation pane" match the document\'s visual outline.',
            )
            + section("Data Tables", "Tables repeat their header row.")
            + section("Images and other Objects", "Images/objects have Alt Text or described in context.")
        )
        self.word = self.ingest("section508-word.docx", self.word_text)

    def command(self, *args, expected_exit=0):
        with redirect_stdout(io.StringIO()) as output:
            status = main([*args, "--database", str(self.database)])
        self.assertEqual(status, expected_exit)
        return json.loads(output.getvalue())

    def ingest(self, name, body):
        path = self.root / name
        with ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                f'<w:document xmlns:w="{WORD_NS}"><w:body>{body}</w:body></w:document>',
            )
        return self.command("ingest", "add", str(path))["id"]

    def deliver(self, word=None, expected_exit=0):
        return self.command(
            "deliver", "run", REQUEST, "--source", self.scope, "--source", word or self.word,
            "--max-steps", "6", expected_exit=expected_exit,
        )

    def test_unresolved_conflict_blocks_delivery_until_resolved(self):
        reason = "重复表头规则存在待人工核对的冲突"
        conflict = self.command("deliver", "flag-conflict", "data_tables", self.word, reason)
        paused = self.deliver(expected_exit=1)
        self.assertEqual(conflict["state"], "open")
        self.assertEqual(paused["state"], "paused")
        self.assertEqual(paused["budget"]["steps"], 6)
        self.assertEqual([slot["id"] for slot in paused["gaps"]], ["data_tables"])
        self.assertEqual(len(paused["covered"]), 3)
        self.assertEqual(len(self.command("claims", "list", self.word)["claims"]), 3)
        self.assertEqual(paused["conflicts"], [conflict])
        self.assertIsNone(paused["artifact"])
        self.assertEqual(self.command("deliver", "show", paused["id"]), paused)

        resolution = self.command("deliver", "resolve-conflict", conflict["id"])
        self.assertEqual(resolution, {**conflict, "state": "resolved"})
        self.assertEqual(self.command("deliver", "resolve-conflict", conflict["id"]), resolution)
        done = self.deliver()
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["gaps"], [])
        self.assertEqual(done["conflicts"], [])
        self.assertEqual(len(done["artifact"]["items"]), 4)
        self.assertEqual(self.command("deliver", "show", paused["id"]), paused)

    def test_conflict_applies_only_to_the_relevant_source_version(self):
        with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as failed:
            main([
                "deliver", "flag-conflict", "data_tables", self.scope, "A conflict",
                "--database", str(self.database),
            ])
        self.assertEqual(failed.exception.code, 2)
        self.assertIn("is not a source for slot data_tables", error.getvalue())

        self.command("deliver", "flag-conflict", "data_tables", self.word, "Conflict in old version")
        new_word = self.ingest("section508-word.docx", self.word_text + paragraph("Another version"))
        self.assertNotEqual(new_word, self.word)
        self.assertEqual(self.deliver(word=new_word)["state"], "done")

    def test_conflict_opened_before_persistence_reopens_gaps(self):
        save_delivery = SqliteStore.save_delivery
        flagged = []

        def flag_before_save(store, task):
            if task["state"] == "done" and not flagged:
                flagged.append(self.command(
                    "deliver", "flag-conflict", "data_tables", self.word, "Opened at delivery",
                ))
            return save_delivery(store, task)

        with patch.object(SqliteStore, "save_delivery", flag_before_save):
            result = self.deliver(expected_exit=1)

        self.assertEqual(result["state"], "paused")
        self.assertEqual([slot["id"] for slot in result["gaps"]], ["data_tables"])
        self.assertEqual(result["conflicts"], flagged)
        self.assertEqual(result["budget"]["steps"], 6)
        self.assertIsNone(result["artifact"])
        self.assertEqual(self.command("deliver", "show", result["id"]), result)


if __name__ == "__main__":
    unittest.main()
