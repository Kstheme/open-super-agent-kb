"""Browser pages for automatic Word decision reviews and human confirmation."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from openkb.ingest.model import Locator
from openkb.ingest.sqlite import open_delivery_store

from .evidence_view import evidence_position
from .web import render
from .word_audit_reviews import process_imports


router = APIRouter()


def _review_page(request: Request, review_id: str, *, error: str | None = None) -> HTMLResponse:
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            review = store.word_audit_review(review_id)
            decision = store.word_audit_decision(review["decision_id"])
            version = store.version(review["version_id"])
    except KeyError:
        raise HTTPException(404, "找不到此决定复核。") from None
    positions = {
        proof["id"]: evidence_position(Locator(**proof["locator"]), proof["block_id"])
        for item in review["items"]
        for proof in (item["old_evidence"], item["new_evidence"])
        if proof is not None
    }
    return render(
        request, "word_decision_review.html", title="人工决定复核", review=review,
        decision=decision, version=version, positions=positions, error=error,
        status_code=409 if error else 200,
    )


@router.get("/word-audit-reviews", response_class=HTMLResponse)
def word_decision_reviews(request: Request):
    with open_delivery_store(request.app.state.settings.database) as store:
        process_imports(store)
        reviews = store.word_audit_reviews()
        entries = [(review, store.word_audit_decision(review["decision_id"])) for review in reviews]
    return render(request, "word_decision_reviews.html", title="决定复核", entries=entries)


@router.get("/word-audit-reviews/{review_id}", response_class=HTMLResponse)
def word_decision_review(request: Request, review_id: str):
    return _review_page(request, review_id)


@router.post("/word-audit-reviews/{review_id}/confirm")
def confirm_word_decision_review(
    request: Request, review_id: str, choice: str = Form(""),
    reason: str = Form(""), adjusted_choice: str = Form(""),
):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            store.confirm_word_audit_review(review_id, choice, reason, adjusted_choice)
    except KeyError:
        raise HTTPException(404, "找不到此决定复核。") from None
    except ValueError as error:
        return _review_page(request, review_id, error=str(error))
    return RedirectResponse(router.url_path_for("word_decision_review", review_id=review_id), status_code=303)
