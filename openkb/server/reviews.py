"""Browser review and proposal decisions for delivered checklists."""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from openkb.ingest.ledger import StoreError
from openkb.ingest.sqlite import open_delivery_store

from .documents import save_upload
from .reviewing import start_review
from .web import render


router = APIRouter()
REVIEW_STATES = {"done": "复核完成", "failed": "复核失败", "paused": "复核已暂停"}
JUDGMENTS = {"material": "规则有变化", "immaterial": "规则未变化", "needs_human": "需要人工核对"}


def _listing(request: Request, artifact_id: str | None, *, error: str | None = None) -> HTMLResponse:
    with open_delivery_store(request.app.state.settings.database) as store:
        completed = tuple(task for task in store.list_deliveries() if task.state == "done")
        if artifact_id is None and completed:
            artifact_id = completed[0].id
        artifact = None
        versions = {}
        if artifact_id:
            try:
                artifact = store.artifact(artifact_id)
            except (KeyError, ValueError):
                error = error or "找不到选中的清单。"
            else:
                versions = {
                    evidence["version_id"]: evidence["original_name"]
                    for item in artifact["items"] for evidence in item["evidence"]
                    if Path(evidence["original_name"]).suffix.lower() == ".docx"
                }
        previous = store.list_reviews()
    return render(
        request, "reviews.html", title="复核", artifact=artifact, completed=completed,
        versions=versions, previous=previous, states=REVIEW_STATES,
        error=error, status_code=400 if error else 200,
    )


@router.get("/reviews", response_class=HTMLResponse)
def reviews(request: Request, artifact_id: str | None = None):
    return _listing(request, artifact_id)


@router.post("/reviews/start")
def submit_revision(
    request: Request,
    artifact_id: str | None = Form(None),
    before_version: str | None = Form(None),
    source: UploadFile | None = File(None),
):
    if not artifact_id or not before_version or source is None:
        return _listing(request, artifact_id, error="请选择清单、资料版本和新版文件。")
    if Path(source.filename or "").suffix.lower() != ".docx":
        return _listing(request, artifact_id, error="请选择 Word（.docx）新版。")
    settings = request.app.state.settings
    try:
        with open_delivery_store(settings.database) as store:
            if Path(store.version(before_version).original_name).suffix.lower() != ".docx":
                raise ValueError("请选择 Word 资料版本。")
            report = save_upload(
                source, lambda path: start_review(store, artifact_id, before_version, path, config=settings.config),
            )
    except KeyError:
        return _listing(request, artifact_id, error="选中的清单或资料版本已不存在，请重新选择。")
    except (OSError, StoreError, ValueError) as error:
        return _listing(request, artifact_id, error=str(error))
    return RedirectResponse(f"/reviews/{report['id']}", status_code=303)


def _failure_message(report: dict) -> str | None:
    if report["state"] == "paused":
        return "复核达到运行预算。未完成条目可在下方查看。"
    if report["state"] == "failed":
        reason = report["error"] or ""
        if reason.startswith(("Model configuration", "Model endpoint preflight failed:")):
            return "模型配置或连接检查失败。"
        return "未能完成复核。请检查模型连接，或人工核对待核对条目。"
    return None


def _result(request: Request, review_id: str, *, error: str | None = None) -> HTMLResponse:
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            review = store.review(review_id)
            before = store.version(review["before_version"])
            after = store.version(review["after_version"])
            artifact = store.artifact(review["artifact_id"], review["artifact_version"])
    except (KeyError, ValueError):
        raise HTTPException(404, "找不到此复核记录。") from None
    return render(
        request, "review.html", title=REVIEW_STATES[review["state"]], review=review, artifact=artifact,
        before=before, after=after, states=REVIEW_STATES, judgments=JUDGMENTS,
        item_names={item["slot_id"]: item["title"] for item in artifact["items"]},
        failure=_failure_message(review), error=error, status_code=409 if error else 200,
    )


@router.get("/reviews/{review_id}", response_class=HTMLResponse)
def review_detail(request: Request, review_id: str):
    return _result(request, review_id)


@router.post("/reviews/{review_id}/proposals/{proposal_id}/{decision}")
def decide_proposal(
    request: Request, review_id: str, proposal_id: str,
    decision: Literal["accept", "reject"], reason: str | None = Form(None),
):
    try:
        with open_delivery_store(request.app.state.settings.database) as store:
            store.decide_review(
                review_id, proposal_id, accept=decision == "accept",
                reason=reason if decision == "reject" else None,
            )
    except KeyError:
        raise HTTPException(404, "找不到此复核提案。") from None
    except ValueError:
        return _result(request, review_id, error="提案已处理，或当前清单条目已更新。请核对清单历史。")
    return RedirectResponse(f"/reviews/{review_id}#proposal-{proposal_id}", status_code=303)
