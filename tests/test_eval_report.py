import unittest

from openkb.eval.report import validate_report_versions


class RunReportTests(unittest.TestCase):
    def test_report_boundary_requires_all_six_version_fields(self):
        report = {
            "harness_version": "1",
            "goldset_hash": "abc123",
            "system": "noop",
            "model_id": None,
            "profile_version": "policy-v1",
            "price_table_version": None,
        }
        validate_report_versions(report)
        for field in report:
            with self.subTest(field=field):
                incomplete_report = dict(report)
                del incomplete_report[field]
                with self.assertRaisesRegex(ValueError, field):
                    validate_report_versions(incomplete_report)
