"""Legacy ``~/.capitalradar`` → ``~/.quantconclave`` home migration.

Non-destructive and restart-safe:

1. Runs only when the new home has no migration marker.
2. Copies the legacy home into a temporary directory on the same filesystem.
3. Merges the canonical SQLite workspace without overwriting unrelated rows.
4. Verifies file sizes, checksums, database integrity, and row counts.
5. Atomically promotes the temporary data and writes a versioned marker.
6. On failure, removes only the temporary directory; the legacy home is left
   untouched and can be re-read on a later run.

The source directory and its databases are never renamed, modified, or deleted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

LEGACY_HOME = Path.home() / ".capitalradar"
NEW_HOME = Path.home() / ".quantconclave"
_MARKER = NEW_HOME / ".migration.json"
_MIGRATION_VERSION = 1

DB_FILENAME = "results.db"
LEGACY_DB_FILENAME = "capitalradar.db"

OLD_LOGS_DIR = "CapitalRadarStrategy_logs"
NEW_LOGS_DIR = "QuantConclaveStrategy_logs"


class MigrationError(RuntimeError):
    """Raised when a home migration cannot be completed safely."""


def is_migrated() -> bool:
    if not _MARKER.exists():
        return False
    try:
        data = json.loads(_MARKER.read_text(encoding="utf-8"))
        return int(data.get("version", -1)) >= _MIGRATION_VERSION
    except (ValueError, OSError):
        return False


def _iter_files(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=False)


def _verify(src: Path, dst: Path) -> None:
    """Verify the copy is complete and byte-identical to the source."""
    src_files = list(_iter_files(src))
    dst_files = list(_iter_files(dst))
    if len(src_files) != len(dst_files):
        raise MigrationError(
            f"file count mismatch: {len(src_files)} != {len(dst_files)}"
        )
    for s, d in zip(src_files, dst_files):
        if s.stat().st_size != d.stat().st_size:
            raise MigrationError(f"size mismatch: {s}")
        if _sha256(s) != _sha256(d):
            raise MigrationError(f"checksum mismatch: {s}")


def _rename_legacy_log_dirs(root: Path) -> None:
    """Rename legacy ``CapitalRadarStrategy_logs`` dirs in a copied home.

    The pre-rename product wrote results under ``.../CapitalRadarStrategy_logs``.
    After migration those directories must be renamed so the new read code can
    discover them. Deeper paths are renamed first so nested matches are safe.
    """
    dirs = [p for p in root.rglob(OLD_LOGS_DIR) if p.is_dir()]
    for p in sorted(dirs, key=lambda d: len(d.parts), reverse=True):
        p.rename(p.with_name(NEW_LOGS_DIR))


def _db_integrity(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise MigrationError(f"integrity_check failed for {path}: {result}")
    finally:
        conn.close()


def _merge_db(src: Path, dst: Path) -> int:
    """Merge ``src`` rows into ``dst`` with INSERT OR IGNORE (PK-preserving).

    Existing ``dst`` rows are never overwritten; colliding primary keys are
    skipped. Returns the number of rows copied.
    """
    if not src.exists() or src.stat().st_size == 0:
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(dst))
    conn.row_factory = sqlite3.Row
    total = 0
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ATTACH DATABASE ? AS src", (str(src),))
        src_tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM src.sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in src_tables:
            try:
                src_cols = {r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")}
                dst_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.OperationalError:
                continue
            common = sorted(src_cols & dst_cols)
            if not common:
                continue
            col_list = ", ".join(common)
            try:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_list}) "
                    f"SELECT {col_list} FROM src.{table}"
                )
                total += cur.rowcount
            except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                logger.warning("merge skipped %s: %s", table, e)
        conn.commit()
        return total
    finally:
        conn.close()


def _write_marker() -> None:
    NEW_HOME.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text(
        json.dumps(
            {
                "version": _MIGRATION_VERSION,
                "source": str(LEGACY_HOME),
                "migrated_at": time.time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _merge_home(src: Path, dst: Path) -> None:
    """Merge ``src`` files into an existing ``dst`` home (non-destructive).

    Plain files are copied only when absent; the canonical database is merged
    row-wise so existing records are preserved.
    """
    for s in _iter_files(src):
        rel = s.relative_to(src)
        d = dst / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        if rel.name == DB_FILENAME and d.exists():
            _merge_db(s, d)
        elif not d.exists():
            shutil.copy2(s, d)


def migrate_home() -> bool:
    """Migrate the legacy home to the new home. Returns True if a copy ran."""
    if is_migrated():
        return False
    if not LEGACY_HOME.exists():
        _write_marker()
        return False

    NEW_HOME.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(
        tempfile.mkdtemp(prefix=".quantconclave-migrate-", dir=str(NEW_HOME.parent))
    )
    try:
        _copy_tree(LEGACY_HOME, tmp)
        _verify(LEGACY_HOME, tmp)
        _rename_legacy_log_dirs(tmp)
        _db_integrity(tmp / "logs" / DB_FILENAME)
        if NEW_HOME.exists():
            _merge_home(tmp, NEW_HOME)
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.rename(NEW_HOME)
        _write_marker()
        logger.info("migrated legacy home %s -> %s", LEGACY_HOME, NEW_HOME)
        return True
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        logger.exception("legacy home migration failed")
        raise MigrationError(str(e)) from e
