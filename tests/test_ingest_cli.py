import io
import json
import os
import tempfile
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from openkb.server.cli import main


class _IngestHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def call(self, command, *args):
        with redirect_stdout(io.StringIO()) as output:
            code = main(["ingest", command, "--database", str(self.root / "kb.sqlite"), *map(str, args)])
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def add(self, path, document):
        return self.call("add", path, "--document", document)["id"]

class IngestCliTests(_IngestHarness):
    def test_markdown_originals_and_ambiguous_blocks_survive_another_import(self):
        old = self.root / "old.md"
        new = self.root / "new.md"
        previous = "# 指南\n\n重复。\n\n锚点。\n\n重复。\n"
        old.write_text(previous, encoding="utf-8")
        new.write_text(previous.replace("锚点。", "新增。\n\n锚点。"), encoding="utf-8")
        before = self.add(old, "guide")
        after = self.add(new, "guide")
        self.assertEqual(self.add(new, "guide"), after)

        report = self.call("align", before, after)
        self.assertNotEqual(report["normalization_version"], "")
        stable = next(item for item in report["matches"] if item["before"]["text"] == "锚点。")
        self.assertEqual(stable["before"]["locator"]["line_start"], 5)
        self.assertEqual(stable["after"]["locator"]["line_start"], 7)
        self.assertEqual([b["text"] for b in report["added"]], ["新增。"])
        self.assertEqual(len(report["ambiguous"]), 1)
        self.assertEqual(len(report["ambiguous"][0]["before"]), 2)
        self.assertFalse(report["removed"])
        self.assertEqual(report, self.call("align", before, after))

        retained = self.root / "retained.md"
        old.unlink()
        self.call("original", before, "--out", retained)
        self.assertEqual(retained.read_bytes().decode("utf-8").splitlines(), previous.splitlines())

    def test_pdf_without_text_remains_a_single_document_block(self):
        pdf = self.root / "no-text.pdf"
        objects = (
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Count 1/Kids[3 0 R]>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>",
        )
        data = bytearray(b"%PDF-1.4\n")
        positions = []
        for number, obj in enumerate(objects, 1):
            positions.append(len(data))
            data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(data)
        data.extend(b"xref\n0 4\n0000000000 65535 f \n")
        for position in positions:
            data.extend(f"{position:010} 00000 n \n".encode())
        data.extend(f"trailer\n<</Root 1 0 R/Size 4>>\nstartxref\n{xref}\n%%EOF\n".encode())
        pdf.write_bytes(data)

        version = self.call("show", self.add(pdf, "no-text"))
        self.assertEqual([block["kind"] for block in version["blocks"]], ["document"])
        self.assertIsNone(version["blocks"][0]["locator"]["page"])
        self.assertIn("原文定位受限", version["warnings"][0])


@unittest.skipUnless(os.getenv("OPENKB_M1_SAMPLES"), "pinned M1 acceptance files are opt-in")
class PublicSampleGateTests(_IngestHarness):
    @property
    def samples(self):
        return Path(os.environ["OPENKB_M1_SAMPLES"])

    def compare(self, old_name, new_name, document):
        before = self.add(self.samples / old_name, document)
        after = self.add(self.samples / new_name, document)
        return self.call("show", before), self.call("align", before, after)

    def test_controlled_insertions_leave_every_previous_block_unchanged(self):
        examples = (
            ("cfr-255-2022.pdf", "cfr-255-controlled-insertion.pdf", "pdf", "Fixture-only note:"),
            ("k8s-components-before.md", "k8s-controlled-insertion.md", "md", "验收样本附注"),
        )
        for original, edited, document, note in examples:
            with self.subTest(document=document):
                before, report = self.compare(original, edited, document)
                self.assertEqual(len(report["matches"]), len(before["blocks"]))
                self.assertFalse(report["removed"])
                self.assertFalse(report["ambiguous"])
                added, = report["added"]
                self.assertTrue(added["text"].startswith(note))
                for match in report["matches"]:
                    old, new = match["before"], match["after"]
                    self.assertEqual(old["block_key"], new["block_key"])
                    label = "page" if document == "pdf" else "line_start"
                    old_position = old["locator"][label]
                    shift = (old_position >= 2) if document == "pdf" else 2 * (old_position >= 104)
                    self.assertEqual(new["locator"][label], old_position + shift)
                if document == "pdf":
                    expected = {f"255.{section}": page for section, page in
                                enumerate((1, 3, 4, 6, 7, 7))}
                    observed = {
                        match["before"]["clause"]: (match["before"]["locator"]["page"],
                                                     match["after"]["locator"]["page"])
                        for match in report["matches"] if match["before"]["kind"] == "heading"
                    }
                    for clause, page in expected.items():
                        self.assertEqual(observed[clause], (page, page + (page >= 2)))
                else:
                    observed = {
                        match["before"]["locator"]["line_start"]: match["after"]["locator"]["line_start"]
                        for match in report["matches"] if match["before"]["kind"] == "paragraph"
                    }
                    for old_line, new_line in ((33, 33), (106, 108), (108, 110), (111, 113), (172, 174)):
                        self.assertEqual(observed[old_line], new_line)
                print(f"M1 controlled {document}: {len(report['matches'])}/{len(before['blocks'])}", file=sys.stderr)

    def test_historical_pdf_reports_all_six_body_title_anchors(self):
        _, report = self.compare("cfr-255-2022.pdf", "cfr-255-2024.pdf", "cfr")
        by_clause = {m["before"]["clause"]: m for m in report["matches"] if m["before"]["kind"] == "heading"}
        expected_pages = ((1, 1), (3, 5), (4, 8), (6, 12), (7, 14), (7, 14))
        for section, pages in enumerate(expected_pages):
            match = by_clause[f"255.{section}"]
            self.assertEqual((match["before"]["locator"]["page"], match["after"]["locator"]["page"]), pages)
            self.assertEqual(match["after"]["clause"], f"255.{section}")
        self.assertTrue(any(b["kind"] == "heading" and b["clause"] == "255.6" for b in report["added"]))

    def test_historical_markdown_reports_all_five_visible_body_anchors(self):
        _, report = self.compare("k8s-components-before.md", "k8s-components-after.md", "k8s")
        by_line = {m["before"]["locator"]["line_start"]: m for m in report["matches"]}
        for old_line, new_line in ((33, 33), (106, 105), (108, 107), (111, 110), (172, 171)):
            match = by_line[old_line]
            self.assertEqual(match["after"]["locator"]["line_start"], new_line)
            self.assertEqual(match["before"]["kind"], "paragraph")
