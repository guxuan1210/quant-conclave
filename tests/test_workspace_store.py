"""Workspace Persistence Module tests — path rule + legacy migration."""

from __future__ import annotations

import sqlite3

from quantconclave.workspace import store


def _create_legacy(db_path, table, columns, rows):
    conn = sqlite3.connect(str(db_path))
    cols = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(f"CREATE TABLE {table} ({cols})")
    conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
    conn.commit()
    conn.close()


def test_get_db_path_uses_results_dir():
    assert store.get_db_path({"results_dir": "/tmp/x"}) == store.get_db_path({"results_dir": "/tmp/x"})
    assert store.get_db_path({"results_dir": "/tmp/x"}).name == "results.db"
    assert str(store.get_db_path({"results_dir": "/tmp/foo"})).endswith("results.db")


def test_migrate_legacy_copies_and_is_idempotent(tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    monkeypatch.setattr(store, "get_legacy_db_path", lambda: legacy_dir / "capitalradar.db")

    _create_legacy(
        legacy_dir / "capitalradar.db",
        "advisory_experiences",
        ["id", "content", "category", "status"],
        [(1, "lesson one", "risk", "active"), (2, "lesson two", "flow", "pending_review")],
    )
    _create_legacy(
        legacy_dir / "capitalradar.db",
        "strategy_templates",
        ["id", "name", "type"],
        [(1, "ma_cross", "custom_llm")],
    )

    config = {"results_dir": str(tmp_path / "results")}
    # Destination schema must exist first (as init_db guarantees in prod).
    conn = store.get_connection(config)
    conn.execute(
        "CREATE TABLE advisory_experiences (id INTEGER PRIMARY KEY, content TEXT, "
        "source_ticker TEXT, source_date TEXT, outcome TEXT, raw_return REAL, "
        "category TEXT, status TEXT, lesson_abstract TEXT, created_at TEXT)"
    )
    conn.execute("CREATE TABLE strategy_templates (id INTEGER PRIMARY KEY, name TEXT, type TEXT)")
    conn.commit()
    conn.close()

    assert store.migrate_legacy_db(config) == 3
    assert store.migrate_legacy_db(config) == 0  # idempotent — no re-copy

    conn = store.get_connection(config)
    n = conn.execute("SELECT COUNT(*) FROM advisory_experiences").fetchone()[0]
    assert n == 2
    conn.close()


def test_migrate_legacy_noop_without_legacy_file(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "get_legacy_db_path", lambda: tmp_path / "nope.db")
    assert store.migrate_legacy_db({"results_dir": str(tmp_path / "r")}) == 0
