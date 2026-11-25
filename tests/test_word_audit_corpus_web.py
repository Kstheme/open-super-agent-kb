"""Opt-in comparison of two pinned originals with the complete M1 corpus."""

from contextlib import redirect_stderr, redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
from unittest import skipUnless
from urllib.request import urlopen

from openkb.server.cli import main

from web_fixtures import BrowserCase


GOAL = (
    "这份非网页 Word 有一张单页数据表和一张跨页数据表，两张都没设置重复表头。"
    "按 Section 508 检查资料，需要检查 Bypass Blocks 吗？哪些表需要改？"
)
APPLICABILITY = "508-applicability-checklist.docx"
WORD = "508-word-checklist.docx"
MANIFEST = Path(__file__).resolve().parents[1] / "goldsets" / "m1-batch-samples" / "manifest.json"


@skipUnless(os.getenv("OPENKB_WORD_AUDIT_BATCH"), "Set OPENKB_WORD_AUDIT_BATCH to the 100 pinned originals")
class WordAuditCorpusWebTests(BrowserCase):
    def setUp(self):
        super().setUp()
        self.corpus = Path(os.environ["OPENKB_WORD_AUDIT_BATCH"]).resolve()
        self.hashes = {
            item["file"]: item["sha256"]
            for item in json.loads(MANIFEST.read_text(encoding="utf-8"))["sources"]
        }

    def pinned_file(self, filename):
        contents = (self.corpus / filename).read_bytes()
        self.assertEqual(sha256(contents).hexdigest(), self.hashes[filename], filename)
        return contents

    def test_pinned_pair_retains_both_originals(self):
        filenames = {APPLICABILITY: APPLICABILITY, WORD: "section508-word.docx"}
        versions = {}
        for original, uploaded in filenames.items():
            page = self.upload(uploaded, self.pinned_file(original))
            versions[original] = re.search(r'data-version-id="([a-f0-9]+)"', page)[1]
        self.assert_audit(filenames, versions)

    def test_same_question_reaches_the_right_evidence_among_hundred_originals(self):
        self.assertEqual(len(self.hashes), 100)
        for filename in self.hashes:
            self.pinned_file(filename)
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as progress:
            status = main(["ingest", "batch", str(self.corpus), "--database", str(self.settings.database)])
        self.assertEqual(status, 0, progress.getvalue())
        batch = json.loads(output.getvalue())
        self.assertEqual((batch["state"], batch["completed"], batch["pending"], batch["failures"]),
                         ("done", 100, 0, []))
        versions = {Path(item["file"]).name: item["version_id"] for item in batch["versions"]}
        self.assertEqual(versions.keys(), self.hashes.keys())
        self.assertEqual(len(set(versions.values())), 100)
        self.assert_audit({APPLICABILITY: APPLICABILITY, WORD: WORD}, versions)

    def assert_audit(self, filenames, versions):
        self.assertIn('action="/word-audits"', self.page("/"))
        with self.post_form("/word-audits", {"goal": GOAL}) as response:
            answer = response.read().decode("utf-8")
        self.assertIn(GOAL, answer)
        self.assertIn("非网页 Word 文档不要求 2.4.1 Bypass Blocks。", answer)
        self.assertIn("单页和跨页数据表格均适用。未设置重复表头的表格需要补设。", answer)

        links = set()
        for slot, source, quote, position in (
            ("scope", APPLICABILITY, "2.4.1 Bypass Blocks (not required for non-web documents and software)", "第 71 段"),
            ("data_tables", WORD, "Tables repeat their header row.", "第 9 张表，第 4 行"),
        ):
            with self.subTest(slot=slot):
                item = re.search(rf'<li\b[^>]*data-slot-id="{slot}"[^>]*>(.*?)</li>', answer, re.DOTALL)
                self.assertIsNotNone(item)
                trace = item[1]
                self.assertRegex(trace, r'data-claim-id="[a-f0-9]+"')
                evidence = re.findall(r'href="(/evidence/[a-f0-9]+)"', trace)
                self.assertEqual(len(evidence), 1)
                detail = self.page(evidence[0])
                for value in (filenames[source], versions[source], quote, position):
                    self.assertIn(value, trace)
                    self.assertIn(value, detail)
                with urlopen(f"{self.address}{evidence[0]}/original", timeout=5) as response:
                    self.assertEqual(sha256(response.read()).hexdigest(), self.hashes[source])
                links.add(evidence[0])
        self.assertEqual(len(links), 2)
        self.assertEqual(set(re.findall(r'href="(/evidence/[a-f0-9]+)"', answer)), links)
