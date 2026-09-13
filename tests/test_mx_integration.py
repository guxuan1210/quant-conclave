"""Tests for the 妙想(MX) data integration.

Pure unit tests — mx_client.query and friends are monkeypatched, no network.
Covers the three new integration surfaces:
1. mx_client rendering / unit normalization / snapshot extraction
2. block_trade_data MX-first 暗盘/大宗 report (with akshare fallback)
3. smart_money_score + batch_analysis realtime DDX enrichment
"""

from __future__ import annotations

import pytest

from quantconclave.dataflows import mx_client
from quantconclave.dataflows import block_trade_data


# ── Canned MX response shapes (as returned by the live API) ──────────────

def _money_flow_response():
    """Snapshot DTO: one timestamp, indicator-coded keys decoded via nameMap."""
    return {
        "data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [
            {
                "title": "贵州茅台(600519.SH)当前超大单净流入等",
                "table": {
                    "headName": ["2026-08-26 16:13"],
                    "CDDJE_f66_3": ["850.8万元"],
                    "DDJE_f72_3": ["6407万元"],
                    "ZDLR_f76_3": ["7258万元"],
                    "CJDDX_5": ["0.004"],
                },
                "nameMap": {
                    "CDDJE_f66_3": "超大单净流入资金",
                    "DDJE_f72_3": "大单净流入资金",
                    "ZDLR_f76_3": "主力净流入资金",
                    "CJDDX_5": "当日DDX",
                },
            }
        ]}}}
    }


def _block_trade_response():
    """Listing DTO: multi-row, literal Chinese columns."""
    return {
        "data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [
            {
                "title": "大宗交易(2025-08-26~2026-08-26)",
                "table": {
                    "headName": ["2026-08-03", "2026-07-31"],
                    "成交价(元)": ["1358.98", "1228.26"],
                    "折/溢价率": ["0%", "-9.1%"],
                    "成交量(股)": ["1.48万", "3500"],
                    "成交金额(元)": ["2011万", "430万"],
                    "买入营业部": ["中信证券总部(非营业场所)", "长江证券深圳福华路"],
                    "卖出营业部": ["广发证券昆明东风东路", "招商证券深圳福民路"],
                },
                "nameMap": {
                    "headName": "headName", "成交价(元)": "成交价(元)",
                    "折/溢价率": "折/溢价率", "成交量(股)": "成交量(股)",
                    "成交金额(元)": "成交金额(元)",
                    "买入营业部": "买入营业部", "卖出营业部": "卖出营业部",
                },
            }
        ]}}}
    }


# ── mx_client: rendering + parsing ───────────────────────────────────────

def test_render_snapshot_dto_uses_name_map():
    text = mx_client.render_data_tables(_money_flow_response())
    assert "超大单净流入资金: 850.8万元" in text
    assert "主力净流入资金: 7258万元" in text
    assert "当日DDX: 0.004" in text


def test_render_listing_dto_builds_markdown_table():
    text = mx_client.render_data_tables(_block_trade_response())
    assert "| 日期 |" in text
    assert "成交价(元)" in text
    assert "折/溢价率" in text
    assert "| 2026-08-03 | 1358.98 | 0% |" in text
    assert "| 2026-07-31 | 1228.26 | -9.1% |" in text


def test_extract_snapshot_metrics_normalizes_units_to_wan():
    m = mx_client.extract_snapshot_metrics(_money_flow_response())
    assert m["超大单净流入资金"] == 850.8      # 850.8万元 → 850.8
    assert m["主力净流入资金"] == 7258.0
    assert m["当日DDX"] == 0.004


def test_parse_cn_amount_handles_units():
    assert mx_client.parse_cn_amount("7258万元") == 7258.0
    assert mx_client.parse_cn_amount("5.853亿元") == 58530.0
    assert mx_client.parse_cn_amount("-7234万元") == -7234.0
    assert mx_client.parse_cn_amount("0.004") == 0.004
    assert mx_client.parse_cn_amount("0%") == 0.0
    assert mx_client.parse_cn_amount("N/A") is None


def test_query_raises_without_key(monkeypatch):
    monkeypatch.delenv("MX_APIKEY", raising=False)
    with pytest.raises(mx_client.MxError):
        mx_client.query("任何查询")


def test_find_dto_list_handles_nested_path():
    dto = mx_client.find_dto_list(_block_trade_response())
    assert len(dto) == 1
    assert dto[0]["table"]["成交价(元)"] == ["1358.98", "1228.26"]


# ── block_trade_data: MX-first 暗盘/大宗 ─────────────────────────────────

def test_block_trade_detail_mx_first(monkeypatch):
    monkeypatch.setattr(mx_client, "query", lambda *a, **k: _block_trade_response())
    # akshare fallback must NOT be reached
    monkeypatch.setattr(
        block_trade_data, "_fetch_all_block_trades",
        lambda *a, **k: pytest.fail("akshare fallback should not be called"),
    )
    report = block_trade_data.get_block_trade_detail("600519.SH", lookback_days=30)
    assert "大宗交易暗盘" in report
    assert "共 2 笔" in report
    # 折价 -9.1% row present with direction tag
    assert "折价" in report
    assert "2026-07-31" in report
    assert "暗盘信号" in report


