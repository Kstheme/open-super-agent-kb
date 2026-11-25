"""The packaged local browser application and shared page renderer."""

from pathlib import Path
import re

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.routing import NoMatchFound

from openkb.ingest.ledger import StoreError

from .runtime import AppSettings


_ASSETS = Path(__file__).parent / "web_assets"
_TEMPLATES = Environment(
    loader=FileSystemLoader(_ASSETS / "templates"),
    autoescape=select_autoescape(("html",)),
)
_LOOPBACK_HOST = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?", re.IGNORECASE)
_NAVIGATION = (
    ("workspace", "工作区"),
    ("documents", "资料"),
    ("checklists", "清单"),
    ("reviews", "复核"),
    ("word_audit_decisions", "审阅决定"),
    ("status", "连接状态"),
)


def render(request: Request, template: str, *, status_code: int = 200, **context) -> HTMLResponse:
    navigation = []
    for name, label in _NAVIGATION:
        try:
            path = request.app.url_path_for(name)
        except NoMatchFound:
            continue
        navigation.append((str(path), label))
    html = _TEMPLATES.get_template(template).render(
        request=request,
        navigation=navigation,
        **context,
    )
    return HTMLResponse(html, status_code=status_code)


def create_app(settings: AppSettings) -> FastAPI:
    from . import checklists, documents, reviews, word_audits, word_decision_reviews
    from .health import status

    app = FastAPI(title="OpenKB", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=_ASSETS / "static"), name="static")

    @app.exception_handler(StoreError)
    async def unavailable_ledger(request: Request, _error: StoreError) -> HTMLResponse:
        return render(request, "ledger_unavailable.html", title="账本不可用", status_code=503)

    @app.middleware("http")
    async def local_browser(request: Request, call_next):
        host = request.headers.get("host", "")
        if not _LOOPBACK_HOST.fullmatch(host):
            return PlainTextResponse("此应用仅接受本机地址。", status_code=400)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin", "").lower() != f"http://{host.lower()}":
                return PlainTextResponse("请求须来自当前本地应用。", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def workspace(request: Request):
        return word_audits.workspace(request)

    app.add_api_route("/status", status, name="status", response_class=HTMLResponse)
    app.include_router(documents.router)
    app.include_router(checklists.router)
    app.include_router(reviews.router)
    app.include_router(word_audits.router)
    app.include_router(word_decision_reviews.router)
    return app
