import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .model import Block, Locator, Version


_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS source_versions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES source_documents(id),
    original_name TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    warnings TEXT NOT NULL,
    original BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS source_blocks (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES source_versions(id),
    ordinal INTEGER NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (version_id, ordinal)
);
"""


def _block(data: str) -> Block:
    fields = json.loads(data)
    fields["heading_path"] = tuple(fields["heading_path"])
    locator = fields["locator"]
    if locator["bbox"] is not None:
        locator["bbox"] = tuple(locator["bbox"])
    fields["locator"] = Locator(**locator)
    return Block(**fields)


class SqliteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def save(self, version: Version, original: bytes) -> Version:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO source_documents (id) VALUES (?)", (version.document_id,))
            inserted = conn.execute(
                """INSERT OR IGNORE INTO source_versions
                   (id, document_id, original_name, content_hash, normalization_version, warnings, original)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (version.id, version.document_id, version.original_name, version.content_hash,
                 version.normalization_version, json.dumps(version.warnings), original),
            ).rowcount
            if inserted:
                conn.executemany(
                    "INSERT INTO source_blocks (id, version_id, ordinal, data) VALUES (?, ?, ?, ?)",
                    ((block.id, version.id, block.ordinal, json.dumps(asdict(block), ensure_ascii=False))
                     for block in version.blocks),
                )
        return version if inserted else self.version(version.id)

    def version(self, version_id: str) -> Version:
        with closing(self._connect()) as conn:
            fields = conn.execute(
                """SELECT id, document_id, original_name, content_hash, normalization_version, warnings
                   FROM source_versions WHERE id = ?""",
                (version_id,),
            ).fetchone()
            if fields is None:
                raise KeyError(f"Unknown version: {version_id}")
            blocks = conn.execute(
                "SELECT data FROM source_blocks WHERE version_id = ? ORDER BY ordinal", (version_id,),
            )
            return Version(*fields[:5], tuple(_block(row[0]) for row in blocks), tuple(json.loads(fields[5])))

    def original(self, version_id: str) -> bytes:
        with closing(self._connect()) as conn:
            data = conn.execute("SELECT original FROM source_versions WHERE id = ?", (version_id,)).fetchone()
            if data is None:
                raise KeyError(f"Unknown version: {version_id}")
            return data[0]
