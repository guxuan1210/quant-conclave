"""Research Workspace Persistence Module — store.

Single owner of the SQLite path rule, the connection factory, and the legacy
``~/.capitalradar/capitalradar.db`` → ``results.db`` migration.

Historically the research workspace was split across **two** databases:
``results.db`` (analysis runs, picks, chat, watchlist, ...) and
``capitalradar.db`` (advisory experiences, strategy library, backtest runs),
with overlapping schemas and hard-coded paths spread across five modules.
Every consumer now resolves the *same* canonical path through
:func:`get_db_path` / :func:`get_connection`, so there is exactly one place
that decides WHERE the database lives.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from quantconclave.migration import LEGACY_HOME, NEW_HOME

logger = logging.getLogger(__name__)

DB_FILENAME = "results.db"
LEGACY_DB_FILENAME = "capitalradar.db"


def default_results_dir() -> Path:
    return NEW_HOME / "logs"


def get_db_path(config: Optional[dict] = None) -> Path:
    """Canonical workspace SQLite path: ``{results_dir}/results.db``."""
    results_dir = (config or {}).get("results_dir") or str(default_results_dir())
    return Path(results_dir) / DB_FILENAME


def get_legacy_db_path() -> Path:
    """Path of the pre-unification ``capitalradar.db`` (kept for migration)."""
    return LEGACY_HOME / LEGACY_DB_FILENAME


def get_config() -> dict:
    """Return the ambient config (single config source for pathless consumers)."""
    from quantconclave.dataflows.config import get_config as _cfg
    return _cfg()


def get_connection(config: Optional[dict] = None) -> sqlite3.Connection:
    """Open a connection to the canonical workspace DB with shared pragmas."""
    db_path = get_db_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Tables that historically lived in the legacy ``~/.capitalradar/capitalradar.db``
# and are now owned by results.db. Ordered so parents precede children
# (strategy_templates before strategy_versions) for FK-satisfying copies.
_LEGACY_TABLES = (
    "advisory_experiences",
    "advisory_experience_log",
    "strategy_templates",
    "strategy_versions",
    "backtest_runs",
)


def legacy_summary() -> dict:
    """Inventory the legacy DB (table -> row count) without migrating anything."""
    legacy = get_legacy_db_path()
    if not legacy.exists():
        return {}
    conn = sqlite3.connect(str(legacy))
    conn.row_factory = sqlite3.Row
    try:
        summary = {}
        for table in _LEGACY_TABLES:
            try:
                summary[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                continue
        return summary
    finally:
        conn.close()


def migrate_legacy_db(config: Optional[dict] = None) -> int:
    """Copy legacy ``capitalradar.db`` rows into the canonical ``results.db``.

    Idempotent and non-destructive: ``INSERT OR IGNORE`` on the primary key, so
    re-running is a no-op, existing canonical rows are never overwritten, and
    the legacy file is left untouched. Returns the number of rows copied.
    """
    legacy = get_legacy_db_path()
    if not legacy.exists():
        return 0
    canonical = get_db_path(config)
    if legacy.resolve() == canonical.resolve():
        return 0

    conn = get_connection(config)
    total = 0
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ATTACH DATABASE ? AS legacy", (str(legacy),))
        for table in _LEGACY_TABLES:
            try:
                leg_cols = {r[1] for r in conn.execute(f"PRAGMA legacy.table_info({table})")}
                cur_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.OperationalError:
                continue
            common = sorted(leg_cols & cur_cols)
            if not common:
                continue
            col_list = ", ".join(common)
            try:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_list}) "
                    f"SELECT {col_list} FROM legacy.{table}"
                )
                total += cur.rowcount
            except (sqlite3.OperationalError, sqlite3.IntegrityError) as e:
                logger.warning("legacy migration skipped %s: %s", table, e)
        conn.commit()
        return total
    finally:
        conn.close()
