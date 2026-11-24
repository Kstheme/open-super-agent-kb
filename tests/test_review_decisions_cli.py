from contextlib import redirect_stderr
import io

from review_fixtures import ReviewCliCase, TABLE_RULE, write_word


class ReviewDecisionCliTests(ReviewCliCase):
    def proposal(self, name="changed.docx", rule="Only tables that span pages repeat their header row."):
        path = write_word(self.root / name, (rule,))
        block = self.new_word_block(path, rule)
        with self.model_reply({
            "judgment": "material", "reason": "Applicability changed.",
            "revised_text": rule, "revised_method": "Inspect relevant tables.",
            "evidence": [{"block_id": block["id"], "quote": rule}],
        }):
            review = self.run_review(path, "--config", self.config)
        proposal, = review["proposals"]
        return review, proposal

    def test_accept_creates_a_new_version_and_preserves_old_evidence(self):
        before = self.command("review", "artifact", self.artifact_id)
        review, proposal = self.proposal()
        accepted = self.command("review", "accept", review["id"], proposal["id"])
        after = self.command("review", "artifact", self.artifact_id)

        self.assertEqual((before["version"], after["version"]), (1, 2))
        self.assertEqual(accepted["artifact_version"], 2)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(before["id"], after["id"])
        [old_item] = [item for item in before["items"] if item["slot_id"] == "data_tables"]
        [new_item] = [item for item in after["items"] if item["slot_id"] == "data_tables"]
        self.assertEqual([x for x in before["items"] if x != old_item],
                         [x for x in after["items"] if x != new_item])
        self.assertEqual(new_item["check"], proposal["revised_text"])
        self.assertEqual(new_item["method"], proposal["revised_method"])
        self.assertNotEqual(new_item["claim_id"], old_item["claim_id"])
        self.assertEqual(self.command("ingest", "evidence-show", old_item["evidence"][0]["id"])["quote"], TABLE_RULE)
        self.assertEqual(self.command("ingest", "evidence-show", new_item["evidence"][0]["id"])["version_id"],
                         review["after_version"])
        claims = self.command("claims", "list", review["after_version"])["claims"]
        self.assertTrue(any(claim["id"] == new_item["claim_id"] for claim in claims))
        self.assertEqual(self.command("review", "artifact", self.artifact_id, "--version", 1), before)
        self.assertEqual(self.command("deliver", "show", self.artifact_id), self.delivery)
        self.assertEqual(self.command("review", "accept", review["id"], proposal["id"]), accepted)

    def test_reject_records_a_reason_without_revising_the_artifact(self):
        review, proposal = self.proposal()
        before = self.command("review", "artifact", self.artifact_id)
        reason = "Keep the internal rule pending approval."
        result = self.command("review", "reject", review["id"], proposal["id"], "--reason", reason)

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["artifact_version"], 1)
        self.assertEqual(result["reason"], reason)
        observed, = self.command("review", "show", review["id"])["proposals"]
        self.assertEqual(observed["decision"], result)
        self.assertEqual(observed["status"], "rejected")
        self.assertEqual(self.command("review", "artifact", self.artifact_id), before)
        with redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as stopped:
            self.command("review", "accept", review["id"], proposal["id"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("already rejected", errors.getvalue())

    def test_accepting_a_stale_proposal_does_not_overwrite_a_concurrent_decision(self):
        stale_review, stale = self.proposal()
        review, proposal = self.proposal("newer.docx", "Data tables longer than two pages repeat the header row.")
        self.command("review", "accept", review["id"], proposal["id"])
        before = self.command("review", "artifact", self.artifact_id)

        with redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as stopped:
            self.command("review", "accept", stale_review["id"], stale["id"])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("stale", errors.getvalue().lower())
        self.assertEqual(self.command("review", "artifact", self.artifact_id), before)
        observed, = self.command("review", "show", stale_review["id"])["proposals"]
        self.assertEqual(observed["status"], "pending")
