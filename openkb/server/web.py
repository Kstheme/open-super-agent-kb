"""The packaged local browser application and shared page renderer."""

from pathlib import Path
import re

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .runtime import AppSettings


_ASSETS = Path(__file__).parent / "web_assets"
_TEMPLATES = Environment(
    loader=FileSystemLoader(_ASSETS / "templates"),
    autoescape=select_autoescape(("html",)),
)
_LOOPBACK_HOST = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?", re.IGNORECASE)
_NAVIGATION = (
    ("/", "工作区"),
    ("/documents", "资料"),
    ("/checklists", "清单"),
    ("/reviews", "复核"),
    ("/status", "连接状态"),
)


def render(request: Request, template: str, *, status_code: int = 200, **context) -> HTMLResponse:
    routes = {getattr(route, "path", None) for route in request.app.routes}
    html = _TEMPLATES.get_template(template).render(
        request=request,
        navigation=[(path, label) for path, label in _NAVIGATION if path in routes],
        **context,
    )
    return HTMLResponse(html, status_code=status_code)


def create_app(settings: AppSettings) -> FastAPI:
    app = FastAPI(title="OpenKB", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=_ASSETS / "static"), name="static")

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
        return render(request, "workspace.html", title="工作区")

    return app
