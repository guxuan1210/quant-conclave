"""Multi-horizon 主力资金 (当日/5/20/60日 + 构成 + 相对强度) feature tests.

Covers the three data-layer additions that feed the watchlist 【资金多周期】
prompt block, the raw-flow panel and the advisor get_moneyflow_multi_horizon
tool:

1. ``aggregate_moneyflow_wan`` — pure, unit-agnostic per-horizon aggregation
   (main=超大+大单, elg, lg, all 四档, pos_days) + the scale-relative anchors:
   ``avg_turnover`` (窗口内日均成交额) and ``ratio`` (主力净额占日均成交额%).
2. ``get_stock_moneyflow`` — must NOT raise the old ``total_vol`` NameError, and
   must return net_60d + multi_horizon even when called with days=5 (the window
   is now ≥60 trading days regardless of the ``days`` param).
3. ``get_moneyflow_multi_horizon`` — advisor-tool backend shape + best-effort
   circ_mv_wan (流通市值).
4. ``watchlist_stock_detail`` — the 90-day cache (元) is read, converted 元→万元
   ONCE, aggregated, and both the no_llm payload and the LLM prompt carry the
   block with 万元 numbers and 占成交额% strength. Regression for the 1万倍 unit
   pitfall and the "absolute 万元 = 强度" fallacy (强度 must be relative to
   liquidity/scale — the user's design correction).
5. Advisor ``get_moneyflow_multi_horizon`` tool returns a compact 万元 summary.

No network: tushare pro, moneyflow cache, Tencent realtime, OHLCV, margin and
MX are all stubbed.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import requests
import tushare as _ts


# ── synthetic data builders ──────────────────────────────────────────────

def _const_row(date: str, buy_elg=1000.0, buy_lg=1000.0):
    """Daily dict with md/sm buy=sell (nets 0) → all == main.

    Per-day turnover = 6000 (1000+0+1000+0 + 1000+1000+1000+1000) → ratio 33.33%.
    """
    return {
        "trade_date": date,
        "buy_elg_amount": buy_elg, "sell_elg_amount": 0.0,
        "buy_lg_amount": buy_lg, "sell_lg_amount": 0.0,
        "buy_md_amount": 1000.0, "sell_md_amount": 1000.0,
        "buy_sm_amount": 1000.0, "sell_sm_amount": 1000.0,
    }


def _mf_df(rows=65, buy_elg=1000.0, buy_lg=2000.0):
    """tushare moneyflow-style newest-first DataFrame; per-day 主力 = buy_elg+buy_lg."""
    import pandas as pd
    return pd.DataFrame({
        "trade_date": [f"202608{rows - i:02d}" for i in range(rows)],  # desc
        "buy_elg_amount": [buy_elg] * rows, "sell_elg_amount": [0.0] * rows,
        "buy_lg_amount": [buy_lg] * rows, "sell_lg_amount": [0.0] * rows,
        "buy_md_amount": [1000.0] * rows, "sell_md_amount": [1000.0] * rows,
        "buy_sm_amount": [1000.0] * rows, "sell_sm_amount": [1000.0] * rows,
    })


class _FakePro:
    """minimal tushare pro_api stand-in: moneyflow() returns a fixed df."""

    def __init__(self, df):
        self._df = df

    def moneyflow(self, **kwargs):
        return self._df


class _NoPro:
    """daily_basic raises — tests must never hit tushare network for 流通市值."""

    def daily_basic(self, **kwargs):
        raise RuntimeError("no tushare network in tests")


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    """build_tool_set's LLM: needs .invoke and .bind_tools only."""

    def __init__(self, content: str = ""):
        self._content = content

    def invoke(self, messages):
        return _Resp(self._content)

    def bind_tools(self, tools):
        return self


class _FakeResp:
    encoding = "gbk"
    text = 'v_sh="1~平安银行~000001~11.20~11.25~11.10~123456~...";\n'

    def json(self):
        return {"data": {}}


