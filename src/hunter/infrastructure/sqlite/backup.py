"""Backup and restore of the SQLite database. (H-11, T-24)

Doc 12: after a restore, bans and deletions made after the backup must be re-applied, so an
old copy never silently lifts a contact ban. Before replacing the database, active bans of the
current database are read and inserted back into the restored copy; then integrity and
foreign keys are checked and table counts are reported for reconciliation.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hunter.infrastructure.sqlite.db import connect, migrate

_SUPPRESSION_COLUMNS = (
    "id, group_id, scope_type, scope_id, match_domain, match_address, reason, "
    "source_event_id, created_by, created_at"
)


@dataclass(frozen=True, slots=True)
class RestoreReport:
    bans_reapplied: int
    integrity: str
    foreign_key_errors: int
    counts: dict[str, int]


def backup(conn: sqlite3.Connection, target: Path) -> Path:
    """Consistent online copy through the SQLite backup API (works with WAL)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    dest = sqlite3.connect(str(target))
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


def restore(backup_path: Path, db_path: Path) -> RestoreReport:
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    current_bans: list[tuple] = []
    if db_path.exists():
        current = sqlite3.connect(str(db_path))
        try:
            current_bans = current.execute(
                f"SELECT {_SUPPRESSION_COLUMNS} FROM suppressions WHERE lifted_at IS NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            current_bans = []  # the current file predates bans
        finally:
            current.close()
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    shutil.copyfile(backup_path, db_path)

    conn = connect(db_path)
    try:
        migrate(conn)
        reapplied = 0
        conn.execute("BEGIN IMMEDIATE")
        for row in current_bans:
            exists = conn.execute(
                "SELECT lifted_at FROM suppressions WHERE id = ?", (row[0],)
            ).fetchone()
            if exists is None:
                conn.execute(
                    f"INSERT INTO suppressions ({_SUPPRESSION_COLUMNS}) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
                reapplied += 1
            elif exists[0] is not None:
                # Lifted in the old copy but active now: the current decision wins.
                conn.execute(
                    "UPDATE suppressions SET lifted_at = NULL, lifted_by = NULL, "
                    "lift_reason = NULL WHERE id = ?",
                    (row[0],),
                )
                reapplied += 1
        conn.execute("COMMIT")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        tables = ("companies", "leads", "messages", "suppressions", "audit_events")
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()
    return RestoreReport(reapplied, integrity, fk_errors, counts)
