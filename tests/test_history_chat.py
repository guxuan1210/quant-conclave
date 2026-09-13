"""Tests for history chat and metrics extraction."""
import pytest
from web.stream import StreamEmitter


class TestMetricsExtraction:
    def test_extract_capital_flow_metrics_english(self):
        emitter = StreamEmitter(session_id="test")
        report = """
        **Main Net Inflow**: +3.2 billion
        **Northbound Flow**: +1.5 billion
        **Institutional Holding %**: 42.3%
        **Manipulation Risk Level**: LOW
        """
        metrics = emitter._extract_key_metrics("capital_flow", report)
        assert metrics.get("main_net_inflow") == "+3.2 billion"
        assert metrics.get("hsgt_net_inflow") == "+1.5 billion"
        assert metrics.get("institutional_holding_pct") == "42.3%"
        assert metrics.get("manipulation_risk") == "LOW"

    def test_extract_capital_flow_metrics_chinese(self):
        emitter = StreamEmitter(session_id="test")
        report = """
        **主力净流入**：+3.2亿
        **北向资金**：+1.5亿
        **融资余额**：85.6亿
        """
        metrics = emitter._extract_key_metrics("capital_flow", report)
        assert metrics.get("main_net_inflow") == "+3.2亿"
        assert metrics.get("hsgt_net_inflow") == "+1.5亿"
        assert metrics.get("margin_balance") == "85.6亿"

    def test_extract_market_metrics(self):
        emitter = StreamEmitter(session_id="test")
        report = """
        **Current Price**: $168.50
        **MA5**: $165.30
        **MA20**: $158.90
        **MACD Signal**: Bullish Golden Cross
        **RSI(14)**: 62.5
        """
        metrics = emitter._extract_key_metrics("market", report)
        assert metrics.get("current_price") == "$168.50"
        assert metrics.get("macd_signal") == "Bullish Golden Cross"
        assert metrics.get("rsi_14") == "62.5"

    def test_extract_empty_report_returns_empty_dict(self):
        emitter = StreamEmitter(session_id="test")
        metrics = emitter._extract_key_metrics("capital_flow", "")
        assert metrics == {}

    def test_extract_unknown_analyst_returns_empty_dict(self):
        emitter = StreamEmitter(session_id="test")
        metrics = emitter._extract_key_metrics("unknown_type", "**PE**: 15")
        assert metrics == {}

    def test_na_values_are_filtered(self):
        emitter = StreamEmitter(session_id="test")
        report = """
        **Current Price**: N/A
        **MA5**: n/a
        **MA20**: 158.90
        """
        metrics = emitter._extract_key_metrics("market", report)
        assert "current_price" not in metrics
        assert "ma5" not in metrics
        assert metrics.get("ma20") == "158.90"


def test_stream_history_chat_emits_sse_and_core_yields_tuples(monkeypatch):
    """The advisory turn was split into ``_history_chat_core`` (event tuples)
    + ``stream_history_chat`` (SSE formatting). The SSE event names and payloads
    must be preserved exactly — this pins the contract without a real LLM."""
    from web import history_chat as hc

    class _Resp:
        content = "这是最终回答"
        tool_calls = []

    class _FakeLLM:
        def invoke(self, messages):
            return _Resp()

    monkeypatch.setattr(hc, "create_history_pm_agent",
                        lambda run_ids, config, lang: (_FakeLLM(), [], "sys", {}))
    monkeypatch.setattr(hc, "save_chat_message",
                        lambda c, t, r, m, tc="": 1)
    monkeypatch.setattr(hc, "get_chat_messages", lambda c, t: [])
    monkeypatch.setattr(hc, "get_thread_run_ids", lambda c, t: [])

    config = {"output_language": "Chinese", "results_dir": ""}

    # stream_history_chat still yields proper SSE event strings
    events = list(hc.stream_history_chat("t1", "你好", config, "Chinese"))
    assert any(e.startswith("event: chat-stream-start\n") for e in events)
    done = next(e for e in events if e.startswith("event: chat-done\n"))
    assert "这是最终回答" in done
    assert 'data: ' in done

    # _history_chat_core yields (name, data) tuples with the same content
    core = list(hc._history_chat_core("t1", "你好", config, "Chinese"))
    names = [n for n, _ in core]
    assert "chat-stream-start" in names and "chat-done" in names
    done_data = next(d for n, d in core if n == "chat-done")
    assert done_data["full_response"] == "这是最终回答"
    assert done_data["tool_calls_count"] == 0


