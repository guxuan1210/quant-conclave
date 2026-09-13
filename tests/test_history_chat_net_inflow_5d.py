"""Regression: history_chat run_smart_screening net_inflow_5d is 万元 net_5d.

``get_stock_moneyflow`` returns 万元 (raw tushare scale); ``net_5d`` is the 5-day
主力(超大+大单) net in 万元 — the field ``STRATEGY_FIELDS["net_inflow_5d"]``
documents as "5日主力净流入(万元), >5000万为强流入". The old mapping was

    cand["net_inflow_5d"] = mf.get("net_amount", 0) / 1e4

which was BOTH the wrong field (latest single day, not 5-day) AND the wrong
unit (万元→元), silently making every net_inflow_5d strategy threshold 1万倍 off:
a candidate with 8000万 5-day main-force inflow was evaluated at 0.12万, so a
5000万 threshold was never met and the candidate vanished. Fixed:

    cand["net_inflow_5d"] = mf.get("net_5d", mf.get("net_amount", 0))

Pure unit test: dataflow / vendor functions are monkeypatched, no network.
"""

from __future__ import annotations

from quantconclave.dataflows.config import get_config


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, messages):
        return _Resp(self._content)

    def bind_tools(self, tools):
        return self


_STRATEGY = (
    '{"name": "主力流入", "conditions": ['
    '{"field": "smart_money_score", "op": ">=", "value": 50},'
    '{"field": "net_inflow_5d", "op": ">=", "value": 5000}],'
    '"order_by": "smart_money_score", "limit": 10}'
)


def _apply_mocks(monkeypatch, mf_extra=None):
    monkeypatch.setattr(
        "quantconclave.sector_scan.rotation.get_rrg_data",
        lambda *a, **k: {"industries": [{"name": "半导体", "quadrant": "leading"}]},
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.smart_scanner.get_industry_stocks",
        lambda name: [{"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行"}],
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.smart_money_score.compute_smart_money_score",
        lambda code, cfg: (75.0, {}),
    )
    # get_stock_moneyflow returns 万元 (raw tushare scale). net_5d = 8000万 5-day
    # 主力 net must clear the 5000万 threshold. net_amount (latest single day,
    # also 万元) is what the OLD buggy `net_amount / 1e4` used → 0.12万, so the
    # threshold was never met and the candidate was silently dropped.
    mf = {
        "net_5d": 8000, "net_amount": 1200, "mf_ratio": 1.2,
        "trend": "up", "buy_elg_ratio": 55.0, "net_inflow_ratio": 0.3,
    }
    if mf_extra:
        mf.update(mf_extra)
    monkeypatch.setattr(
        "quantconclave.dataflows.eastmoney_sector.get_stock_moneyflow",
        lambda code, days=5: mf,
    )
    monkeypatch.setattr(
        "quantconclave.sector_scan.pick_tracker.record_pick",
        lambda *a, **k: None,
    )


def test_net_inflow_5d_uses_5day_wan_net_5d_not_net_amount_divided(monkeypatch):
    """A real 8000万 5-day 主力 net must clear the 5000万 strategy threshold —
    under the old `net_amount / 1e4` mapping it evaluated at 0.12万 and the
    candidate vanished with no survivors."""
    from web.history_chat import build_tool_set
    _apply_mocks(monkeypatch)
    tools = build_tool_set(get_config(), _FakeLLM(_STRATEGY))
    tool_map = {t.name: t for t in tools}
    result = tool_map["run_smart_screening"].invoke(
        {"requirement": "主力流入", "market_context": ""}
    )
    assert "000001.SZ" in result
    assert "没有找到候选股票" not in result


def test_screening_carries_net_inflow_20d_60d(monkeypatch):
    """run_smart_screening must populate net_inflow_20d/60d (万元) so strategies
    can screen on 中/长期主力立场. get_stock_moneyflow now returns net_20d/net_60d
    (万元); a strategy requiring 20d>=1亿 / 60d>=2亿 must not drop the candidate."""
    from web.history_chat import build_tool_set
    strategy = (
        '{"name": "中期主力", "conditions": ['
        '{"field": "smart_money_score", "op": ">=", "value": 50},'
        '{"field": "net_inflow_20d", "op": ">=", "value": 10000},'
        '{"field": "net_inflow_60d", "op": ">=", "value": 20000}],'
        '"order_by": "smart_money_score", "limit": 10}'
    )
    _apply_mocks(monkeypatch, mf_extra={"net_20d": 15000, "net_60d": 30000})
    tools = build_tool_set(get_config(), _FakeLLM(strategy))
    tool_map = {t.name: t for t in tools}
    result = tool_map["run_smart_screening"].invoke(
        {"requirement": "中期主力", "market_context": ""}
    )
    assert "000001.SZ" in result
    assert "没有找到候选股票" not in result
