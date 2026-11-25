"""Browser import and browsing for retained source versions."""

from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from shutil import copyfileobj
from tempfile import TemporaryDirectory
from typing import TypeVar
from uuid import uuid4

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from openkb.ingest.ledger import Ledger, StoreError
from openkb.ingest.parsers import PARSERS
from openkb.ingest.sqlite import open_delivery_store

from .web import render


router = APIRouter()
T = TypeVar("T")


def save_upload(source: UploadFile, consume: Callable[[Path], T]) -> T:
    """Provide a temporary local file with its original, validated basename."""
    filename = source.filename or ""
    if (
        not filename or filename != filename.rstrip(" .")
        or any(char in '<>:"/\\|?*' or ord(char) < 32 for char in filename)
        or PureWindowsPath(filename).is_reserved()
    ):
        raise ValueError("文件名无效，请选择具有普通文件名的资料。")
    if Path(filename).suffix.lower() not in PARSERS:
        raise ValueError("不支持此资料格式。请选择 Markdown、PDF 或 DOCX。")
    with TemporaryDirectory(prefix="openkb-upload-") as directory:
        path = Path(directory) / filename
        with path.open("xb") as target:
            copyfileobj(source.file, target)
        return consume(path)


def _page(request: Request, *, imported: str | None = None, error: str | None = None) -> HTMLResponse:
    settings = request.app.state.settings
    with open_delivery_store(settings.database) as store:
        versions = store.list_versions()
    imported_version = next((version for version in versions if version.id == imported), None)
    return render(
        request, "documents.html", title="资料", versions=versions,
        imported=imported_version, error=error, status_code=400 if error else 200,
    )


@router.get("/documents", response_class=HTMLResponse)
def documents(request: Request, imported: str | None = None):
    return _page(request, imported=imported)


@router.post("/documents/import")
def import_document(request: Request, source: UploadFile | None = File(None)):
    if source is None:
        return _page(request, error="请选择要导入的文件。")
    try:
        settings = request.app.state.settings
        with open_delivery_store(settings.database) as store:
            version = save_upload(source, lambda path: Ledger(store).add(path, uuid4().hex))
    except (OSError, StoreError, ValueError) as error:
        return _page(request, error=str(error))
    return RedirectResponse(f"/documents?imported={version.id}", status_code=303)