def test_stream_history_chat_pushes_reply_after_chat_done(monkeypatch):
    """The web-facing SSE wrapper must forward the completed reply to the
    WeChat bridge after ``chat-done``. The push runs on a daemon thread inside
    ``_push_web_advisor_reply`` — here we only pin the wiring (correct text,
    exactly once), the push itself is covered in test_bot_advisor_bridge.py."""
    from web import history_chat as hc

    class _Resp:
        content = "分析完成：看多 3 只。"
        tool_calls = []

    class _FakeLLM:
        def invoke(self, messages):
            return _Resp()

    monkeypatch.setattr(hc, "create_history_pm_agent",
                        lambda run_ids, config, lang: (_FakeLLM(), [], "sys", {}))
    monkeypatch.setattr(hc, "save_chat_message",
                        lambda c, t, r, m, tc="": 1)
    monkeypatch.setattr(hc, "get_chat_messages", lambda c, t: [])
    monkeypatch.setattr(hc, "get_thread_run_ids", lambda c, t: [])

    pushed = []
    monkeypatch.setattr(hc, "_push_web_advisor_reply",
                        lambda text: pushed.append(text))

    config = {"output_language": "Chinese", "results_dir": ""}
    events = list(hc.stream_history_chat("t1", "你好", config, "Chinese"))

    # SSE stream is unchanged (still ends with a chat-done event)…
    assert any(e.startswith("event: chat-done\n") for e in events)
    # …and the completed reply was forwarded to the WeChat bridge exactly once.
    assert pushed == ["分析完成：看多 3 只。"]


def test_core_does_not_push_wechat(monkeypatch):
    """The shared core stays pure — only the SSE wrapper triggers the push, so
    the bot bridge (which calls the core directly) never double-sends."""
    from web import history_chat as hc

    class _Resp:
        content = "回复"
        tool_calls = []

    class _FakeLLM:
        def invoke(self, messages):
            return _Resp()

    monkeypatch.setattr(hc, "create_history_pm_agent",
                        lambda run_ids, config, lang: (_FakeLLM(), [], "sys", {}))
    monkeypatch.setattr(hc, "save_chat_message",
                        lambda c, t, r, m, tc="": 1)
    monkeypatch.setattr(hc, "get_chat_messages", lambda c, t: [])
    monkeypatch.setattr(hc, "get_thread_run_ids", lambda c, t: [])
    pushed = []
    monkeypatch.setattr(hc, "_push_web_advisor_reply",
                        lambda text: pushed.append(text))

    config = {"output_language": "Chinese", "results_dir": ""}
    list(hc._history_chat_core("t1", "你好", config, "Chinese"))
    assert pushed == []


# ---- 数据核验 footer ----------------------------------------------------------


def test_parse_quote_change_pct_tencent_and_akshare():
    from web import history_chat as hc
    # Tencent: `**Change**: 涨跌额 / 涨跌幅` — the %-terminated number is 涨跌幅.
    assert hc._parse_quote_change_pct("**Change**: -0.06 / 2.28%") == 2.28
    assert hc._parse_quote_change_pct("Change: +0.5%") == 0.5
    assert hc._parse_quote_change_pct("no change here") is None


def test_parse_quote_price_tencent_and_plain():
    from web import history_chat as hc
    assert hc._parse_quote_price("**Current Price**: 24.35") == 24.35
    assert hc._parse_quote_price("Current Price: 168.50") == 168.50
    assert hc._parse_quote_price("nothing here") is None


def test_verify_advisor_facts_appends_live_quotes(monkeypatch):
    from web import history_chat as hc
    monkeypatch.setattr(
        "quantconclave.dataflows.interface.route_to_vendor",
        lambda method, symbol="": "**Current Price**: 24.35\n**Change**: +0.24 / 2.28%\n",
    )
    out = hc._verify_advisor_facts(
        "600030 中信证券 主力确认级（评分≥70），核心理由……", {})
    assert "📌 数据核验" in out
    assert "600030" in out
    assert "+2.28%" in out        # today's real move, not a multi-day window
    assert "24.35" in out         # current price


def test_verify_advisor_facts_no_tickers_returns_empty(monkeypatch):
    from web import history_chat as hc
    assert hc._verify_advisor_facts("纯文字回复，没有股票代码", {}) == ""


