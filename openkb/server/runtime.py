"""Paths shared by the installed CLI and the browser application."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    data_dir: Path
    config: Path

    @classmethod
    def from_paths(cls, data_dir: Path | None = None, config: Path | None = None) -> "AppSettings":
        directory = (data_dir if data_dir is not None else Path.home() / ".openkb").expanduser().resolve()
        config_path = (config if config is not None else directory / "config.toml").expanduser().resolve()
        return cls(directory, config_path)

    @property
    def database(self) -> Path:
        return self.data_dir / "kb.db"
