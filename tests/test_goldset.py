import json
from pathlib import Path
import tempfile
import unittest

from openkb.eval.goldset import load_goldset

from test_eval_cli import goldset


class GoldSetValidationTests(unittest.TestCase):
    def test_invalid_cases_fail_at_load_with_distinct_reasons(self):
        for reason in ("unknown item_id", "revision pair", "duplicate annotation", "missing annotation"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                data = goldset()
                case = data["cases"][1]
                annotation = case["annotations"][0]
                if reason == "unknown item_id":
                    annotation["item_id"] = "foreign-item"
                elif reason == "revision pair":
                    case["corpus"]["after"][0]["document_id"] = "another-policy"
                elif reason == "duplicate annotation":
                    case["annotations"].append(annotation.copy())
                else:
                    case["annotations"].clear()
                source = Path(tmp) / "cases.json"
                source.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, f"control.*{reason}"):
                    load_goldset(source)


    def test_explicit_artifact_slots_must_be_unique_and_positive(self):
        for reason, ordinal in (("duplicate", 1), ("positive integer", 0), ("positive integer", True)):
            with self.subTest(ordinal=ordinal), tempfile.TemporaryDirectory() as tmp:
                data = goldset()
                case = data["cases"][0]
                case["artifact"][0].update(section="notices", ordinal=1)
                case["artifact"].append({
                    "item_id": "channel", "snapshot_text": "written",
                    "section": "notices", "ordinal": ordinal,
                })
                case["annotations"].append({"item_id": "channel", "judgment": "not_affected"})
                source = Path(tmp) / "cases.json"
                source.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, reason):
                    load_goldset(source)

    def test_versioned_annotations_require_provenance_even_if_both_keys_were_removed(self):
        published = Path(__file__).resolve().parents[1] / "goldsets" / "m0-compliance.json"
        data = json.loads(published.read_text(encoding="utf-8"))
        annotation = data["cases"][0]["annotations"][0]
        del annotation["baseline_claim_ids"]
        del annotation["baseline_evidence"]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cases.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "baseline_claim_ids"):
                load_goldset(source)

    def test_changed_blocks_cannot_reuse_the_same_version_id(self):
        data = goldset()
        data["cases"][0]["corpus"]["after"][0]["version"]["version_id"] = "v1"
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cases.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "revision pair.*reuse version_id"):
                load_goldset(source)

    def test_material_revision_evidence_must_point_into_after_snapshot(self):
        data = goldset()
        annotation = data["cases"][0]["annotations"][0]
        annotation["revised_text"] = "十四天"
        annotation["evidence"] = [{
            "document_id": "policy", "version_id": "v1", "block_key": "policy/1-七天",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cases.json"
            source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "revision.*outside the expected snapshot"):
                load_goldset(source)


class PublishedGoldSetTests(unittest.TestCase):
    def test_published_cases_cover_the_m0_calibration_scenarios(self):
        path = Path(__file__).resolve().parents[1] / "goldsets" / "m0-compliance.json"
        cases = load_goldset(path).cases
        revisions = [case for case in cases if case.request.corpus.before != case.request.corpus.after]
        control = [case for case in cases if case.request.corpus.before == case.request.corpus.after]
        self.assertEqual(len(revisions), 5)
        self.assertEqual(len(control), 1)
        self.assertEqual({a["judgment"] for a in control[0].annotations}, {"not_affected"})
        self.assertTrue(any(
            {a["judgment"] for a in case.annotations} == {"immaterial", "not_affected"}
            for case in revisions
        ))
        annotations = [annotation for case in cases for annotation in case.annotations]
        self.assertEqual(len(annotations), 24)
        self.assertTrue(all(a["baseline_claim_ids"] and a["baseline_evidence"] for a in annotations))
        self.assertTrue(all(
            a["revised_text"] and a["evidence"] for a in annotations if a["judgment"] == "material"
        ))
        self.assertTrue(any(len(a["baseline_evidence"]) > 1 for a in annotations))
        self.assertTrue(any(
            "| 地区 | 限额 |" in block.text
            for case in revisions
            for document in case.request.corpus.after
            for block in document.version.blocks
        ))