def _flow_csv():
    return (
        "ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,"
        "buy_lg_amount,sell_lg_amount,buy_md_amount,sell_md_amount,"
        "buy_sm_amount,sell_sm_amount\n"
        "000001.SZ,20260819,20000000,10000000,20000000,30000000,0,0,0,0,0\n"
        "000001.SZ,20260820,50000000,20000000,0,30000000,0,0,0,0,0\n"
    )


def _mh_cache_rows():
    """get_cached_moneyflow returns 元-scale rows ASC (oldest→newest), like the
    cache table. 万元: 08-18 主力-500, 08-19 主力+2000, 08-20 主力+5000."""
    return [
        {"ts_code": "000001.SZ", "trade_date": "20260818",
         "buy_elg_amount": 10000000, "sell_elg_amount": 0,
         "buy_lg_amount": 5000000, "sell_lg_amount": 20000000,
         "buy_md_amount": 0, "sell_md_amount": 0,
         "buy_sm_amount": 0, "sell_sm_amount": 0},
        {"ts_code": "000001.SZ", "trade_date": "20260819",
         "buy_elg_amount": 10000000, "sell_elg_amount": 20000000,
         "buy_lg_amount": 30000000, "sell_lg_amount": 0,
         "buy_md_amount": 0, "sell_md_amount": 0,
         "buy_sm_amount": 0, "sell_sm_amount": 0},
        {"ts_code": "000001.SZ", "trade_date": "20260820",
         "buy_elg_amount": 20000000, "sell_elg_amount": 0,
         "buy_lg_amount": 30000000, "sell_lg_amount": 0,
         "buy_md_amount": 0, "sell_md_amount": 0,
         "buy_sm_amount": 0, "sell_sm_amount": 0},
    ]


def _patch_watchlist_network(monkeypatch):
    """Shared stubs for the two watchlist_stock_detail tests: realtime quote,
    moneyflow cache, tushare circ_mv (via _get_pro) and margin network."""
    from capitalradar.dataflows import eastmoney_sector as es
    from web import moneyflow_cache as mfc
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp())
    monkeypatch.setattr(mfc, "fetch_or_cache_moneyflow", lambda *a, **k: _flow_csv())
    monkeypatch.setattr(mfc, "get_cached_moneyflow", lambda *a, **k: _mh_cache_rows())
    monkeypatch.setattr(es, "_get_pro", lambda: _NoPro())   # circ_mv never hits network
    monkeypatch.setattr(_ts, "pro_api", lambda *a, **k: _no_margin(*a, **k))


def _no_margin(*a, **k):
    raise RuntimeError("no margin network in tests")


# ── aggregate_moneyflow_wan ──────────────────────────────────────────────

def test_aggregate_constant_61_days():
    from capitalradar.dataflows.eastmoney_sector import aggregate_moneyflow_wan
    rows = [_const_row(f"202609{61 - i:02d}") for i in range(61)]
    agg = aggregate_moneyflow_wan(rows)
    assert agg["1"] == {"main": 2000.0, "elg": 1000.0, "lg": 1000.0,
                        "all": 2000.0, "avg_turnover": 6000.0, "ratio": 33.33,
                        "pos_days": 1, "days": 1}
    assert agg["5"] == {"main": 10000.0, "elg": 5000.0, "lg": 5000.0,
                        "all": 10000.0, "avg_turnover": 6000.0, "ratio": 33.33,
                        "pos_days": 5, "days": 5}
    assert agg["20"]["main"] == 40000.0 and agg["20"]["pos_days"] == 20
    assert agg["60"]["main"] == 120000.0 and agg["60"]["pos_days"] == 60
    # relative strength: cumulative 主力净额/窗口内累计成交额 — constant flow keeps a
    # constant ratio across horizons (33.33% for every window), unlike a bare 万 figure.
    assert agg["60"]["avg_turnover"] == 6000.0
    assert agg["60"]["ratio"] == pytest.approx(33.33, abs=0.01)


