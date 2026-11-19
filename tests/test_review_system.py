import unittest
from dataclasses import replace

from openkb.eval.contract import (
    ArtifactItem, Block, Budget, Corpus, Cost, Document, EvidenceReference,
    Proposal, ReviewRequest, ReviewResult, ReviewSystem, Timing, Version,
)
from openkb.eval.noop import NoopReviewSystem


class ReviewSystemTests(unittest.TestCase):
    def assert_review_contract(self, system: ReviewSystem, expected: ReviewResult):
        before = Block("policy/1-old", ("Policy",), 1, 0, 7, "7 days.")
        after = Block("policy/1-new", ("Policy",), 1, 0, 8, "14 days.")
        request = ReviewRequest(
            corpus=Corpus(
                before=(Document("policy", Version("v1", (before,))),),
                after=(Document("policy", Version("v2", (after,))),),
            ),
            artifact=(ArtifactItem("notice-period", "Allow 7 days."),),
            goal="Maintain the notice checklist",
            budget=Budget(max_steps=3, max_tokens=500, max_wall_seconds=5),
        )
        self.assertIsNone(request.ledger)
        actual = system.review(request)
        self.assertEqual(actual.timing.model_seconds, expected.timing.model_seconds)
        if actual.timing.model_seconds is not None:
            self.assertGreaterEqual(actual.timing.total_seconds, actual.timing.model_seconds)
        self.assertEqual(replace(actual, timing=expected.timing), expected)

    def test_noop_finishes_offline_without_proposals_or_ledger_use(self):
        self.assert_review_contract(
            NoopReviewSystem(),
            ReviewResult((), Cost(0, 0, None, None), Timing(0, 0), "done", False),
        )

    def test_substitute_satisfies_the_same_contract(self):
        class FixedReviewSystem:
            def review(self, request):
                document, = request.corpus.after
                block, = document.version.blocks
                item, = request.artifact
                return ReviewResult(
                    (Proposal(item.item_id, "material", block.text, (EvidenceReference(
                        document.document_id, document.version.version_id, block.block_key,
                    ),)),),
                    Cost(80, 20, None, "offline-fixture"),
                    Timing(None, 1), "paused", False, ("Check the exception clause",),
                )

        self.assert_review_contract(
            FixedReviewSystem(),
            ReviewResult(
                (Proposal("notice-period", "material", "14 days.", (
                    EvidenceReference("policy", "v2", "policy/1-new"),
                )),),
                Cost(80, 20, None, "offline-fixture"),
                Timing(None, 1), "paused", False, ("Check the exception clause",),
            ),
        )
