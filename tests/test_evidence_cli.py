import os
import unittest
from pathlib import Path

from test_ingest_cli import _IngestHarness


class EvidenceCliTests(_IngestHarness):
    def test_exact_quote_must_match_version_and_disambiguate_repetition(self):
        original = self.root / "original.md"
        original.write_text("重复。重复。", encoding="utf-8")
        version = self.add(original, "text")
        block, = self.call("show", version)["blocks"]
        for quote, args in (("不存在", ()), ("重复。", ()), ("重复。", ("--quote-start", 2))):
            with self.subTest(quote=quote, args=args), self.assertRaises(SystemExit):
                self.call("evidence-add", version, block["id"], "--quote", quote, *args)

        args = (version, block["id"], "--quote", "重复。", "--quote-start", 3)
        evidence = self.call("evidence-add", *args)
        self.assertEqual(self.call("evidence-add", *args), evidence)
        result = self.call("evidence-show", evidence["id"])
        self.assertEqual((result["quote_start"], result["quote_end"]), (3, 6))
        self.assertEqual(result["locator"]["line_start"], 1)

        original.write_text("新增。", encoding="utf-8")
        new = self.add(original, "text")
        with self.assertRaises(SystemExit):
            self.call("evidence-add", new, block["id"], "--quote", "重复。")


@unittest.skipUnless(os.getenv("OPENKB_M1_SAMPLES"), "pinned M1 acceptance files are opt-in")
class PublicEvidenceGateTests(_IngestHarness):
    def test_twelve_public_anchors_and_old_version_after_move(self):
        samples = Path(os.environ["OPENKB_M1_SAMPLES"])
        groups = {
            "section508-word.docx": (
                ("Use this checklist to record MS Word 508 Accessibility results.", {"paragraph": 2}),
                ("The filename identifies the document or its purpose.", {"table": 1, "row": 3}),
                ("The document uses MS Word heading styles.", {"table": 2, "row": 1}),
                ("“Text wrapping” is set to “None” in Table Properties.", {"table": 5, "row": 3}),
            ),
            "cfr-255-2022.pdf": (
                ("Purpose and definitions.", {"page": 1, "clause": "255.0", "kind": "heading"}),
                ("General considerations.", {"page": 3, "clause": "255.1", "kind": "heading"}),
                ("Consumer endorsements.", {"page": 4, "clause": "255.2", "kind": "heading"}),
                ("Expert endorsements.", {"page": 6, "clause": "255.3", "kind": "heading"}),
            ),
            "k8s-components-before.md": (
                ("本文档概述了一个正常运行的 Kubernetes 集群所需的各种组件。", {"line_start": 33}),
                ("在每个节点上运行，维护运行的 Pod 并提供 Kubernetes 运行时环境：", {"line_start": 106}),
                ("确保 Pod 及其容器正常运行。", {"line_start": 108, "line_end": 109}),
                ("Kubernetes 允许灵活地部署和管理这些组件。", {"line_start": 172}),
            ),
        }
        for file_name, anchors in groups.items():
            version_id = self.add(samples / file_name, file_name)
            blocks = self.call("show", version_id)["blocks"]
            for quote, position in anchors:
                with self.subTest(file=file_name, quote=quote):
                    block, = [b for b in blocks if quote in b["text"] and all(
                        b.get(key, b["locator"].get(key)) == value for key, value in position.items()
                    )]
                    evidence = self.call("evidence-add", version_id, block["id"], "--quote", quote)
                    result = self.call("evidence-show", evidence["id"])
                    self.assertEqual((result["version_id"], result["document_id"], result["original_name"]),
                                     (version_id, file_name, file_name))
                    self.assertEqual(result["locator"], block["locator"])
                    self.assertEqual(result["quote"], quote)

        name = "k8s-components-before.md"
        source = samples / name
        before = self.add(source, name)
        quote = groups[name][1][0]
        block, = [b for b in self.call("show", before)["blocks"] if quote in b["text"]]
        evidence = self.call("evidence-add", before, block["id"], "--quote", quote)
        original = source.read_bytes()
        old = quote.encode("utf-8")
        marker = "[容器运行时（Container runtime）]".encode("utf-8")
        self.assertEqual(original.count(old + b"\n\n"), 1)
        self.assertEqual(original.count(marker), 1)
        moved = self.root / "moved.md"
        moved.write_bytes(original.replace(old + b"\n\n", b"", 1).replace(marker, old + b"\n\n" + marker, 1))
        after = self.add(moved, name)
        self.assertNotEqual(after, before)
        new_block, = [b for b in self.call("show", after)["blocks"] if quote in b["text"]]
        self.assertEqual(new_block["locator"]["line_start"], 112)
        located = self.call("evidence-show", evidence["id"])
        self.assertEqual(located["version_id"], before)
        self.assertEqual(located["locator"]["line_start"], 106)
        retained = self.root / "retained.md"
        self.call("original", located["version_id"], "--out", retained)
        self.assertEqual(retained.read_bytes().splitlines()[105].decode("utf-8"), quote)
