"""Browser delivery, checklist history and retained evidence."""

from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openkb.ingest.ledger import Ledger
from openkb.ingest.model import Locator
from openkb.ingest.sqlite import open_delivery_store
from openkb.profiles.word_accessibility import CHECKLIST_TITLE

from .delivery import start_delivery
from .web import render


router = APIRouter()


def _listing(request: Request, *, error: str | None = None) -> HTMLResponse:
    with open_delivery_store(request.app.state.settings.database) as store:
        versions = store.list_versions()
        deliveries = store.list_deliveries()
    return render(
        request, "checklists.html", title="清单", checklist_title=CHECKLIST_TITLE,
        versions=versions, deliveries=deliveries, error=error, status_code=400 if error else 200,
    )


@router.get("/checklists", response_class=HTMLResponse)
def checklists(request: Request):
    return _listing(request)


@router.post("/checklists/generate")
def generate_checklist(request: Request, source: list[str] | None = Form(None)):
    if not source:
        return _listing(request, error="请选择至少一个资料版本。")
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            task = start_delivery(store, CHECKLIST_TITLE, source)
    except KeyError:
        return _listing(request, error="选中的资料版本已不存在，请重新选择。")
    if task["state"] == "paused":
        return RedirectResponse(f"/checklists/tasks/{task['id']}", status_code=303)
    return RedirectResponse(f"/checklists/{task['id']}", status_code=303)


@router.get("/checklists/tasks/{task_id}", response_class=HTMLResponse)
def checklist_task(request: Request, task_id: str):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            task = store.delivery(task_id)
    except KeyError:
        raise HTTPException(404, "找不到此清单任务。") from None
    if task["artifact"] is not None:
        return RedirectResponse(f"/checklists/{task['artifact']['id']}", status_code=303)
    return render(request, "checklist_task.html", title="交付已暂停", task=task)


@router.get("/checklists/{artifact_id}", response_class=HTMLResponse)
def checklist(
    request: Request,
    artifact_id: str,
    version: int | None = Query(None, ge=1),
    evidence: str | None = None,
):
    selected = None
    position = None
    with open_delivery_store(request.app.state.settings.database) as store:
        try:
            artifact = store.artifact(artifact_id, version)
            history = store.artifact_versions(artifact_id)
        except (KeyError, ValueError):
            raise HTTPException(404, "找不到此清单版本。") from None
        if evidence is not None:
            if not any(
                reference["id"] == evidence
                for item in artifact["items"]
                for reference in item["evidence"]
            ):
                raise HTTPException(404, "此清单版本没有引用该证据。")
            try:
                selected = Ledger(store).locate_evidence(evidence)
            except KeyError:
                raise HTTPException(404, "找不到此原文证据。") from None
            position = _position(selected.locator, selected.block_id)
    return render(
        request, "checklist.html", title=artifact["title"], artifact=artifact, history=history,
        selected_evidence=selected, position=position,
    )


def _position(locator: Locator, block_id: str) -> str:
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


@router.get("/evidence/{evidence_id}", response_class=HTMLResponse)
def evidence_detail(request: Request, evidence_id: str):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            evidence = Ledger(store).locate_evidence(evidence_id)
    except KeyError:
        raise HTTPException(404, "找不到此原文证据。") from None
    return render(
        request, "evidence.html", title="原文证据", evidence=evidence,
        position=_position(evidence.locator, evidence.block_id),
    )


@router.get("/evidence/{evidence_id}/original")
def evidence_original(request: Request, evidence_id: str):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            ledger = Ledger(store)
            evidence = ledger.locate_evidence(evidence_id)
            original = ledger.original(evidence.version_id)
    except KeyError:
        raise HTTPException(404, "找不到此证据对应的原件。") from None
    filename = quote(evidence.original_name, safe="")
    return Response(
        original, media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