def test_aggregate_sorts_newest_first():
    """Input order must not matter — the newest trade_date wins the '1' window."""
    from capitalradar.dataflows.eastmoney_sector import aggregate_moneyflow_wan
    a = _const_row("20260801", buy_elg=1000.0, buy_lg=0.0)   # 主力 +1000 (elg)
    b = dict(_const_row("20260802", buy_elg=0.0, buy_lg=0.0),
             sell_elg_amount=2000.0)                          # 主力 -2000
    c = dict(_const_row("20260803", buy_elg=0.0, buy_lg=5000.0))  # 主力 +5000 (lg)
    agg = aggregate_moneyflow_wan([a, b, c])   # ASC input
    assert agg["1"]["main"] == 5000.0 and agg["1"]["days"] == 1      # newest = c
    assert agg["1"]["avg_turnover"] == 9000.0                         # c's daily turnover
    assert agg["1"]["ratio"] == pytest.approx(55.56, abs=0.01)        # 5000/9000
    assert agg["5"]["main"] == 4000.0                                 # 1000-2000+5000
    assert agg["5"]["elg"] == -1000.0 and agg["5"]["lg"] == 5000.0
    assert agg["5"]["avg_turnover"] == pytest.approx(6666.7, abs=0.1)
    assert agg["5"]["ratio"] == pytest.approx(20.0, abs=0.1)          # 4000/(5000+6000+9000)
    assert agg["5"]["pos_days"] == 2 and agg["5"]["days"] == 3        # a & c positive


def test_aggregate_partial_window_reports_actual_days():
    """Fewer than 60 rows → '60' aggregates what exists and says so (days=N)."""
    from capitalradar.dataflows.eastmoney_sector import aggregate_moneyflow_wan
    rows = [_const_row(f"202608{n:02d}") for n in range(1, 11)]  # 10 rows, ASC
    agg = aggregate_moneyflow_wan(rows)
    assert agg["60"]["days"] == 10
    assert agg["60"]["main"] == 20000.0   # 10 × 2000
    assert agg["60"]["avg_turnover"] == 6000.0
    assert agg["60"]["ratio"] == pytest.approx(33.33, abs=0.01)


# ── get_stock_moneyflow: NameError fix + net_60d + multi_horizon ──────────

def test_get_stock_moneyflow_no_nameerror_and_net_60d(monkeypatch):
    """The old code raised NameError (total_vol undefined) whenever data existed,
    so every caller silently degraded. Now it returns a full dict including
    net_60d and multi_horizon even when called with days=5."""
    from capitalradar.dataflows import eastmoney_sector as es
    monkeypatch.setattr(es, "_get_pro", lambda: _FakePro(_mf_df(65)))
    mf = es.get_stock_moneyflow("000001.SZ", days=5)
    assert mf["net_1d"] == 3000.0
    assert mf["net_5d"] == 15000.0
    assert mf["net_20d"] == 60000.0
    assert mf["net_60d"] == 180000.0                 # 60 rows × 3000
    assert mf["net_amount"] == 15000.0               # head(5)
    assert mf["main_force_net"] == 15000.0
    assert mf["multi_horizon"]["60"]["days"] == 60   # window covers ≥60 trading days
    assert mf["multi_horizon"]["5"]["main"] == mf["net_5d"]
    assert "net_inflow_ratio" in mf                  # no NameError on this key


def test_get_stock_moneyflow_empty_still_returns_default(monkeypatch):
    from capitalradar.dataflows import eastmoney_sector as es
    import pandas as pd
    monkeypatch.setattr(es, "_get_pro", lambda: _FakePro(pd.DataFrame()))
    assert es.get_stock_moneyflow("000001.SZ", days=5) == {"net_amount": 0.0}


# ── get_moneyflow_multi_horizon ──────────────────────────────────────────