def test_block_trade_detail_mx_empty_falls_back_akshare(monkeypatch):
    # MX returns no rows → akshare path used (real pandas df so filtering works)
    import pandas as pd

    monkeypatch.setattr(
        mx_client, "query",
        lambda *a, **k: {"data": {"data": {"searchDataResultDTO": {}}}},
    )
    df = pd.DataFrame([
        {"证券代码": "600519", "交易日期": "20260803", "收盘价": 1358.0,
         "成交价": 1358.98, "折溢率": -5.0, "成交量": 100, "成交额": 1e6,
         "买方营业部": "机构专用", "卖方营业部": "某营业部"},
        {"证券代码": "600519", "交易日期": "20260803", "收盘价": 1358.0,
         "成交价": 1300.0, "折溢率": 0.0, "成交量": 50, "成交额": 5e5,
         "买方营业部": "A", "卖方营业部": "B"},
    ])
    monkeypatch.setattr(
        block_trade_data, "_fetch_all_block_trades",
        lambda *a, **k: df,
    )
    report = block_trade_data.get_block_trade_detail("600519.SH", lookback_days=30)
    assert "大宗交易暗盘" in report
    assert "机构买入" in report


def test_mx_block_report_inst_buy_signal():
    rows = [{
        "日期": "2026-08-03", "成交价(元)": "10.0", "折/溢价率": "-6.0%",
        "成交量(股)": "1000", "成交金额(元)": "1万",
        "买入营业部": "机构专用", "卖出营业部": "广发证券",
    }]
    report = block_trade_data._mx_block_trade_report("000001.SZ", rows, "20260101", "20260201")
    assert "🟢 折价大宗+机构买入" in report
    assert "🟢机构买入" in report


# ── smart_money_score: MX realtime fallback ─────────────────────────────

def test_smart_money_realtime_uses_mx(monkeypatch):
    from quantconclave.sector_scan import smart_money_score
    monkeypatch.setattr(mx_client, "query", lambda *a, **k: _money_flow_response())
    monkeypatch.setattr(
        "quantconclave.dataflows.eastmoney_realtime_flow._get_realtime_fund_flow",
        lambda *a, **k: pytest.fail("push2 fallback should not be called"),
    )
    rows = smart_money_score._fetch_realtime_flow_signal("600519.SH")
    assert rows is not None
    row = rows[0]
    assert row["_source"] == "realtime"
    assert row["_vendor"] == "mx"
    # MX 主力资金 metrics arrive in 万元; the MX branch normalizes ×1e4 to yuan
    # so the whole pipeline holds ONE unit convention (元). 7258万元 → 72580000.0.
    assert row["net_amount"] == 7258.0 * 1e4
    assert row["buy_elg_amount"] == 850.8 * 1e4
    assert row["buy_lg_amount"] == 6407.0 * 1e4


def test_smart_money_realtime_mx_empty_uses_push2(monkeypatch):
    from quantconclave.sector_scan import smart_money_score
    monkeypatch.setattr(mx_client, "query", lambda *a, **k: {"data": {}})
    monkeypatch.setattr(
        "quantconclave.dataflows.eastmoney_realtime_flow._get_realtime_fund_flow",
        lambda *a, **k: {
            "main_net_inflow": 123.0, "super_large_net": 80.0,
            "large_net": 43.0, "price": 10.5,
        },
    )
    rows = smart_money_score._fetch_realtime_flow_signal("600519.SH")
    assert rows is not None
    assert rows[0]["net_amount"] == 123.0
    assert "_vendor" not in rows[0]  # push2 path has no vendor marker


# ── batch_analysis: MX 实时DDX enrichment ───────────────────────────────

def test_batch_analysis_candidate_gets_mx_ddx(monkeypatch):
    from quantconclave.sector_scan import batch_analysis
    monkeypatch.setattr(mx_client, "query", lambda *a, **k: _money_flow_response())
    # stub K-line + moneyflow so only the MX enrichment is under test
    monkeypatch.setattr(batch_analysis, "get_stock_daily", lambda *a, **k: [])
    monkeypatch.setattr(batch_analysis, "get_moneyflow_trend", lambda *a, **k: {
        "net_amount": 1e5, "cum_3d": 0, "cum_5d": 0, "cum_10d": 0,
        "consecutive_inflow": 0, "big_order_divergence": False, "daily_flows": [],
    })
    monkeypatch.setattr(batch_analysis, "get_daily_basic_batch", lambda *a, **k: {})
    info = batch_analysis._gather_stock_data({"ts_code": "600519.SH", "name": "贵州茅台"}, {})
    assert info["mx_ddx"] == 0.004
    assert info["mx_main_net"] == 7258.0
