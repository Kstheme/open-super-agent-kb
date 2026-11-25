"""Build the Word workflow links from the persisted audit and decision."""

from fastapi import Request

from .word_audit_reviews import REVIEW_STATE_LABELS


def audit_workflow(
    request: Request, audit: dict | None = None, *, page: str = "audit",
    decision: dict | None = None, review: dict | None = None,
) -> dict:
    audit_href = ""
    decision_href = ""
    if audit:
        audit_href = str(request.app.url_path_for("word_audit", audit_id=audit["id"]))
    if decision:
        decision_href = str(request.app.url_path_for("word_audit_decision", decision_id=decision["id"]))

    review_state = review["state"] if review else None
    work_href = decision_href if review_state in {"pending", "failed"} else audit_href
    evidence_href = {"workspace": work_href, "review": decision_href}.get(page, "")
    historical = page in {"decision", "review"} or bool(decision_href and evidence_href == decision_href)
    decision_target = decision_href if decision and page != "decision" else "#word-audit-decision"
    if page == "workspace" and not decision:
        decision_target = audit_href + "#word-audit-decision"
    review_target = ""
    if review:
        review_target = "#decision-impact-title" if page == "review" else str(
            request.app.url_path_for("word_decision_review", review_id=review["id"]),
        )

    covered = {item["slot_id"] for item in audit["covered"]} if audit else set()
    conflicts = {item["slot_id"] for item in audit.get("evidence_conflicts", ())} if audit else set()
    audit_state = audit["state"] if audit else None
    nodes = []

    def node(key, label, status, target="", tone=""):
        nodes.append({"key": key, "label": label, "status": status, "target": target, "tone": tone})

    intake_target = "#word-audit-goal" if not audit else evidence_href + "#word-audit-answer"
    node("intake", "提交委托", "已提交" if audit else "待提交", intake_target, "done" if audit else "")

    for key, slot, label in (
        ("scope", "scope", "核对适用范围"), ("table", "data_tables", "核对表头规则"),
    ):
        finding = evidence_href + "#word-audit-" + slot
        next_item = finding if historical else evidence_href + "#word-audit-next"
        if slot == "data_tables" and audit and audit.get("needs_page_span") and not historical:
            node(key, label, "等待补充", next_item, "attention")
        elif slot in covered:
            node(key, label, "已查证", finding, "done")
        elif slot in conflicts:
            node(key, label, "证据冲突", finding, "attention")
        elif audit_state == "waiting":
            node(key, label, "等待补充", next_item, "attention")
        elif audit:
            node(key, label, "待查证", next_item, "attention")
        else:
            node(key, label, "待查证")

    if audit:
        answer_status = {
            "done": "建议已就绪", "waiting": "仍有待查证项", "paused": "预算已用完", "failed": "查证失败",
        }.get(audit_state, "仍有待查证项")
        node("answer", "审阅结论", answer_status, evidence_href + "#word-audit-answer",
             "done" if audit_state == "done" else "attention")
    else:
        node("answer", "审阅结论", "待形成")

    if decision:
        node("decision", "人工决定", "已记录", decision_target, "done")
    elif audit and audit.get("needs_page_span"):
        node("decision", "人工决定", "待审阅")
    elif covered:
        node("decision", "人工决定", "待确认", decision_target)
    else:
        node("decision", "人工决定", "待审阅")

    if review:
        node("review", "新版主动复核", REVIEW_STATE_LABELS[review_state], review_target,
             "attention" if review_state in {"pending", "failed"} else "done")
    elif decision:
        node("review", "新版主动复核", "等待新版", str(request.app.url_path_for("documents")))
    else:
        node("review", "新版主动复核", "等待决定")

    if review_state in {"pending", "failed"}:
        current = "review"
    elif not audit:
        current = "intake"
    elif audit_state != "done" and not decision:
        current = "scope" if "scope" not in covered else "table"
    elif not decision:
        current = "decision"
    else:
        current = "review"
    return {"nodes": nodes, "current": current, "steps": audit.get("budget", {}).get("steps") if audit else None}
