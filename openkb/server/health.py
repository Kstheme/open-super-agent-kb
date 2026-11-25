"""Local resource and model connection diagnostics."""

from dataclasses import dataclass
from pathlib import Path
import re
from tempfile import TemporaryFile
from typing import Literal

from fastapi import Request
from fastapi.responses import HTMLResponse

from openkb.agent.model import EndpointError, ModelClient, load_model_config
from openkb.ingest.ledger import StoreError
from openkb.ingest.sqlite import check_database

from .runtime import AppSettings
from .web import render


@dataclass(frozen=True)
class Check:
    label: str
    state: Literal["通过", "失败", "未检查"]
    detail: str


def _directory_check(directory: Path) -> Check:
    try:
        with TemporaryFile(dir=directory) as probe:
            probe.write(b"openkb")
            probe.flush()
    except FileNotFoundError:
        return Check("数据目录", "失败", "数据目录不存在，请先运行 openkb init。")
    except OSError:
        return Check("数据目录", "失败", "无法在数据目录创建、写入或删除临时文件，请检查权限。")
    return Check("数据目录", "通过", "可创建、写入并删除临时文件。")


def _database_check(database: Path) -> Check:
    if not database.is_file():
        return Check("SQLite", "失败", "找不到账本，请先初始化或确认数据目录。")
    try:
        check_database(database)
    except StoreError as error:
        return Check("SQLite", "失败", f"无法打开并读取账本：{error}")
    return Check("SQLite", "通过", "账本可打开并读取。")


def _config_error(error: ValueError) -> str:
    if isinstance(error.__cause__, FileNotFoundError):
        return "找不到模型配置文件，请使用 openkb init 配置模型。"
    if isinstance(error.__cause__, OSError):
        return "无法读取模型配置文件，请检查文件权限。"
    return "模型配置无效，请检查 TOML 格式、模型地址、模型名称、密钥和价格表版本。"


def endpoint_failure_detail(error: EndpointError) -> str:
    """Describe known failures without exposing raw transport or provider text."""
    cause = error.__cause__ or error
    if isinstance(cause, TimeoutError):
        return "连接模型端点超时。"
    message = str(cause)
    http = re.fullmatch(r"HTTP ([1-5][0-9]{2})", message)
    if http:
        code = http.group(1)
        if code in {"401", "403"}:
            return f"端点拒绝认证（HTTP {code}），请检查模型凭据。"
        return f"模型端点返回 HTTP {code}，请检查服务状态和地址。"
    if message.startswith("connection failed:"):
        return "无法建立连接，请检查模型服务是否启动和端点地址是否正确。"
    if message.endswith("was absent from /models"):
        return "模型端点的列表中没有已配置的模型。"
    return "端点未返回有效的模型列表，请检查地址、模型凭据和模型名称。"


def model_status(config_path: Path) -> tuple[str | None, tuple[Check, Check]]:
    try:
        config = load_model_config(config_path)
    except ValueError as error:
        return None, (
            Check("模型配置", "失败", _config_error(error)),
            Check("模型端点", "未检查", "修正模型配置后可检测连接。"),
        )

    configured = Check("模型配置", "通过", "已读取有效配置。")
    try:
        ModelClient(config).preflight(timeout=5)
    except EndpointError as error:
        return config.model_id, (configured, Check("模型端点", "失败", endpoint_failure_detail(error)))
    return config.model_id, (configured, Check("模型端点", "通过", "端点可达，模型已列出。"))


def doctor(settings: AppSettings) -> int:
    directory = _directory_check(settings.data_dir)
    database = _database_check(settings.database)
    model, model_checks = model_status(settings.config)
    checks = (directory, database, *model_checks)
    for check in checks:
        print(f"[{check.state}] {check.label}  {check.detail}")
    if model:
        print(f"当前模型  {model}")
    return 0 if all(check.state == "通过" for check in checks) else 1


def status(request: Request) -> HTMLResponse:
    model, (config, endpoint) = model_status(request.app.state.settings.config)
    heading = {
        "通过": "端点可达",
        "失败": "端点不可达",
        "未检查": "端点待检查",
    }[endpoint.state]
    response = render(
        request, "status.html", title="连接状态", model=model,
        config=config, endpoint=endpoint, heading=heading,
    )
    response.headers["Cache-Control"] = "no-store"
    return response
