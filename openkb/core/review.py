"""Find the evidence that requires review after a source revision."""

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable


def align_blocks(before: list[dict], after: list[dict]) -> dict:
    old = defaultdict(list)
    new = defaultdict(list)
    for block in before:
        old[block["block_key"]].append(block)
    for block in after:
        new[block["block_key"]].append(block)

    matches, ambiguous, added, removed = [], [], [], []
    for key in dict.fromkeys((*old, *new)):
        previous, current = old.get(key, ()), new.get(key, ())
        if not previous:
            added.extend(current)
        elif not current:
            removed.extend(previous)
        elif len(previous) == len(current) == 1:
            matches.append({"before": previous[0], "after": current[0]})
        else:
            ambiguous.append({"block_key": key, "before": previous, "after": current})
    return {"matches": matches, "ambiguous": ambiguous, "added": added, "removed": removed}


def _structural_candidates(block: dict, added: list[dict], anchors: list[dict], ordinals: list[int]) -> list[dict]:
    """Use retained neighbors to narrow possible replacements without assigning identity."""
    index = bisect_left(ordinals, block["ordinal"])
    lower = anchors[index - 1]["after"]["ordinal"] if index else 0
    upper = anchors[index]["after"]["ordinal"] if index < len(anchors) else float("inf")
    return [
        candidate for candidate in added
        if lower < candidate["ordinal"] < upper
        and candidate["heading_path"] == block["heading_path"]
        and candidate["clause"] == block["clause"]
        and candidate["kind"] == block["kind"]
    ]


def trace_review(
    items: list[dict],
    alignment: dict,
    rebase: Callable[[dict, dict], dict | None],
) -> dict:
    """Derive affected items from recorded evidence and deterministic block alignment."""
    result = {"affected": [], "rebased_evidence": [], "alignment_warnings": []}
    before_version = alignment["before_version"]
    after_version = alignment["after_version"]
    if before_version == after_version:
        return result

    anchors = sorted(alignment["matches"], key=lambda pair: pair["before"]["ordinal"])
    ordinals = [pair["before"]["ordinal"] for pair in anchors]
    matched = {pair["before"]["id"]: pair for pair in anchors}
    removed = {block["id"]: block for block in alignment["removed"]}
    ambiguous = {
        block["id"]: (block, group["after"])
        for group in alignment["ambiguous"]
        for block in group["before"]
    }
    warnings = result["alignment_warnings"]
    for group in alignment["ambiguous"]:
        warnings.append({
            "kind": "ambiguous_block_key",
            "block_key": group["block_key"],
            "before_block_ids": [block["id"] for block in group["before"]],
            "after_block_ids": [block["id"] for block in group["after"]],
        })
    for side, alerts in alignment["warnings"].items():
        warnings.extend({"kind": "source_warning", "side": side, "message": alert} for alert in alerts)

    for item in items:
        changes = []
        item_id = item["slot_id"]
        for evidence in item["evidence"]:
            if evidence["version_id"] != before_version:
                continue
            block_id = evidence["block_id"]
            if block_id in matched:
                previous, current = matched[block_id]["before"], matched[block_id]["after"]
                if previous["text"] == current["text"]:
                    located = rebase(evidence, current)
                    if located is not None:
                        result["rebased_evidence"].append({
                            "item_id": item_id, "old_evidence": evidence, "new_evidence": located,
                        })
                        continue
                    status = "ambiguous"
                else:
                    status = "changed"
                candidates = [current]
            elif block_id in ambiguous:
                previous, candidates = ambiguous[block_id]
                status = "ambiguous"
            else:
                previous = removed[block_id]
                candidates = _structural_candidates(previous, alignment["added"], anchors, ordinals)
                status = "ambiguous" if len(candidates) > 1 else "changed" if candidates else "removed"

            if status == "ambiguous" and block_id not in ambiguous:
                warnings.append({
                    "kind": "uncertain_evidence_alignment",
                    "before_block_ids": [block_id],
                    "after_block_ids": [block["id"] for block in candidates],
                })
            changes.append({
                "old_evidence": evidence,
                "before_block": previous,
                "status": status,
                "candidate_blocks": candidates,
            })
        if changes:
            result["affected"].append({
                "item_id": item_id, "claim_id": item["claim_id"], "item": item, "changes": changes,
            })

    return result