def test_get_moneyflow_multi_horizon_shape(monkeypatch):
    from capitalradar.dataflows import eastmoney_sector as es
    monkeypatch.setattr(es, "_get_pro", lambda: _FakePro(_mf_df(65)))
    agg = es.get_moneyflow_multi_horizon("000001.SZ")
    assert agg["source_rows"] == 65
    assert agg["end_date"] == datetime.now().strftime("%Y%m%d")
    assert agg["60"]["days"] == 60
    assert agg["5"]["main"] == 15000.0
    assert set(("1", "5", "20", "60")) <= set(agg)
    assert "circ_mv_wan" in agg     # best-effort 流通市值 (scale anchor), may be 0
    assert agg["circ_mv_wan"] == 0.0


def test_get_moneyflow_multi_horizon_circ_mv_best_effort(monkeypatch):
    """daily_basic is optional — a pro without it degrades to circ_mv_wan=0."""
    from capitalradar.dataflows import eastmoney_sector as es
    monkeypatch.setattr(es, "_get_pro", lambda: _FakePro(_mf_df(65)))
    agg = es.get_moneyflow_multi_horizon("000001.SZ")
    assert agg["circ_mv_wan"] == 0.0


def test_get_moneyflow_multi_horizon_empty(monkeypatch):
    from capitalradar.dataflows import eastmoney_sector as es
    import pandas as pd
    monkeypatch.setattr(es, "_get_pro", lambda: _FakePro(pd.DataFrame()))
    agg = es.get_moneyflow_multi_horizon("000001.SZ")
    assert agg["source_rows"] == 0
    assert agg["60"]["days"] == 0 and agg["60"]["main"] == 0.0


# ── watchlist_stock_detail: cache 元 → 万元 → aggregate ──────────────────

def test_watchlist_detail_flow_multi_horizon_from_yuan_cache(monkeypatch):
    """The 90-day cache stores 元; the detail endpoint must /1e4 ONCE and
    aggregate per horizon. Regression for the 1万倍 display pitfall."""
    from web.app import watchlist_stock_detail
    _patch_watchlist_network(monkeypatch)
    result = watchlist_stock_detail("000001.SZ", no_llm=True)
    mh = result["flow_multi_horizon"]
    assert mh["1"]["main"] == 5000.0    # 08-20: (2000万-0)+(3000万-0)
    assert mh["1"]["elg"] == 2000.0
    assert mh["1"]["lg"] == 3000.0
    assert mh["1"]["ratio"] == pytest.approx(100.0, abs=0.01)   # 5000/5000当日成交额
    assert mh["5"]["main"] == 6500.0    # 5000 + 2000 + (-500)
    assert mh["5"]["pos_days"] == 2     # 08-20, 08-19 positive
    assert mh["5"]["days"] == 3
    assert mh["5"]["avg_turnover"] == pytest.approx(4833.3, abs=0.1)
    assert mh["5"]["ratio"] == pytest.approx(44.83, abs=0.01)  # 6500/(3500+6000+5000)
    assert mh["60"]["days"] == 3        # only 3 rows cached → honest count


