"""Find the evidence that requires review after a source revision."""

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


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


@dataclass(frozen=True)
class BlockTrace:
    before: dict
    status: Literal["unchanged", "changed", "ambiguous", "removed"]
    candidates: list[dict]
    uncertain_replacement: bool = False


def trace_blocks(alignment: dict, block_ids: Iterable[str]) -> tuple[dict[str, BlockTrace], list[dict]]:
    """Resolve cited blocks against an alignment, independently of their evidence or artifact."""
    anchors = sorted(alignment["matches"], key=lambda pair: pair["before"]["ordinal"])
    ordinals = [pair["before"]["ordinal"] for pair in anchors]
    matched = {pair["before"]["id"]: pair for pair in anchors}
    removed = {block["id"]: block for block in alignment["removed"]}
    ambiguous = {
        block["id"]: (block, group["after"])
        for group in alignment["ambiguous"]
        for block in group["before"]
    }
    warnings = []
    for group in alignment["ambiguous"]:
        warnings.append({
            "kind": "ambiguous_block_key",
            "block_key": group["block_key"],
            "before_block_ids": [block["id"] for block in group["before"]],
            "after_block_ids": [block["id"] for block in group["after"]],
        })
    for side, alerts in alignment.get("warnings", {}).items():
        warnings.extend({"kind": "source_warning", "side": side, "message": alert} for alert in alerts)

    traces = {}
    for block_id in dict.fromkeys(block_ids):
        if block_id in matched:
            previous, current = matched[block_id]["before"], matched[block_id]["after"]
            status = "unchanged" if previous["text"] == current["text"] else "changed"
            traces[block_id] = BlockTrace(previous, status, [current])
        elif block_id in ambiguous:
            previous, candidates = ambiguous[block_id]
            traces[block_id] = BlockTrace(previous, "ambiguous", candidates)
        else:
            previous = removed[block_id]
            candidates = _structural_candidates(previous, alignment["added"], anchors, ordinals)
            if len(candidates) > 1:
                traces[block_id] = BlockTrace(previous, "ambiguous", candidates, uncertain_replacement=True)
            else:
                traces[block_id] = BlockTrace(previous, "changed" if candidates else "removed", candidates)
    return traces, warnings
