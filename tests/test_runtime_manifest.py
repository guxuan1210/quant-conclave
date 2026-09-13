"""Runtime Manifest consistency tests.

The manifest is the single source of truth for ports/URLs. These tests
fail if the README or the source files drift from it — the exact class
of drift the manifest exists to prevent (README said 8002/8007 while
``run_web.py`` said 8003/8005).
"""

from __future__ import annotations

from pathlib import Path

from capitalradar.runtime_manifest import (
    HOST,
    as_dict,
    chart_port,
    chart_url,
    main_port,
    main_url,
    render_readme_table,
)

_ROOT = Path(__file__).resolve().parent.parent


def test_default_ports_are_the_launcher_ports():
    assert (main_port(), chart_port()) == (8003, 8005)


def test_urls_are_derived_from_ports():
    assert main_url() == f"http://{HOST}:{main_port()}"
    assert chart_url() == f"http://{HOST}:{chart_port()}"


def test_readme_table_matches_manifest():
    table = render_readme_table()
    assert f"`{main_url()}`" in table
    assert f"`{chart_url()}`" in table


def test_readme_documents_the_manifest_urls():
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert main_url() in readme
    assert chart_url() in readme


def test_as_dict_is_serializable_and_complete():
    d = as_dict()
    assert set(d) == {"main", "chart"}
    assert d["main"]["port"] == main_port()
    assert d["chart"]["port"] == chart_port()
    assert d["main"]["url"] == main_url()
