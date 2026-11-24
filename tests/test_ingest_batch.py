import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openkb.ingest.ledger import Ledger
from openkb.ingest.sqlite import SqliteStore
from openkb.server.cli import main

from test_ingest_docx import _write_word


class BatchCliTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.source = self.root / "sources"
        self.source.mkdir()

    def call(self, command, *args, code=0):
        with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as progress:
            result = main(["ingest", command, "--database", str(self.root / "kb.sqlite"), *map(str, args)])
        self.assertEqual(result, code, progress.getvalue())
        return json.loads(output.getvalue()), progress.getvalue()

    def test_file_budget_resume_and_repair_only_failed_file(self):
        first = self.source / "1.md"
        failed = self.source / "2.docx"
        last = self.source / "3.md"
        first.write_text("First", encoding="utf-8")
        failed.write_bytes(b"damaged")
        last.write_text("Last", encoding="utf-8")

        paused, _ = self.call("batch", self.source, "--limit", 1)
        batch_id = paused["batch_id"]
        self.assertEqual((paused["state"], paused["completed"], paused["pending"]), ("paused", 1, 2))
        self.assertEqual(self.call("status", batch_id)[0], paused)
        first.unlink()

        result, progress = self.call("resume", batch_id, code=1)
        self.assertNotIn(str(first), progress)
        self.assertEqual((result["state"], result["completed"], result["pending"]), ("failed", 2, 0))
        self.assertEqual(result["failures"][0]["file"], str(failed))
        self.assertIn("Cannot parse DOCX", result["failures"][0]["reason"])

        _write_word(failed, "已修复")
        last.unlink()
        repaired, progress = self.call("retry", batch_id, failed)
        self.assertEqual((repaired["state"], repaired["completed"], repaired["failures"]), ("done", 3, []))
        self.assertIn(str(failed), progress)
        self.assertEqual(len(progress.splitlines()), 1)

    def test_interruption_while_recording_checkpoint_rolls_back_the_version(self):
        source = self.source / "policy.md"
        source.write_text("First version", encoding="utf-8")
        version_ids = []

        def interrupted(conn, batch_id, item):
            version_ids.append(item.version_id)
            raise KeyboardInterrupt

        with patch.object(SqliteStore, "_finish_file", staticmethod(interrupted)):
            paused, _ = self.call("batch", self.source, code=130)
        self.assertEqual((paused["completed"], paused["pending"]), (0, 1))
        with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
            main(["ingest", "show", version_ids[0], "--database", str(self.root / "kb.sqlite")])
        self.assertIn("Unknown version", error.getvalue())
        source.write_text("Replacement version", encoding="utf-8")
        resumed, _ = self.call("resume", paused["batch_id"])
        self.assertEqual(resumed["completed"], 1)
        self.assertNotEqual(resumed["versions"][0]["version_id"], version_ids[0])

    def test_stale_resume_does_not_write_an_untracked_version(self):
        source = self.source / "policy.md"
        source.write_text("Initial content", encoding="utf-8")
        first_report = None

        def complete_in_other_invocation(batch_id, index, total, path):
            nonlocal first_report
            with patch("openkb.server.batch_cli._progress"):
                first_report, _ = self.call("resume", batch_id)
            source.write_text("Replacement content", encoding="utf-8")

        with patch("openkb.server.batch_cli._progress", complete_in_other_invocation):
            stale_report, _ = self.call("batch", self.source)

        self.assertEqual(stale_report, first_report)
        first_id = first_report["versions"][0]["version_id"]
        self.assertEqual(self.call("show", first_id)[0]["blocks"][0]["text"], "Initial content")
        replacement = Ledger(SqliteStore(self.root / "separate.sqlite")).add(source)
        with redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
            main(["ingest", "show", replacement.id, "--database", str(self.root / "kb.sqlite")])
        self.assertIn("Unknown version", error.getvalue())

    @unittest.skipUnless(os.environ.get("OPENKB_M1_BATCH"), "Set OPENKB_M1_BATCH to the verified 100 originals")
    def test_real_batch_resumes_from_interrupted_document_51(self):
        samples = Path(os.environ["OPENKB_M1_BATCH"])
        files = sorted(path for path in samples.iterdir() if path.suffix.lower() in {".docx", ".pdf"})
        self.assertEqual(len(files), 100)
        for path in files:
            shutil.copyfile(path, self.source / path.name)
        original_add = Ledger.add
        attempted = []

        def interrupted(ledger, path, **kwargs):
            attempted.append(path.name)
            if len(attempted) == 51:
                # Interrupt after reading the first bytes but before writing the version.
                with path.open("rb") as source:
                    self.assertTrue(source.read(8))
                    raise KeyboardInterrupt
            return original_add(ledger, path, **kwargs)

        with patch.object(Ledger, "add", interrupted):
            paused, _ = self.call("batch", self.source, code=130)
        self.assertEqual((paused["state"], paused["completed"], paused["pending"]), ("paused", 50, 50))
        batch_id = paused["batch_id"]
        self.assertEqual(paused["versions"], self.call("status", batch_id)[0]["versions"])
        for path in files[:50]:
            (self.source / path.name).unlink()

        result, progress = self.call("resume", batch_id)
        self.assertEqual((result["state"], result["completed"], result["pending"]), ("done", 100, 0))
        self.assertEqual(result["total"], 100)
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["versions"][:50], paused["versions"])
        self.assertEqual(len(progress.splitlines()), 50)
        self.assertIn(f"51/100: {self.source / attempted[-1]}", progress.splitlines()[0])
