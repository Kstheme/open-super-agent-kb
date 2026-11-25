"""Text for evidence positions shown in the browser."""

from openkb.ingest.model import Locator


def evidence_position(locator: Locator, block_id: str) -> str:
    if locator.table is not None and locator.row is not None:
        return f"第 {locator.table} 张表，第 {locator.row} 行"
    if locator.paragraph is not None:
        return f"第 {locator.paragraph} 段"
    if locator.page is not None:
        return f"第 {locator.page} 页"
    if locator.line_start is not None:
        if locator.line_end and locator.line_end != locator.line_start:
            return f"第 {locator.line_start}–{locator.line_end} 行"
        return f"第 {locator.line_start} 行"
    return f"资料块 {block_id}"