def test_verify_advisor_facts_gate_off_returns_empty(monkeypatch):
    from web import history_chat as hc
    assert hc._verify_advisor_facts(
        "600030 看多", {"advisory_data_verify": False}) == ""


def test_verify_advisor_facts_fetch_failure_marks_unavailable(monkeypatch):
    from web import history_chat as hc

    def boom(method, symbol=""):
        raise ConnectionError("network down")

    monkeypatch.setattr("quantconclave.dataflows.interface.route_to_vendor", boom)
    out = hc._verify_advisor_facts("600030 看多", {})
    assert "实时行情暂不可用" in out
    assert "600030" in out


def test_verify_advisor_facts_vendor_fallback_marks_unavailable(monkeypatch):
    from web import history_chat as hc
    monkeypatch.setattr(
        "quantconclave.dataflows.interface.route_to_vendor",
        lambda method, symbol="": "# SKIP_VENDOR: no data",
    )
    out = hc._verify_advisor_facts("600030 看多", {})
    assert "实时行情暂不可用" in out


def test_core_appends_verify_footer_before_persist(monkeypatch):
    """The verification footer is appended BEFORE save_chat_message, so DB
    history and every downstream consumer (SSE / WeCom push / stage3 report)
    receive the SAME verified copy."""
    from web import history_chat as hc

    class _Resp:
        content = "600030 主力确认级（评分≥70）— 优先关注"
        tool_calls = []

    class _FakeLLM:
        def invoke(self, messages):
            return _Resp()

    saved = {}
    monkeypatch.setattr(hc, "create_history_pm_agent",
                        lambda run_ids, config, lang: (_FakeLLM(), [], "sys", {}))
    monkeypatch.setattr(hc, "save_chat_message",
                        lambda c, t, r, m, tc="": saved.update(
                            {"role": r, "content": m, "tc": tc}) or 1)
    monkeypatch.setattr(hc, "get_chat_messages", lambda c, t: [])
    monkeypatch.setattr(hc, "get_thread_run_ids", lambda c, t: [])
    monkeypatch.setattr(hc, "_verify_advisor_facts",
                        lambda text, cfg: "\n\n---\n**📌 数据核验** footer")

    config = {"output_language": "Chinese", "results_dir": ""}
    core = list(hc._history_chat_core("t1", "看看600030", config, "Chinese"))
    done = next(d for n, d in core if n == "chat-done")
    expected = "600030 主力确认级（评分≥70）— 优先关注" + "\n\n---\n**📌 数据核验** footer"
    assert done["full_response"] == expected
    assert saved["content"] == expected          # persisted copy carries the footer


# ---- SMS 资金流数据完整性（三层修复） --------------------------------------------


def test_advisor_sms_tool_renders_data_unavailable(monkeypatch):
    """When the SMS engine flags _data_unavailable, the advisor's SMS tool must
    say 数据不可用/verify_moneyflow — never the bearish 主力参与度不足."""
    import quantconclave.sector_scan.smart_money_score as sms
    from web.ai_pick_agent import build_aipick_tools

    def fake_compute(ticker, config):
        return 0, {
            "_total": 0, "_verdict": "no_signal",
            "_data_unavailable": True, "_realtime_only": False,
            "_note": "资金流数据缺失（5日/10日/20日原始数据全为0或缺失），评分不可信，不出分",
            "_meta": {}, "_validity": {"ok": True, "flags": []},
        }

    monkeypatch.setattr(sms, "compute_smart_money_score", fake_compute)
    tools = build_aipick_tools({"output_language": "Chinese"}, None)
    tool = {t.name: t for t in tools}["get_smart_money_score"]
    out = tool.invoke({"ticker": "600030.SH"})
    assert "数据不可用" in out
    assert "verify_moneyflow" in out
    assert "主力参与度不足" not in out


def test_advisor_toolset_contains_verify_moneyflow(monkeypatch):
    from web.history_chat import build_tool_set
    tools = build_tool_set({"output_language": "Chinese"}, None)
    names = {t.name for t in tools}
    assert "verify_moneyflow" in names


def test_advisor_prompt_has_capital_discipline():
    from web.history_chat import build_pm_system_prompt
    prompt = build_pm_system_prompt([], {"output_language": "Chinese"}, "Chinese")
    assert "不构成买入依据" in prompt
    assert "verify_moneyflow" in prompt
