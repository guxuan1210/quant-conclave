"""Domain catalog consistency tests.

The catalog (quantconclave/catalog.py) is the single source of truth for the
role -> node/label/report-key mapping. These tests pin the canonical values and
assert that the graph, CLI, and web modules derive from it instead of drifting.
"""

from __future__ import annotations

import quantconclave.catalog as catalog


def test_roles_and_order_are_consistent():
    assert set(catalog.ANALYST_ROLES.keys()) == set(catalog.ANALYST_ORDER)
    assert len(catalog.ANALYST_ORDER) == 7


def test_ordered_roles_follows_canonical_order():
    keys = [r.key for r in catalog.ordered_roles()]
    assert keys == catalog.ANALYST_ORDER


def test_social_role_is_sentiment_not_social():
    # v0.2.5 renamed "Social Analyst" -> "Sentiment Analyst"; the wire key stays
    # "social" for saved-config back-compat.
    role = catalog.ANALYST_ROLES["social"]
    assert role.label == "Sentiment Analyst"
    assert role.node == "Sentiment Analyst"
    assert role.report_key == "sentiment_report"


def test_report_keys_are_consistent():
    for key, role in catalog.ANALYST_ROLES.items():
        assert role.report_key.endswith("_report"), key
        assert role.key == key


def test_graph_node_specs_derive_from_catalog():
    from quantconclave.graph.analyst_execution import ANALYST_NODE_SPECS
    assert set(ANALYST_NODE_SPECS.keys()) == set(catalog.ANALYST_ROLES.keys())
    for key, spec in ANALYST_NODE_SPECS.items():
        role = catalog.ANALYST_ROLES[key]
        assert spec.agent_node == role.node
        assert spec.tool_node == role.tool_node
        assert spec.clear_node == role.clear_node
        assert spec.report_key == role.report_key


def test_parallel_runner_report_keys_derive_from_catalog():
    from quantconclave.graph.parallel_analyst_runner import ANALYST_REPORT_KEYS
    expected = {
        k: r.report_key for k, r in catalog.ANALYST_ROLES.items() if k != "capital_flow"
    }
    assert ANALYST_REPORT_KEYS == expected


def test_cli_mappings_derive_from_catalog():
    import cli.main as cli_main
    assert cli_main.ANALYST_ORDER == catalog.ANALYST_ORDER
    assert cli_main.ANALYST_AGENT_NAMES == {k: r.label for k, r in catalog.ANALYST_ROLES.items()}
    assert cli_main.ANALYST_REPORT_MAP == {k: r.report_key for k, r in catalog.ANALYST_ROLES.items()}


def test_adjudicator_report_fields_derive_from_catalog():
    from quantconclave.graph.adjudicator import REPORT_FIELDS
    assert REPORT_FIELDS == [r.report_key for r in catalog.ordered_roles()]


def test_propagation_initial_state_has_all_report_keys():
    from quantconclave.graph.propagation import Propagator
    state = Propagator().create_initial_state("000001", "2026-09-13")
    for role in catalog.ANALYST_ROLES.values():
        assert state[role.report_key] == ""


def test_web_stream_mappings_derive_from_catalog():
    import web.stream as stream
    assert stream.ANALYST_REPORT_KEYS == {k: r.report_key for k, r in catalog.ANALYST_ROLES.items()}
    assert stream.ANALYST_NAMES == {k: r.label for k, r in catalog.ANALYST_ROLES.items()}
