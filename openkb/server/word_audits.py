"""Browser entry and evidence view for Word audit requests."""

from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from openkb.ingest.docx import inspect_table_headers
from openkb.ingest.model import Locator, VersionSummary
from openkb.ingest.sqlite import SqliteStore, open_delivery_store

from .documents import save_upload
from .evidence_view import evidence_position
from .web import render
from .word_audit import audit_sources, run_word_audit
from .word_audit_reviews import process_imports
from .word_audit_workflow import audit_workflow


router = APIRouter()


def _workflow_links(store, audit: dict, decision: dict | None = None) -> tuple:
    if decision is None:
        decision = next(iter(store.word_audit_decisions(audit["id"])), None)
    review = next(iter(store.word_audit_reviews(decision["id"])), None) if decision else None
    return decision, review


def workspace(request: Request, *, goal: str = "", error: str | None = None) -> HTMLResponse:
    with open_delivery_store(request.app.state.settings.database) as store:
        process_imports(store)
        versions = audit_sources(store)
        reviews = store.word_audit_reviews()
        review = next((entry for entry in reviews if entry["state"] in {"pending", "failed"}), None)
        if review:
            decision = store.word_audit_decision(review["decision_id"])
            audit = decision["audit"]
        else:
            audit = store.latest_word_audit()
            decision, review = _workflow_links(store, audit) if audit else (None, None)
    return render(
        request, "workspace.html", title="工作区", versions=versions, goal=goal,
        word_reviews=reviews, error=error, status_code=400 if error else 200,
        workflow=audit_workflow(request, audit, page="workspace", decision=decision, review=review),
    )


def inspect_deliverable(path: Path) -> dict:
    original = path.read_bytes()
    return {
        "original_name": path.name,
        "content_hash": sha256(original).hexdigest(),
        "table_headers": inspect_table_headers(original),
    }


@router.post("/word-audits")
def start_word_audit(
    request: Request, goal: str = Form(""), source: list[str] | None = Form(None),
    deliverable: UploadFile | None = File(None),
):
    try:
        if not goal.strip():
            raise ValueError("请输入审阅目标。")
        with open_delivery_store(request.app.state.settings.database) as store:
            inspected = None
            if deliverable is not None and deliverable.filename:
                if Path(deliverable.filename).suffix.casefold() != ".docx":
                    raise ValueError("请上传 DOCX 交付件。")
                inspected = save_upload(deliverable, inspect_deliverable)
            audit = run_word_audit(store, goal, source or (), deliverable=inspected)
    except KeyError:
        return workspace(request, goal=goal, error="所选资料版本已不存在，请重新选择。")
    except ValueError as error:
        return workspace(request, goal=goal, error=str(error))
    return RedirectResponse(router.url_path_for("word_audit", audit_id=audit["id"]), status_code=303)


@router.get("/word-audits/{audit_id}", response_class=HTMLResponse)
def word_audit(request: Request, audit_id: str):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            audit = store.word_audit(audit_id)
            sources = audit_sources(store) if audit["gaps"] else ()
    except KeyError:
        raise HTTPException(404, "找不到此审阅。") from None

    return render_word_audit(request, audit, resume_sources=sources)


@router.post("/word-audits/{audit_id}/resume")
def resume_word_audit(
    request: Request, audit_id: str, source: list[str] | None = Form(None),
    answer_snapshot: str | None = Form(None),
):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            previous = store.word_audit(audit_id)
            if answer_snapshot and answer_snapshot != store.word_audit_snapshot_id(previous):
                return render_word_audit(
                    request, previous, resume_sources=audit_sources(store),
                    resume_error="此委托已有新结果，请重新核对。",
                )
            try:
                run_word_audit(store, previous["goal"], source or (), previous=previous)
            except (KeyError, ValueError):
                return render_word_audit(
                    request, store.word_audit(audit_id), resume_sources=audit_sources(store),
                    resume_error="续审没有完成，请核对当前结果或所选资料后重试。",
                )
    except KeyError:
        raise HTTPException(404, "找不到此审阅。") from None

    return RedirectResponse(router.url_path_for("word_audit", audit_id=audit_id), status_code=303)


def render_word_audit(
    request: Request, audit: dict, *, decision: dict | None = None, decision_error: str | None = None,
    resume_sources: tuple[VersionSummary, ...] = (), resume_error: str | None = None,
) -> HTMLResponse:
    with open_delivery_store(request.app.state.settings.database) as store:
        latest_decision, review = _workflow_links(store, audit, decision)
    workflow = audit_workflow(
        request, audit, page="decision" if decision else "audit", decision=latest_decision, review=review,
    )
    alternatives = [
        item for conflict in audit.get("evidence_conflicts", ()) for item in conflict["alternatives"]
    ]
    positions = {
        proof["id"]: evidence_position(Locator(**proof["locator"]), proof["block_id"])
        for item in (*audit["covered"], *alternatives)
        for proof in item["evidence"]
    }
    return render(
        request, "word_audit.html", title="Word 交付审阅", audit=audit, positions=positions,
        slots={slot["id"]: slot["title"] for slot in audit["skeleton"]},
        decision=decision, decision_error=decision_error, resume_sources=resume_sources, resume_error=resume_error,
        workflow=workflow, status_code=400 if decision_error or resume_error else 200,
        answer_snapshot=SqliteStore.word_audit_snapshot_id(audit),
    )


@router.get("/word-audit-decisions", response_class=HTMLResponse)
def word_audit_decisions(request: Request):
    with open_delivery_store(request.app.state.settings.database) as store:
        decisions = store.word_audit_decisions()
    return render(request, "word_audit_decisions.html", title="审阅决定历史", decisions=decisions)


@router.get("/word-audit-decisions/{decision_id}", response_class=HTMLResponse)
def word_audit_decision(request: Request, decision_id: str):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            decision = store.word_audit_decision(decision_id)
    except KeyError:
        raise HTTPException(404, "找不到此审阅决定。") from None
    return render_word_audit(request, decision["audit"], decision=decision)


@router.post("/word-audits/{audit_id}/decisions")
def record_word_audit_decision(
    request: Request, audit_id: str, choice: str = Form(""), reason: str = Form(""),
    answer_snapshot: str | None = Form(None),
):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            try:
                decision = store.record_word_audit_decision(
                    audit_id, choice, reason, answer_snapshot=answer_snapshot,
                )
            except ValueError as error:
                return render_word_audit(request, store.word_audit(audit_id), decision_error=str(error))
    except KeyError:
        raise HTTPException(404, "找不到此审阅。") from None
    return RedirectResponse(
        router.url_path_for("word_audit_decision", decision_id=decision["id"]), status_code=303,
    )
