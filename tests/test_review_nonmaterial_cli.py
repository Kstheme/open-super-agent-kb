from review_fixtures import TABLE_RULE, ReviewCliCase, write_word


class NonmaterialReviewCliTests(ReviewCliCase):
    def test_paraphrase_is_logged_as_immaterial_without_a_proposal(self):
        path = write_word(self.root / "paraphrase.docx", ("Each data table repeats its header row.",))
        with self.model_reply({"judgment": "immaterial", "reason": "规则含义保持一致。"}):
            report = self.run_review(path, "--config", self.config)

        self.assertEqual(report["state"], "done")
        self.assertEqual([item["item_id"] for item in report["affected"]], ["data_tables"])
        self.assertEqual(report["judgments"], [{
            "item_id": "data_tables", "judgment": "immaterial", "reason": "规则含义保持一致。",
        }])
        self.assertEqual(report["proposals"], [])
        self.assertEqual(self.command("review", "show", report["id"]), report)
        self.assertEqual(self.command("deliver", "show", self.artifact_id), self.delivery)

    def test_removed_basis_shows_the_original_statement_and_lost_evidence(self):
        path = write_word(self.root / "removed-rule.docx", ())

        report = self.run_review(path)

        self.assertEqual(report["state"], "done")
        affected, = report["affected"]
        self.assertEqual(affected["item_id"], "data_tables")
        self.assertEqual(affected["changes"][0]["status"], "removed")
        self.assertEqual(affected["changes"][0]["candidate_blocks"], [])
        self.assertIsNone(report["model_id"])
        self.assertEqual(report["usage"]["steps"], 0)
        self.assert_needs_human(report)

    def test_unrelated_replacement_can_be_marked_for_human_review(self):
        path = write_word(self.root / "unrelated-row.docx", ("Data tables must have readable captions.",))
        with self.model_reply({"judgment": "needs_human", "reason": "新版只讨论表注。"}):
            report = self.run_review(path, "--config", self.config)

        self.assertEqual(report["state"], "done")
        self.assertEqual(report["judgments"][0]["reason"], "新版只讨论表注。")
        self.assert_needs_human(report)

    def assert_needs_human(self, report):
        judgment, = report["judgments"]
        item, = [entry for entry in self.delivery["artifact"]["items"] if entry["slot_id"] == "data_tables"]
        self.assertEqual(judgment["item_id"], "data_tables")
        self.assertEqual(judgment["judgment"], "needs_human")
        self.assertEqual(judgment["claim_id"], item["claim_id"])
        self.assertEqual(judgment["original_statement"], item["check"])
        self.assertEqual(judgment["lost_evidence"], item["evidence"])
        old_evidence, = judgment["lost_evidence"]
        located = self.command("ingest", "evidence-show", old_evidence["id"])
        self.assertEqual(located["version_id"], self.word_version["id"])
        self.assertEqual(located["quote"], TABLE_RULE)
        self.assertEqual(report["proposals"], [])
        self.assertEqual(self.command("review", "show", report["id"]), report)
        self.assertEqual(self.command("deliver", "show", self.artifact_id), self.delivery)