def test_watchlist_detail_prompt_includes_multi_horizon_wan(monkeypatch):
    """The LLM prompt must carry the 【资金多周期】 block with 万元 numbers, the
    pre-computed 主力方向 sign pattern AND the relative-strength framing
    (占成交额%, 流通市值, ⚠️ scale caveat) — the model cites verbatim and must
    NOT treat a bare 万元 net as a strength verdict."""
    from web.app import watchlist_stock_detail
    from capitalradar.dataflows import interface
    _patch_watchlist_network(monkeypatch)

    def _no_ohlcv(*a, **k):
        raise RuntimeError("no OHLCV network in tests")
    monkeypatch.setattr(interface, "route_to_vendor", _no_ohlcv)

    def _no_mx(*a, **k):
        raise RuntimeError("no MX network in tests")
    monkeypatch.setattr("capitalradar.dataflows.mx_client.query_text", _no_mx)

    class _Structured:
        def invoke(self, prompt):
            raise RuntimeError("force free-text fallback")

    class _FakeLLM:
        def __init__(self):
            self.prompts = []
        def with_structured_output(self, schema):
            return _Structured()
        def invoke(self, prompt):
            self.prompts.append(prompt)
            return _Resp("结论：看多\n命中路径：路径A 低位吸筹启动\n……")

    class _Client:
        def __init__(self, llm):
            self.llm = llm
        def get_llm(self):
            return self.llm

    llm = _FakeLLM()
    # web.app imports create_llm_client INTO its module namespace (from
    # capitalradar.llm_clients) — patching web.app.create_llm_client fails with
    # AttributeError. Patch the source module the import reads at call time.
    monkeypatch.setattr(
        "capitalradar.llm_clients.create_llm_client", lambda *a, **k: _Client(llm)
    )

    watchlist_stock_detail("000001.SZ")

    assert llm.prompts, "LLM was never invoked — prompt not captured"
    prompt = llm.prompts[0]
    assert "【资金多周期】" in prompt
    assert "当日  主力+5000万" in prompt
    assert "5日  主力+6500万" in prompt
    assert "占成交额+100.0%" in prompt     # 1日 relative strength (5000/5000)
    assert "占成交额+44.8%" in prompt      # 5日 relative strength (6500/14500)
    # 流通市值 unavailable in tests (NoPro) → the avg-turnover anchor renders instead
    assert "近3日日均成交额0.48亿" in prompt
    # all horizons positive → descriptive sign pattern (no hard 做多 verdict)
    assert "各周期一致净流入" in prompt
    assert "主力持续做多" not in prompt
    # scale/liquidity caveat — the user's design correction is in the prompt
    assert "强度须结合市值" in prompt
    assert "勿仅凭绝对额或单一周期下结论" in prompt
    # 近N日 window must be N consecutive trading days, no cherry-picking dates
    # (302132 regression: skipped two weak days, called 08-26/27/09-01 "近3日")
    assert "近N日连续交易日" in prompt
    assert "最后N个连续交易日" in prompt
    assert "禁止自行挑选日期拼凑窗口" in prompt
    # net_amount 全口径残留清零 (002495 regression): flow_detail net 列现在按主力
    # (elg+lg) 算; 数据展示层不再有 全口径 标签 (规则文本允许提到该词来禁用, 但任何
    # 『全口径净额/最大/净流出』的数值展示都必须删干净).
    assert "全口径净额(含中小单)" not in prompt
    assert "全口径最大净流入" not in prompt
    assert "全口径净流出" not in prompt
    assert "主力净" in prompt            # 【逐日资金流】行 + 表头已改主力净



# ── advisor tool returns a compact 万元 summary ───────────────────────────

def test_advisor_get_moneyflow_multi_horizon_tool(monkeypatch):
    from capitalradar.dataflows.config import get_config
    from web.history_chat import build_tool_set
    from capitalradar.dataflows import eastmoney_sector as es

    def _fake_mh(code):
        rows = [_const_row(f"202609{n:02d}") for n in range(1, 62)]
        agg = es.aggregate_moneyflow_wan(rows)
        agg["end_date"] = "20260901"
        agg["source_rows"] = 61
        return agg

    monkeypatch.setattr(es, "get_moneyflow_multi_horizon", _fake_mh)

    tools = build_tool_set(get_config(), _FakeLLM())
    tool_map = {t.name: t for t in tools}
    assert "get_moneyflow_multi_horizon" in tool_map
    out = tool_map["get_moneyflow_multi_horizon"].invoke(
        {"ticker": "000001.SZ"}
    )
    assert "000001.SZ" in out
    assert "60日" in out and "20日" in out and "5日" in out and "当日" in out
    assert "主力" in out and "万" in out
    assert "各周期一致净流入" in out
    assert "占成交额" in out                       # relative strength present
    assert "日均成交额" in out                     # liquidity anchor present
    assert "全口径" not in out                      # noise dropped from the block
