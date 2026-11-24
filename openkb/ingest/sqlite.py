import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .model import Block, Evidence, Locator, TableCell, Version


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
CREATE UNIQUE INDEX IF NOT EXISTS source_blocks_version_id ON source_blocks(version_id, id);
CREATE TABLE IF NOT EXISTS source_evidence (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    block_id TEXT NOT NULL,
    quote TEXT NOT NULL,
    quote_start INTEGER NOT NULL CHECK (quote_start >= 0),
    quote_end INTEGER NOT NULL CHECK (quote_end > quote_start),
    quote_hash TEXT NOT NULL,
    FOREIGN KEY (version_id, block_id) REFERENCES source_blocks (version_id, id)
);
"""


def _block(data: str) -> Block:
    fields = json.loads(data)
    fields["heading_path"] = tuple(fields["heading_path"])
    locator = fields["locator"]
    if locator["bbox"] is not None:
        locator["bbox"] = tuple(locator["bbox"])
    if "table_cells" in locator:
        locator["table_cells"] = tuple(TableCell(**cell) for cell in locator["table_cells"])
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

    def save_evidence(self, evidence: Evidence) -> Evidence:
        with closing(self._connect()) as conn, conn:
            self._insert_evidence(conn, evidence)
        return evidence

    @staticmethod
    def _insert_evidence(conn: sqlite3.Connection, evidence: Evidence) -> None:
        conn.execute(
            """INSERT INTO source_evidence
               (id, version_id, block_id, quote, quote_start, quote_end, quote_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
            (evidence.id, evidence.version_id, evidence.block_id, evidence.quote,
             evidence.quote_start, evidence.quote_end, evidence.quote_hash),
        )

    def evidence(self, evidence_id: str) -> Evidence:
        with closing(self._connect()) as conn:
            fields = conn.execute(
                """SELECT id, version_id, block_id, quote, quote_start, quote_end, quote_hash
                   FROM source_evidence WHERE id = ?""",
                (evidence_id,),
            ).fetchone()
        if fields is None:
            raise KeyError(f"Unknown evidence: {evidence_id}")
        return Evidence(*fields)
