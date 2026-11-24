from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .ledger import Ledger
from .model import Version
from .parsers import PARSERS


@dataclass(frozen=True)
class BatchFile:
    path: Path
    version_id: str | None
    error: str | None


class BatchStore(Protocol):
    def start_batch(self, batch_id: str, paths: Sequence[Path]) -> None: ...
    def batch_files(self, batch_id: str) -> tuple[BatchFile, ...]: ...
    def finish_file(self, batch_id: str, item: BatchFile) -> None: ...
    def save_batch_version(self, batch_id: str, path: Path, version: Version, original: bytes) -> Version: ...


Progress = Callable[[str, int, int, Path], None]


class Batch:
    def __init__(self, ledger: Ledger, store: BatchStore, progress: Progress) -> None:
        self.ledger = ledger
        self.store = store
        self.progress = progress

    def start(self, directory: Path) -> str:
        directory = directory.resolve(strict=True)
        if not directory.is_dir():
            raise ValueError(f"Expected a directory: {directory}")
        paths = sorted(
            path for path in directory.rglob("*")
            if path.suffix.lower() in PARSERS and path.is_file() and not path.is_symlink()
        )
        if not paths:
            raise ValueError(f"No supported documents in {directory}")
        batch_id = uuid4().hex
        self.store.start_batch(batch_id, paths)
        return batch_id

    def status(self, batch_id: str) -> dict:
        files = self.store.batch_files(batch_id)
        failures = [{"file": str(item.path), "reason": item.error} for item in files if item.error is not None]
        completed = [
            {"file": str(item.path), "version_id": item.version_id}
            for item in files if item.version_id is not None
        ]
        pending = len(files) - len(completed) - len(failures)
        state = "paused" if pending else "failed" if failures else "done"
        return {
            "batch_id": batch_id, "kind": "ingest", "state": state,
            "total": len(files), "completed": len(completed), "pending": pending,
            "failures": failures, "versions": completed,
        }

    def resume(self, batch_id: str, limit: int | None = None, retry: Path | None = None) -> dict:
        if limit is not None and limit < 1:
            raise ValueError("File budget must be at least 1")
        files = self.store.batch_files(batch_id)
        pending = [
            (index, item.path) for index, item in enumerate(files, 1)
            if item.version_id is None and item.error is None
        ]
        if retry is not None:
            target = retry.resolve()
            pending = [
                (index, item.path) for index, item in enumerate(files, 1)
                if item.path == target and item.error is not None
            ]
            if not pending:
                raise ValueError(f"File is not in this batch's failure list: {target}")

        try:
            for index, path in pending[:limit]:
                self.progress(batch_id, index, len(files), path)
                try:
                    self.ledger.add(path, save=self._batch_save(batch_id, path))
                except (OSError, ValueError) as error:
                    self.store.finish_file(batch_id, BatchFile(path, None, str(error) or type(error).__name__))
        except KeyboardInterrupt:
            report = self.status(batch_id)
            report["interrupted"] = True
            return report
        return self.status(batch_id)

    def _batch_save(self, batch_id: str, path: Path) -> Callable[[Version, bytes], Version]:
        def save(version: Version, original: bytes) -> Version:
            return self.store.save_batch_version(batch_id, path, version, original)

        return save
