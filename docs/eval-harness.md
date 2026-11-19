# M0 review evaluation interface

Install the package with `python -m pip install .`, then run a UTF-8 gold-set JSON file:

```sh
openkb eval run --goldset goldsets/m0-compliance.json --system noop --out report.json
```

The default system is `noop`, and the default destination is `report.json` in the working directory.

## Gold-set input

The JSON root contains `profile_version` (an identifier for the declaration template) and `cases` (an array). The report identifies the input by the SHA-256 hash of the file bytes, including annotations.

| Path within a case | Fields and values |
| --- | --- |
| Case | `case_id`, `corpus`, `artifact`, `goal`, `budget`, `annotations`, optional `ledger` |
| `corpus` | `before` and `after`, each an array of documents |
| Document | `document_id`, `version` |
| Version | `version_id`, `blocks` (array) |
| Block | Required `block_key` (non-empty string), `heading_path` (array of strings), `ordinal`, `start_offset`, `end_offset`, `text` |
| `artifact` | Array of items with stable `item_id` and `snapshot_text`, plus optional `section` and positive `ordinal` |
| `goal` | String |
| `budget` | `max_steps`, `max_tokens`, `max_wall_seconds` |
| `annotations` | Array of objects keyed by `item_id` and `judgment` (`material`, `immaterial`, or `not_affected`). The published gold set also records baseline claim and evidence references, plus the expected revision and its evidence for material annotations. |
| `ledger` | Optional object representing a ledger view |

The [published M0 gold set](../goldsets/m0-compliance.json) contains 24 annotations for five independent synthetic Chinese policy revisions and an unchanged control. It includes a lodging-limit table, cross-clause references in purchase and training rules, and a meeting-minutes revision that changes wording while preserving policy meaning.

Each published annotation has stable synthetic claim identifiers in `baseline_claim_ids` and references to blocks from the before snapshot in `baseline_evidence`. Material annotations also have `revised_text` and after-snapshot `evidence`. Each reference uses `document_id`, `version_id`, and `block_key`, matching the M0 review port. Minimal existing inputs can still provide just `item_id` and `judgment`.

Before running any case, the loader checks block keys and document identity on both sides. A document must keep identical blocks when the same version ID appears on both sides. The loader rejects unknown or duplicate annotation item IDs, missing annotations, invalid judgments, and evidence references outside the corresponding snapshot.

## Adapter and report boundaries

- Implement `openkb.eval.contract.ReviewSystem.review(request)` and return a `ReviewResult`. Proposals refer to stable artifact item IDs. An evidence reference locates a block by document, version, and block key. Cost carries token counts, an estimated amount, and the model ID. Timing is in seconds. Set `used_ledger` explicitly and use `gaps` or `error` to explain `paused` or `failed` results.
- Use `load_goldset(Path(...))` and `run_goldset(goldset, system, system_name=..., price_table_version=...)` to run one pass. The runner hands adapters `ReviewRequest` values. It records an adapter exception as a failed case with its error and continues. If an adapter throws after receiving a ledger, its ledger use is unverified and the fallback result carries `used_ledger: false`. Annotations remain available as `GoldCase.annotations` for scoring and test calibration.
- `RunReport.to_json()` serializes ordered `cases` as `{case_id, result}` records. Each result includes proposals, state, gaps, error, ledger use, timing, and cost. The report uses a single model ID from the case results. The version keys `harness_version`, `goldset_hash`, `system`, `model_id`, `profile_version`, and `price_table_version` are mandatory. `model_id` and `price_table_version` permit JSON `null` when unavailable or inapplicable, as does `cost.estimated_amount`. Use `validate_report_versions` on decoded reports before comparison.
- The runner scores each review against the case annotations. Calibration tests use the same runner with an oracle, an always-material adapter, and an adapter that throws.

## Metrics and repeated runs

Pass `--runs N` for positive integer `N`. The report lists each run in `runs`, with its own `cases`, `aggregate`, `excluded_cases`, and `incomplete_cases`. The top-level `cases` provides the first run's results and top-level `aggregate` provides the median of each metric across the runs.

- Recall is the share of material annotations that were judged material. The false-positive rate is the share of immaterial or unaffected annotations that were judged material. A zero denominator is JSON `null`. Each run takes the arithmetic mean over the applicable cases for each rate and lists excluded cases by metric.
- Failed or paused cases remain in the denominator, with zero material judgments counted. The original result and gaps remain in the case record. The run separately lists incomplete case IDs.
- Timing and cost totals sum the values from the review results. A missing component makes that total unknown. Estimated amount per correctly detected material item is the total estimated amount divided by the number of correctly detected material items. Unknown amounts and zero correct items give JSON `null`. Medians use applicable numeric values.
- `disagreement` gives the fraction of a case's annotated items whose effective judgment varies across runs. An absent proposal counts as unaffected. A failed or paused run contributes only unaffected judgments. With one run or no annotated items the rate is JSON `null`.

## Compare reports and configure the advisory summary

```sh
openkb eval run --goldset goldset.json --out baseline.json
openkb eval compare --report-a baseline.json --report-b candidate.json
```

The run summary lists the actual thresholds, report versions, excluded cases, and
an advisory conclusion. The JSON report includes the same thresholds under
`thresholds`. With no matching configuration, `recall_tolerance` defaults to
`0.05` and `cheaper_cost_ratio` defaults to `0.8`. Override either value in
`~/.openkb/config.toml` or a file passed to `--config`.

```toml
[eval.thresholds]
recall_tolerance = 0.05
cheaper_cost_ratio = 0.8
```

Both thresholds accept values between 0 and 1. The first is an absolute recall
difference and the second is a candidate-to-baseline cost ratio. The comparison
shows the aggregate values and signed B-minus-A deltas. Reports with different
gold-set hashes are marked incomparable and their deltas are suppressed.

## Regenerate comparison system

Configure a model with [the M0 model settings](model-budget.md) and run:

```sh
openkb eval run --goldset goldsets/m0-compliance.json --system regenerate --out regenerate.json
```

Use the same gold-set file for both systems. The report records the configured model and price-table versions. Pass an alternate TOML path with `--config` when needed. Model endpoint preflight runs once before case evaluation. A missing configuration or unavailable endpoint stops the command with a reason.

The system regenerates the checklist from the complete after snapshot, goal, and baseline section outline. It aligns all known item IDs first, then pairs remaining items by section and ordinal before asking for semantic judgments. Only baseline item IDs enter the scored output. Older artifacts without section metadata are grouped in one section, with ordinals assigned in existing item order. Both model calls share a case budget and a paused run retains the original case gaps and accumulated usage.

The report includes a note about the comparison system's ability to read the full changed corpus without retrieval. `regenerate` uses no ledger.
