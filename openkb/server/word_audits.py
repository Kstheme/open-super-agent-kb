"""Browser entry and evidence view for Word audit requests."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from openkb.ingest.model import Locator
from openkb.ingest.sqlite import open_delivery_store

from .evidence_view import evidence_position
from .web import render
from .word_audit import audit_sources, run_word_audit


router = APIRouter()


def workspace(request: Request, *, goal: str = "", error: str | None = None) -> HTMLResponse:
    with open_delivery_store(request.app.state.settings.database) as store:
        versions = audit_sources(store)
    return render(
        request, "workspace.html", title="工作区", versions=versions, goal=goal,
        error=error, status_code=400 if error else 200,
    )


@router.post("/word-audits")
def start_word_audit(
    request: Request, goal: str = Form(""), source: list[str] | None = Form(None),
):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            audit = run_word_audit(store, goal, source or ())
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
    except KeyError:
        raise HTTPException(404, "找不到此审阅。") from None

    positions = {
        proof["id"]: evidence_position(Locator(**proof["locator"]), proof["block_id"])
        for item in audit["covered"]
        for proof in item["evidence"]
    }
    return render(
        request, "word_audit.html", title="Word 交付审阅", audit=audit, positions=positions,
        slots={slot["id"]: slot["title"] for slot in audit["skeleton"]},
    )
