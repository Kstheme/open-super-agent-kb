from contextlib import redirect_stderr
import io

from review_fixtures import TABLE_RULE, ReviewCliCase, paragraph, write_word


class ReviewCliTests(ReviewCliCase):
    def test_position_change_rebases_evidence_and_keeps_original_artifact(self):
        path = write_word(
            self.root / "section508-moved.docx",
            prefix=paragraph("Editorial note for acceptance test."),
        )
        result = self.run_review(path)

        self.assertEqual(result["kind"], "review")
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["artifact_id"], self.artifact_id)
        self.assertEqual(result["before_version"], self.word_version["id"])
        self.assertNotEqual(result["after_version"], result["before_version"])
        self.assertEqual(result["normalization_version"], self.word_version["normalization_version"])
        self.assertEqual(result["affected"], [])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["alignment_warnings"], [])
        self.assertEqual(len(result["rebased_evidence"]), 4)

        table_move, = [move for move in result["rebased_evidence"] if move["item_id"] == "data_tables"]
        before = self.command("ingest", "show", result["before_version"])
        after = self.command("ingest", "show", result["after_version"])
        self.assertEqual(before["document_id"], after["document_id"])
        old_block, = [block for block in before["blocks"] if block["id"] == table_move["old_evidence"]["block_id"]]
        new_block, = [block for block in after["blocks"] if block["id"] == table_move["new_evidence"]["block_id"]]
        self.assertEqual(old_block["block_key"], new_block["block_key"])
        self.assertNotEqual(old_block["ordinal"], new_block["ordinal"])
        located = self.command("ingest", "evidence-show", table_move["new_evidence"]["id"])
        self.assertEqual(located["version_id"], result["after_version"])
        self.assertEqual(located["quote"], TABLE_RULE)
        self.assertEqual(self.command("review", "show", result["id"]), result)
        self.assertEqual(self.command("deliver", "show", self.artifact_id), self.delivery)

    def test_changed_table_claim_is_the_only_affected_item(self):
        replacement = "Only data tables that span multiple pages must repeat their header row."
        path = write_word(self.root / "section508-updated.docx", (replacement,))

        result = self.run_review(path)

        affected, = result["affected"]
        self.assertEqual(affected["item_id"], "data_tables")
        self.assertEqual(affected["claim_id"], self.delivery["artifact"]["items"][2]["claim_id"])
        change, = affected["changes"]
        self.assertEqual(change["status"], "changed")
        self.assertEqual(change["old_evidence"]["quote"], TABLE_RULE)
        self.assertEqual(change["before_block"]["id"], change["old_evidence"]["block_id"])
        candidate, = change["candidate_blocks"]
        self.assertEqual(candidate["text"], replacement)
        self.assertEqual(len(result["rebased_evidence"]), 3)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(self.command("deliver", "show", self.artifact_id), self.delivery)

    def test_duplicate_matches_are_reported_without_guessing_evidence(self):
        path = write_word(self.root / "section508-duplicate.docx", (TABLE_RULE, TABLE_RULE))

        result = self.run_review(path)

        affected, = result["affected"]
        self.assertEqual(affected["item_id"], "data_tables")
        change, = affected["changes"]
        self.assertEqual(change["status"], "ambiguous")
        self.assertEqual(len(change["candidate_blocks"]), 2)
        self.assertTrue(result["alignment_warnings"])
        self.assertNotIn("data_tables", {move["item_id"] for move in result["rebased_evidence"]})
        self.assertEqual(result["proposals"], [])

    def test_selected_version_must_be_evidence_for_the_artifact(self):
        path = write_word(self.root / "section508-other.docx", (TABLE_RULE,))
        unrelated = self.command("ingest", "add", path, "--document", "unrelated")

        with redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as exit:
            self.command("review", "run", self.artifact_id, unrelated["id"], path)

        self.assertEqual(exit.exception.code, 2)
        self.assertIn("not used by this artifact", errors.getvalue())
        self.assertEqual(self.command("deliver", "show", self.artifact_id), self.delivery)
