"""Initialize and serve a single local workspace."""

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from openkb.agent.model import load_model_config
from openkb.ingest.ledger import StoreError
from openkb.ingest.sqlite import open_delivery_store

from .runtime import AppSettings


def configure(commands):
    initialize = commands.add_parser("init", help="Prepare the local data directory and model configuration")
    serve = commands.add_parser("serve", help="Open the local Web application on 127.0.0.1")
    for command in (initialize, serve):
        command.add_argument("--data-dir", type=Path, help="Local workspace (default: ~/.openkb)")
    initialize.add_argument("--config", type=Path, help="Copy an existing model configuration into the workspace")
    initialize.add_argument("--base-url", help="OpenAI-compatible API root when creating a new configuration")
    initialize.add_argument("--model-id", help="Model identifier when creating a new configuration")
    serve.add_argument("--config", type=Path, help="Use an existing model configuration file directly")
    serve.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    return {"init": initialize, "serve": serve}


def _write_new_config(target: Path, data: bytes) -> None:
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError(f"Existing model configuration left unchanged: {target}")
        return
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


def _initialize(args) -> int:
    settings = AppSettings.from_paths(args.data_dir)
    if args.config is not None and (args.base_url is not None or args.model_id is not None):
        raise ValueError("Choose --config or --base-url and --model-id")
    if (args.base_url is None) != (args.model_id is None):
        raise ValueError("--base-url and --model-id are required together")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.config is not None:
        source = args.config.expanduser().resolve()
        load_model_config(source)
        _write_new_config(settings.config, source.read_bytes())
    elif args.base_url is not None:
        if settings.config.exists():
            raise ValueError(f"Existing model configuration left unchanged: {settings.config}")
        secret = getpass("模型 API 密钥: ")
        contents = (
            "[model]\n"
            f"base_url = {json.dumps(args.base_url, ensure_ascii=False)}\n"
            f"model_id = {json.dumps(args.model_id, ensure_ascii=False)}\n"
            f"api_key = {json.dumps(secret, ensure_ascii=False)}\n"
            '\n[prices]\nversion = "local"\n'
        )
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=settings.data_dir, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(contents)
            load_model_config(temporary)
            _write_new_config(settings.config, contents.encode("utf-8"))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    with open_delivery_store(settings.database):
        pass
    print(f"本地账本已就绪 {settings.database}")
    if settings.config.is_file():
        print(f"模型配置 {settings.config}")
    else:
        print("尚未配置模型。可通过 openkb init --config 导入现有配置。")
    return 0


def run(args, parser: argparse.ArgumentParser) -> int:
    try:
        if args.command == "init":
            return _initialize(args)
        if not 1 <= args.port <= 65535:
            raise ValueError("--port must be between 1 and 65535")
        settings = AppSettings.from_paths(args.data_dir, args.config)
        if not settings.database.is_file():
            raise ValueError(f"Local workspace not initialized: {settings.data_dir}. Run openkb init first.")
        with open_delivery_store(settings.database):
            pass

        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, proxy_headers=False)
        return 0
    except (OSError, StoreError, ValueError, EOFError) as error:
        parser.error(str(error))
