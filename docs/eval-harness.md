# M0 review evaluation interface

Install the package with `python -m pip install .`, then run a UTF-8 gold-set JSON file:

```sh
openkb eval run --goldset goldset.json --system noop --out report.json
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
| `artifact` | Array of items with stable `item_id` and `snapshot_text` |
| `goal` | String |
| `budget` | `max_steps`, `max_tokens`, `max_wall_seconds` |
| `annotations` | Array of objects keyed by `item_id` and `judgment` (`material`, `immaterial`, or `not_affected`). Additional annotation fields are retained for #3. |
| `ledger` | Optional object representing a ledger view |

The loader checks `block_key` for every block on both sides before running any case. See the [two-case test input](../tests/test_eval_cli.py) for a concrete example. Issue #3 supplies the corpus and checks annotation coverage, uniqueness, and revision consistency.

## Adapter and report boundaries

- Implement `openkb.eval.contract.ReviewSystem.review(request)` and return a `ReviewResult`. Proposals refer to stable artifact item IDs. An evidence reference locates a block by document, version, and block key. Cost carries token counts, an estimated amount, and the model ID. Timing is in seconds. Set `used_ledger` explicitly and use `gaps` or `error` to explain `paused` or `failed` results.
- Use `load_goldset(Path(...))` and `run_goldset(goldset, system, system_name=..., price_table_version=...)` to run one pass. The runner hands adapters `ReviewRequest` values. It records an adapter exception as a failed case with its error and continues. If an adapter throws after receiving a ledger, its ledger use is unverified and the fallback result carries `used_ledger: false`. Annotations remain available as `GoldCase.annotations` for scoring and test calibration.
- `RunReport.to_json()` serializes ordered `cases` as `{case_id, result}` records. Each result includes proposals, state, gaps, error, ledger use, timing, and cost. The report uses a single model ID from the case results. The version keys `harness_version`, `goldset_hash`, `system`, `model_id`, `profile_version`, and `price_table_version` are mandatory. `model_id` and `price_table_version` permit JSON `null` when unavailable or inapplicable, as does `cost.estimated_amount`. Use `validate_report_versions` on decoded reports before comparison.
- Issue #5 adds scoring and aggregation from the case results and annotations. Issue #7 adds comparison and summaries. Issue #8 can inject a test adapter through the same runner. P1 owns the public representation of the section and ordinal that #6 needs for alignment.
