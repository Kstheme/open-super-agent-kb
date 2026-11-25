import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from openkb.core.delivery import DeliveryConflict

from .claims import Claim, compliance_claim
from .ledger import StoreError
from .model import Block, DeliverySummary, Evidence, Locator, ReviewSummary, TableCell, Version, VersionSummary
from .batch import BatchFile


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
CREATE TABLE IF NOT EXISTS ingest_batch_files (
    batch_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    path TEXT NOT NULL,
    version_id TEXT REFERENCES source_versions(id),
    error TEXT,
    PRIMARY KEY (batch_id, path),
    UNIQUE (batch_id, ordinal),
    CHECK (version_id IS NULL OR error IS NULL)
);
"""


_CLAIM_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_claims (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES source_versions(id),
    profile TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_claims_version ON ledger_claims(version_id);
CREATE TABLE IF NOT EXISTS ledger_claim_evidence (
    claim_id TEXT NOT NULL REFERENCES ledger_claims(id),
    evidence_id TEXT NOT NULL REFERENCES source_evidence(id),
    PRIMARY KEY (claim_id, evidence_id)
);
"""


_DELIVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS deliver_tasks (
    id TEXT PRIMARY KEY,
    skeleton_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skeleton_proposals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES deliver_tasks(id),
    title TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS skeleton_proposals_task ON skeleton_proposals(task_id);
CREATE TABLE IF NOT EXISTS delivery_conflicts (
    id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL,
    source_version TEXT NOT NULL REFERENCES source_versions(id),
    reason TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open', 'resolved'))
);
CREATE INDEX IF NOT EXISTS open_delivery_conflicts_version
    ON delivery_conflicts(source_version) WHERE state = 'open';
"""


_WORD_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS word_audits (
    id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS word_audit_decisions (
    id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL REFERENCES word_audits(id),
    choice TEXT NOT NULL CHECK (choice IN ('release', 'return')),
    reason TEXT NOT NULL,
    audit_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS word_audit_decisions_audit ON word_audit_decisions(audit_id);
CREATE TABLE IF NOT EXISTS word_audit_review_imports (
    version_id TEXT PRIMARY KEY REFERENCES source_versions(id),
    decision_boundary INTEGER NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS word_audit_reviews (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES word_audit_decisions(id),
    version_id TEXT NOT NULL REFERENCES source_versions(id),
    snapshot_json TEXT NOT NULL,
    UNIQUE (decision_id, version_id)
);
CREATE TABLE IF NOT EXISTS word_audit_review_confirmations (
    review_id TEXT PRIMARY KEY REFERENCES word_audit_reviews(id),
    choice TEXT NOT NULL CHECK (choice IN ('keep', 'adjust')),
    resulting_choice TEXT NOT NULL CHECK (resulting_choice IN ('release', 'return')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_tasks (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES deliver_tasks(id),
    before_version TEXT NOT NULL REFERENCES source_versions(id),
    after_version TEXT NOT NULL REFERENCES source_versions(id),
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_revisions (
    artifact_id TEXT NOT NULL REFERENCES deliver_tasks(id),
    version INTEGER NOT NULL CHECK (version >= 2),
    snapshot_json TEXT NOT NULL,
    PRIMARY KEY (artifact_id, version)
);
CREATE TABLE IF NOT EXISTS edit_marks (
    review_id TEXT NOT NULL REFERENCES review_tasks(id),
    proposal_id TEXT NOT NULL,
    claim_id TEXT NOT NULL REFERENCES ledger_claims(id),
    reason TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (review_id, proposal_id)
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


def check_database(path: Path) -> None:
    """Read the existing ledger schema without creating or migrating a database."""
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA schema_version").fetchone()
    except sqlite3.Error as error:
        raise StoreError(str(error)) from error


@contextmanager
def open_delivery_store(path: Path) -> Iterator["SqliteStore"]:
    """Expose delivery storage failures through the ledger boundary."""
    try:
        yield SqliteStore(path)
    except sqlite3.Error as error:
        raise StoreError(str(error)) from error


class SqliteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA + _CLAIM_SCHEMA + _DELIVERY_SCHEMA + _REVIEW_SCHEMA + _WORD_AUDIT_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def save(self, version: Version, original: bytes) -> Version:
        with closing(self._connect()) as conn, conn:
            inserted = self._insert_version(conn, version, original)
        return version if inserted else self.version(version.id)

    @staticmethod
    def _insert_version(conn: sqlite3.Connection, version: Version, original: bytes) -> bool:
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
            conn.execute(
                """INSERT INTO word_audit_review_imports (version_id, decision_boundary)
                   SELECT ?, boundary FROM (SELECT MAX(rowid) AS boundary FROM word_audit_decisions)
                   WHERE boundary IS NOT NULL AND EXISTS (
                       SELECT 1 FROM source_versions WHERE document_id = ? AND id <> ?
                   )""",
                (version.id, version.document_id, version.id),
            )
        return bool(inserted)

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

    def list_versions(self, *, document_id: str | None = None) -> tuple[VersionSummary, ...]:
        query = "SELECT id, document_id, original_name, content_hash, warnings FROM source_versions"
        parameters = ()
        if document_id is not None:
            query += " WHERE document_id = ?"
            parameters = (document_id,)
        with closing(self._connect()) as conn:
            rows = conn.execute(query + " ORDER BY rowid DESC", parameters)
            return tuple(VersionSummary(*row[:4], tuple(json.loads(row[4]))) for row in rows)

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
            return self._evidence(conn, evidence_id)

    @staticmethod
    def _evidence(conn: sqlite3.Connection, evidence_id: str) -> Evidence:
        fields = conn.execute(
            """SELECT id, version_id, block_id, quote, quote_start, quote_end, quote_hash
               FROM source_evidence WHERE id = ?""",
            (evidence_id,),
        ).fetchone()
        if fields is None:
            raise KeyError(f"Unknown evidence: {evidence_id}")
        return Evidence(*fields)

    def start_batch(self, batch_id: str, paths: Sequence[Path]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO ingest_batch_files (batch_id, ordinal, path) VALUES (?, ?, ?)",
                ((batch_id, index, str(path)) for index, path in enumerate(paths, 1)),
            )

    def batch_files(self, batch_id: str) -> tuple[BatchFile, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT path, version_id, error FROM ingest_batch_files
                   WHERE batch_id = ? ORDER BY ordinal""",
                (batch_id,),
            )
            files = tuple(BatchFile(Path(path), version_id, error) for path, version_id, error in rows)
        if not files:
            raise KeyError(f"Unknown ingest batch: {batch_id}")
        return files

    def finish_file(self, batch_id: str, item: BatchFile) -> None:
        with closing(self._connect()) as conn, conn:
            self._finish_file(conn, batch_id, item)

    def save_batch_version(self, batch_id: str, path: Path, version: Version, original: bytes) -> Version:
        with closing(self._connect()) as conn, conn:
            # Serialize competing submissions before checking the file's current state.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version_id FROM ingest_batch_files WHERE batch_id = ? AND path = ?",
                (batch_id, str(path)),
            ).fetchone()
            if row is None:
                raise KeyError(f"File is not in ingest batch {batch_id}: {path}")
            completed_version = row[0]
            if completed_version is None:
                inserted = self._insert_version(conn, version, original)
                self._finish_file(conn, batch_id, BatchFile(path, version.id, None))
                if inserted:
                    return version
                completed_version = version.id
        return self.version(completed_version)

    @staticmethod
    def _finish_file(conn: sqlite3.Connection, batch_id: str, item: BatchFile) -> None:
        conn.execute(
            """UPDATE ingest_batch_files SET version_id = ?, error = ?
               WHERE batch_id = ? AND path = ? AND version_id IS NULL""",
            (item.version_id, item.error, batch_id, str(item.path)),
        )

    def save_claims(self, claims: tuple[Claim, ...]) -> None:
        with closing(self._connect()) as conn, conn:
            for claim in claims:
                self._insert_claim(conn, claim)

    @classmethod
    def _insert_claim(cls, conn: sqlite3.Connection, claim: Claim) -> None:
        if not claim.evidence or any(item.version_id != claim.version_id for item in claim.evidence):
            raise ValueError("Claim evidence must bind to its source version")
        for evidence in claim.evidence:
            cls._insert_evidence(conn, evidence)
        conn.execute(
            """INSERT INTO ledger_claims (id, version_id, profile, text)
               VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
            (claim.id, claim.version_id, claim.profile, claim.text),
        )
        conn.executemany(
            """INSERT INTO ledger_claim_evidence (claim_id, evidence_id)
               VALUES (?, ?) ON CONFLICT(claim_id, evidence_id) DO NOTHING""",
            ((claim.id, item.id) for item in claim.evidence),
        )

    def claims(self, version_id: str) -> tuple[Claim, ...]:
        with closing(self._connect()) as conn:
            if conn.execute("SELECT 1 FROM source_versions WHERE id = ?", (version_id,)).fetchone() is None:
                raise KeyError(f"Unknown version: {version_id}")
            rows = conn.execute(
                """SELECT c.id, c.version_id, c.profile, c.text,
                          e.id, e.version_id, e.block_id, e.quote, e.quote_start, e.quote_end, e.quote_hash
                   FROM ledger_claims c
                   JOIN ledger_claim_evidence ce ON ce.claim_id = c.id
                   JOIN source_evidence e ON e.id = ce.evidence_id
                   WHERE c.version_id = ? ORDER BY c.id, e.id""",
                (version_id,),
            )
            records = {}
            for row in rows:
                evidence = records.setdefault(row[:4], [])
                evidence.append(Evidence(*row[4:]))
        return tuple(Claim(*fields, tuple(evidence)) for fields, evidence in records.items())

    def save_delivery(self, task: dict) -> tuple[DeliveryConflict, ...]:
        payload = {key: value for key, value in task.items() if key not in {"skeleton", "skeleton_proposals"}}
        with closing(self._connect()) as conn, conn:
            if task["state"] == "done":
                conn.execute("BEGIN IMMEDIATE")
                conflicts = self._open_delivery_conflicts(conn, task["sources"])
                if conflicts:
                    return conflicts
            conn.execute(
                "INSERT INTO deliver_tasks (id, skeleton_json, result_json) VALUES (?, ?, ?)",
                (task["id"], json.dumps(task["skeleton"], ensure_ascii=False),
                 json.dumps(payload, ensure_ascii=False)),
            )
        return ()

    def save_word_audit(self, audit: dict, *, previous: dict | None = None) -> None:
        with closing(self._connect()) as conn, conn:
            if previous is not None:
                conn.execute("BEGIN IMMEDIATE")
                current = self._word_audit(conn, audit["id"])
                if current != previous:
                    raise ValueError("此委托已有新结果，请重新核对。")
            conn.execute(
                """INSERT INTO word_audits (id, snapshot_json) VALUES (?, ?)
                   ON CONFLICT (id) DO UPDATE SET snapshot_json = excluded.snapshot_json""",
                (audit["id"], json.dumps(audit, ensure_ascii=False)),
            )

    def word_audit(self, audit_id: str) -> dict:
        with closing(self._connect()) as conn:
            return self._word_audit(conn, audit_id)

    @staticmethod
    def _word_audit(conn: sqlite3.Connection, audit_id: str) -> dict:
        row = conn.execute(
            "SELECT snapshot_json FROM word_audits WHERE id = ?", (audit_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Word audit: {audit_id}")
        return json.loads(row[0])

    @staticmethod
    def word_audit_snapshot_id(audit: dict) -> str:
        snapshot = json.dumps(audit, ensure_ascii=False, sort_keys=True)
        return sha256(snapshot.encode("utf-8")).hexdigest()

    def record_word_audit_decision(
        self, audit_id: str, choice: str, reason: str, *, answer_snapshot: str | None = None,
    ) -> dict:
        if choice not in ("release", "return"):
            raise ValueError("请选择放行或退回修改。")
        reason = reason.strip()
        if not reason:
            raise ValueError("请填写决定理由。")
        decision_id = uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            audit = self._word_audit(conn, audit_id)
            if answer_snapshot is not None and answer_snapshot != self.word_audit_snapshot_id(audit):
                raise ValueError("审阅建议已更新，请核对后再保存决定。")
            if not audit["covered"]:
                raise ValueError("当前审阅暂无有依据的建议。")
            conn.execute(
                """INSERT INTO word_audit_decisions (id, audit_id, choice, reason, audit_snapshot_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (decision_id, audit_id, choice, reason, json.dumps(audit, ensure_ascii=False)),
            )
            return self._word_audit_decision(conn, decision_id)

    def word_audit_decision(self, decision_id: str) -> dict:
        with closing(self._connect()) as conn:
            return self._word_audit_decision(conn, decision_id)

    def _word_audit_decision(self, conn: sqlite3.Connection, decision_id: str) -> dict:
        row = conn.execute(
            """SELECT id, audit_id, choice, reason, created_at, audit_snapshot_json
               FROM word_audit_decisions WHERE id = ?""",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Word audit decision: {decision_id}")
        return self._decision_snapshot(row)

    def word_audit_decisions(self, audit_id: str | None = None) -> tuple[dict, ...]:
        query = """SELECT id, audit_id, choice, reason, created_at, audit_snapshot_json
                   FROM word_audit_decisions"""
        where, parameters = (" WHERE audit_id = ?", (audit_id,)) if audit_id is not None else ("", ())
        with closing(self._connect()) as conn:
            rows = conn.execute(query + where + " ORDER BY rowid DESC", parameters)
            return tuple(self._decision_snapshot(row) for row in rows)

    @staticmethod
    def _decision_snapshot(row: tuple) -> dict:
        decision_id, audit_id, choice, reason, created_at, audit_json = row
        return {
            "id": decision_id,
            "audit_id": audit_id,
            "choice": choice,
            "reason": reason,
            "created_at": created_at,
            "audit": json.loads(audit_json),
        }

    def pending_word_audit_imports(self) -> tuple[str, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT version_id FROM word_audit_review_imports WHERE processed = 0 ORDER BY rowid",
            )
            return tuple(row[0] for row in rows)

    def word_audit_import_decisions(self, version_id: str) -> tuple[dict, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT id, audit_id, choice, reason, created_at, audit_snapshot_json
                   FROM word_audit_decisions WHERE rowid <= (
                       SELECT decision_boundary FROM word_audit_review_imports WHERE version_id = ?
                   )""",
                (version_id,),
            )
            return tuple(self._decision_snapshot(row) for row in rows)

    def finish_word_audit_import(self, version_id: str, reviews: Sequence[dict]) -> None:
        with closing(self._connect()) as conn, conn:
            processed = conn.execute(
                "UPDATE word_audit_review_imports SET processed = 1 WHERE version_id = ? AND processed = 0",
                (version_id,),
            ).rowcount
            if processed:
                conn.executemany(
                    """INSERT INTO word_audit_reviews (id, decision_id, version_id, snapshot_json)
                       VALUES (?, ?, ?, ?)""",
                    ((review["id"], review["decision_id"], version_id, json.dumps(review, ensure_ascii=False))
                     for review in reviews),
                )

    def word_audit_review(self, review_id: str) -> dict:
        with closing(self._connect()) as conn:
            return self._word_audit_review(conn, review_id)

    @staticmethod
    def _word_audit_review(conn: sqlite3.Connection, review_id: str) -> dict:
        row = conn.execute(
            """SELECT review.snapshot_json, confirmation.choice, confirmation.resulting_choice,
                      confirmation.reason, confirmation.created_at
               FROM word_audit_reviews AS review LEFT JOIN word_audit_review_confirmations AS confirmation
                 ON confirmation.review_id = review.id
               WHERE review.id = ?""",
            (review_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Word audit review: {review_id}")
        return SqliteStore._word_audit_review_snapshot(row)

    @staticmethod
    def _word_audit_review_snapshot(row: tuple) -> dict:
        snapshot, choice, resulting_choice, reason, created_at = row
        review = json.loads(snapshot)
        review["confirmation"] = None
        if choice is not None:
            review["state"] = "confirmed"
            review["confirmation"] = {
                "choice": choice, "resulting_choice": resulting_choice,
                "reason": reason, "created_at": created_at,
            }
        return review

    def word_audit_reviews(self, decision_id: str | None = None) -> tuple[dict, ...]:
        where, parameters = (" WHERE review.decision_id = ?", (decision_id,)) if decision_id else ("", ())
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT review.snapshot_json, confirmation.choice, confirmation.resulting_choice,
                          confirmation.reason, confirmation.created_at
                   FROM word_audit_reviews AS review LEFT JOIN word_audit_review_confirmations AS confirmation
                     ON confirmation.review_id = review.id""" + where + " ORDER BY review.rowid DESC",
                parameters,
            )
            return tuple(self._word_audit_review_snapshot(row) for row in rows)

    def confirm_word_audit_review(self, review_id: str, choice: str, reason: str,
                                  adjusted_choice: str = "") -> None:
        if choice not in {"keep", "adjust"} or (choice == "adjust" and adjusted_choice not in {"release", "return"}):
            raise ValueError("请选择保留原决定、放行或退回修改。")
        reason = reason.strip()
        if not reason:
            raise ValueError("请填写复核理由。")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            review = self._word_audit_review(conn, review_id)
            if review["state"] != "pending":
                raise ValueError("这项复核已处理或暂不可确认。")
            decision = self._word_audit_decision(conn, review["decision_id"])
            resulting_choice = adjusted_choice if choice == "adjust" else decision["choice"]
            conn.execute(
                """INSERT INTO word_audit_review_confirmations
                   (review_id, choice, resulting_choice, reason) VALUES (?, ?, ?, ?)""",
                (review_id, choice, resulting_choice, reason),
            )

    def flag_delivery_conflict(self, conflict: DeliveryConflict) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO delivery_conflicts (id, slot_id, source_version, reason, state)
                   VALUES (?, ?, ?, ?, ?)""",
                (conflict.id, conflict.slot_id, conflict.source_version, conflict.reason, conflict.state),
            )

    def resolve_delivery_conflict(self, conflict_id: str) -> DeliveryConflict:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE delivery_conflicts SET state = 'resolved' WHERE id = ?", (conflict_id,))
            row = conn.execute(
                "SELECT id, slot_id, source_version, reason, state FROM delivery_conflicts WHERE id = ?",
                (conflict_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown delivery conflict: {conflict_id}")
            return DeliveryConflict(*row)

    def open_delivery_conflicts(self, source_versions: Sequence[str]) -> tuple[DeliveryConflict, ...]:
        with closing(self._connect()) as conn:
            return self._open_delivery_conflicts(conn, source_versions)

    @staticmethod
    def _open_delivery_conflicts(
        conn: sqlite3.Connection, source_versions: Sequence[str],
    ) -> tuple[DeliveryConflict, ...]:
        if not source_versions:
            return ()
        parameters = ", ".join("?" for _ in source_versions)
        rows = conn.execute(
            f"""SELECT id, slot_id, source_version, reason, state FROM delivery_conflicts
                WHERE state = 'open' AND source_version IN ({parameters}) ORDER BY rowid""",
            source_versions,
        )
        return tuple(DeliveryConflict(*row) for row in rows)

    def list_deliveries(self) -> tuple[DeliverySummary, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT d.id, json_extract(d.result_json, '$.artifact.title'),
                          json_extract(d.result_json, '$.state'),
                          CASE WHEN json_extract(d.result_json, '$.state') = 'done'
                               THEN COALESCE(revisions.latest, 1) END
                   FROM deliver_tasks AS d
                   LEFT JOIN (
                       SELECT artifact_id, MAX(version) AS latest
                       FROM artifact_revisions GROUP BY artifact_id
                   ) AS revisions ON revisions.artifact_id = d.id
                   ORDER BY d.rowid DESC"""
            )
            return tuple(DeliverySummary(*row) for row in rows)

    def delivery(self, task_id: str) -> dict:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT skeleton_json, result_json FROM deliver_tasks WHERE id = ?", (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown delivery task: {task_id}")
            proposals = conn.execute(
                "SELECT id, task_id, title, state FROM skeleton_proposals WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            )
            return {
                **json.loads(row[1]),
                "skeleton": json.loads(row[0]),
                "skeleton_proposals": [
                    {"id": proposal_id, "task_id": task_id, "title": title, "state": state}
                    for proposal_id, task_id, title, state in proposals
                ],
            }

    def save_skeleton_proposal(self, proposal: dict) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO skeleton_proposals (id, task_id, title, state) VALUES (?, ?, ?, ?)",
                (proposal["id"], proposal["task_id"], proposal["title"], proposal["state"]),
            )

    def artifact_versions(self, artifact_id: str) -> tuple[int, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT version FROM artifact_revisions WHERE artifact_id = ?
                   UNION ALL
                   SELECT 1 FROM deliver_tasks
                   WHERE id = ? AND json_extract(result_json, '$.state') = 'done'
                   ORDER BY version DESC""",
                (artifact_id, artifact_id),
            )
            return tuple(row[0] for row in rows)

    def artifact(self, artifact_id: str, version: int | None = None) -> dict:
        with closing(self._connect()) as conn:
            return self._artifact(conn, artifact_id, version)

    @staticmethod
    def _artifact(conn: sqlite3.Connection, artifact_id: str, version: int | None = None) -> dict:
        if version is None:
            latest = conn.execute(
                """SELECT snapshot_json FROM artifact_revisions
                   WHERE artifact_id = ? ORDER BY version DESC LIMIT 1""",
                (artifact_id,),
            ).fetchone()
            if latest:
                return json.loads(latest[0])
        elif version != 1:
            revision = conn.execute(
                "SELECT snapshot_json FROM artifact_revisions WHERE artifact_id = ? AND version = ?",
                (artifact_id, version),
            ).fetchone()
            if revision is None:
                raise KeyError(f"Unknown artifact version: {artifact_id} version {version}")
            return json.loads(revision[0])
        row = conn.execute("SELECT result_json FROM deliver_tasks WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown delivery task: {artifact_id}")
        snapshot = json.loads(row[0])["artifact"]
        if snapshot is None:
            raise ValueError(f"Delivery task has no completed artifact: {artifact_id}")
        return {**snapshot, "version": 1}

    def save_review(self, review: dict) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO review_tasks (id, artifact_id, before_version, after_version, result_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (review["id"], review["artifact_id"], review["before_version"], review["after_version"],
                 json.dumps(review, ensure_ascii=False)),
            )

    def list_reviews(self) -> tuple[ReviewSummary, ...]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT r.id, json_extract(d.result_json, '$.artifact.title'),
                          json_extract(r.result_json, '$.artifact_version'), v.original_name,
                          json_extract(r.result_json, '$.state')
                   FROM review_tasks AS r
                   JOIN deliver_tasks AS d ON d.id = r.artifact_id
                   JOIN source_versions AS v ON v.id = r.after_version
                   ORDER BY r.rowid DESC"""
            )
            return tuple(ReviewSummary(*row) for row in rows)

    def review(self, review_id: str) -> dict:
        with closing(self._connect()) as conn:
            return self._review(conn, review_id)

    @staticmethod
    def _review(conn: sqlite3.Connection, review_id: str) -> dict:
        row = conn.execute("SELECT result_json FROM review_tasks WHERE id = ?", (review_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown review: {review_id}")
        return json.loads(row[0])

    def decide_review(self, review_id: str, proposal_id: str, *, accept: bool, reason: str | None = None) -> dict:
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            review = self._review(conn, review_id)
            proposal = next((item for item in review["proposals"] if item["id"] == proposal_id), None)
            if proposal is None:
                raise KeyError(f"Unknown proposal: {proposal_id}")
            status = "accepted" if accept else "rejected"
            reason = (reason or "").strip() or None
            if proposal["status"] != "pending":
                decision = proposal["decision"]
                if (decision["status"], decision["reason"]) == (status, reason):
                    return decision
                raise ValueError(f"Proposal already {proposal['status']}: {proposal_id}")

            artifact_id = review["artifact_id"]
            artifact = self._artifact(conn, artifact_id)
            version = artifact["version"]
            if accept:
                version = self._accept_review_item(conn, review, artifact, proposal)
            elif reason is not None:
                conn.execute(
                    """INSERT INTO edit_marks (review_id, proposal_id, claim_id, reason)
                       VALUES (?, ?, ?, ?)""",
                    (review_id, proposal_id, proposal["claim_id"], reason),
                )
            decision = {
                "review_id": review_id, "proposal_id": proposal_id, "status": status,
                "artifact_id": artifact_id, "artifact_version": version, "reason": reason,
            }
            proposal.update(status=status, decision=decision)
            conn.execute(
                "UPDATE review_tasks SET result_json = ? WHERE id = ?",
                (json.dumps(review, ensure_ascii=False), review_id),
            )
            return decision

    @classmethod
    def _accept_review_item(cls, conn: sqlite3.Connection, review: dict, artifact: dict, proposal: dict) -> int:
        slot_id = proposal["item_id"]
        item = next((item for item in artifact["items"] if item["slot_id"] == slot_id), None)
        if item is None or item != proposal["old_item"]:
            raise ValueError(f"Stale proposal for {slot_id}: the current item has changed")
        proofs = tuple(cls._evidence(conn, reference["id"]) for reference in proposal["evidence"])
        claim = compliance_claim(review["after_version"], proposal["revised_text"], proofs)
        cls._insert_claim(conn, claim)
        revised = {
            **item, "claim_id": claim.id, "check": claim.text,
            "method": proposal["revised_method"], "evidence": proposal["evidence"],
        }
        version = artifact["version"] + 1
        snapshot = {
            **artifact, "version": version,
            "items": [revised if existing["slot_id"] == slot_id else existing for existing in artifact["items"]],
        }
        conn.execute(
            "INSERT INTO artifact_revisions (artifact_id, version, snapshot_json) VALUES (?, ?, ?)",
            (artifact["id"], version, json.dumps(snapshot, ensure_ascii=False)),
        )
        return version
