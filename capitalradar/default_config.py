import os

_CAPITALRADAR_HOME = os.path.join(os.path.expanduser("~"), ".capitalradar")

# Single source of truth for env-var → config-key overrides. To expose
# a new config key for environment-based override, add a row here — no
# entry-point script changes required. Coercion is driven by the type
# of the existing default, so users can keep writing plain strings in
# their .env file.
_ENV_OVERRIDES = {
    "CAPITALRADAR_LLM_PROVIDER":         "llm_provider",
    "CAPITALRADAR_DEEP_THINK_PROVIDER":  "deep_think_provider",
    "CAPITALRADAR_QUICK_THINK_PROVIDER": "quick_think_provider",
    "CAPITALRADAR_DEEP_THINK_LLM":       "deep_think_llm",
    "CAPITALRADAR_QUICK_THINK_LLM":      "quick_think_llm",
    "CAPITALRADAR_LLM_BACKEND_URL":      "backend_url",
    "CAPITALRADAR_OUTPUT_LANGUAGE":      "output_language",
    "CAPITALRADAR_MAX_DEBATE_ROUNDS":    "max_debate_rounds",
    "CAPITALRADAR_MAX_RISK_ROUNDS":      "max_risk_discuss_rounds",
    "CAPITALRADAR_CHECKPOINT_ENABLED":   "checkpoint_enabled",
    "CAPITALRADAR_BENCHMARK_TICKER":     "benchmark_ticker",
    "CAPITALRADAR_PROXY":                "proxy",
    "TUSHARE_TOKEN":                      "tushare_token",
    "CAPITALRADAR_WECOM_WEBHOOK_URL":     "wecom_webhook_url",
    "CAPITALRADAR_WECOM_BOT_ID":          "wecom_bot_id",
    "CAPITALRADAR_WECOM_BOT_SECRET":      "wecom_bot_secret",
    "CAPITALRADAR_WECOM_BOT_ADVISOR_ENABLED": "wecom_bot_advisor_enabled",
    "CAPITALRADAR_WEB_ADVISOR_WECHAT_PUSH_ENABLED": "web_advisor_wechat_push_enabled",
    "CAPITALRADAR_ADVISORY_DATA_VERIFY": "advisory_data_verify",
    "CAPITALRADAR_SMS_CROSSCHECK_MAX_RATIO": "sms_crosscheck_max_ratio",
    "CAPITALRADAR_SMS_MIN_5D_NET_ABS_WAN":   "sms_min_5d_net_abs_wan",
    "CAPITALRADAR_SMS_DUAL_SOURCE_GATE":     "sms_dual_source_gate",
}


def _coerce(value: str, reference):
    """Coerce env-var string to the type of the existing default value."""
    if isinstance(reference, bool):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: dict) -> dict:
    """Apply CAPITALRADAR_* env vars to the config dict in-place."""
    for env_var, key in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue
        config[key] = _coerce(raw, config.get(key))
    return config


DEFAULT_CONFIG = _apply_env_overrides({
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("CAPITALRADAR_RESULTS_DIR", os.path.join(_CAPITALRADAR_HOME, "logs")),
    "data_cache_dir": os.getenv("CAPITALRADAR_CACHE_DIR", os.path.join(_CAPITALRADAR_HOME, "cache")),
    # Global WeCom (企业微信) group-bot webhook URL. Scheduled tasks fall back to
    # this when their own per-task webhook_url is empty — set it once to push
    # every task; a per-task value always wins.
    "wecom_webhook_url": os.getenv("CAPITALRADAR_WECOM_WEBHOOK_URL", ""),
    # WeCom 智能机器人 (botid + secret, WebSocket long-connection channel).
    # Global fallback for scheduled-task pushes; a per-task bot_id/bot_secret
    # wins when set. Requires the user to have messaged the bot once so the
    # server can learn the target chatid.
    "wecom_bot_id": os.getenv("CAPITALRADAR_WECOM_BOT_ID", ""),
    "wecom_bot_secret": os.getenv("CAPITALRADAR_WECOM_BOT_SECRET", ""),
    # Master switch for the WeCom-bot → advisor-agent bridge (remote control of
    # scheduled tasks from the user's personal chat). Off disables the handler
    # entirely; scheduled-run pushes are unaffected.
    "wecom_bot_advisor_enabled": True,
    # Optional allowlist of WeCom single-chat userids allowed to drive the
    # advisor (WeCom userid). Empty = every single-chat user who messages the bot
    # may drive it (multi-user default); when populated, only listed userids run
    # an advisor turn — everyone else is ignored (logged, never answered).
    "wecom_bot_advisor_users": [],
    # Optional mirror of web-advisor replies to the WeCom bot's personal chat.
    # When on, each completed web advisory turn pushes its final reply to the
    # learned single-chat target. Default OFF — toggled live from the advisory
    # UI (persisted separately); this is only the startup default.
    "web_advisor_wechat_push_enabled": False,
    # Advisor data verification: append a live-quote 数据核验 footer to every
    # completed advisory answer (re-fetches today's 涨跌幅 for each A-share
    # ticker the answer mentions) so stale/mislabeled figures are visible.
    "advisory_data_verify": True,
    # SMS (Smart Money Score) 资金流数据完整性：
    # sms_crosscheck_max_ratio — verify_moneyflow 交叉验证偏差倍数阈值（>阈值 → 偏差）。
    # sms_min_5d_net_abs_wan  — 5日净流绝对值下限（万元），低于 → 标注「数据缺失，评分不可信」。
    # sms_dual_source_gate    — 评分前先对主源(东财系)净额与同花顺(moneyflow_dc)做交叉验证；
    #                           偏差 → 停发方向性结论，summary 改为双源冲突警告。
    "sms_crosscheck_max_ratio": 10.0,
    "sms_min_5d_net_abs_wan": 1000.0,
    "sms_dual_source_gate": True,
    "memory_log_path": os.getenv("CAPITALRADAR_MEMORY_LOG_PATH", os.path.join(_CAPITALRADAR_HOME, "memory", "trading_memory.md")),
    # HTTP proxy for data sources blocked in certain regions (Reddit, StockTwits, CLS, etc.)
    "proxy": "http://127.0.0.1:7897",
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": "deepseek",
    "deep_think_llm": "deepseek-v4-pro",
    "quick_think_llm": "deepseek-v4-flash",
    # Optional per-role provider overrides. When None (default), each role
    # uses the global ``llm_provider`` (e.g. deep → deepseek, quick → ollama).
    "deep_think_provider": None,
    "quick_think_provider": None,
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # The CLI overrides this per provider when the user picks one. Keeping a
    # provider-specific URL here would leak (e.g. OpenAI's /v1 was previously
    # being forwarded to Gemini, producing malformed request URLs).
    "backend_url": None,
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    "anthropic_effort": None,           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": False,
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": "Chinese",
    # Trend retracement detection settings
    "retracement": {
        "lookback_days": 30,
        "min_decline_pct": 5.0,
        "max_retrace_pct": 15.0,
        "min_duration_days": 3,
        "max_duration_days": 15,
        "volume_shrink_ratio": 0.7,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "rsi_neutral_low": 40,
        "rsi_neutral_high": 50,
        "support_ma_periods": [20, 50],
    },
    # Autocli web scraping settings for A-share data sources
    "autocli": {
        "enabled": True,
        "chrome_debug_port": 9222,
        "request_delay_ms": 1500,
        "max_retries": 2,
        "timeout_seconds": 20,
        "xueqiu_max_posts": 10,
        "guba_max_posts": 10,
        "dragon_tiger_recent_days": 10,
    },
    # Debate and discussion settings
    "ollama_servers": ["http://172.20.86.254:11434/v1"],
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 350,
    "max_analyst_tool_calls": 10,   # per-analyst cap — reduced from 12 for speed — forces report if exceeded
    "analyst_concurrency_limit": 2,  # Ollama local GPU can handle 2 concurrent
    # Capital flow data lookback (trading days). The Capital Flow Analyst
    # should request at least this many days of money flow and margin trading
    # data to detect multi-week accumulation/distribution patterns.
    "capital_flow_lookback_days": 60,
    # News / data fetching parameters
    # Increase for longer lookback strategies or to broaden macro coverage;
    # decrease to reduce token usage in agent prompts.
    "news_article_limit": 20,             # max articles per ticker (ticker-news)
    "global_news_article_limit": 10,      # max articles for global/macro news
    "global_news_lookback_days": 7,       # macro news lookback window
    # Search queries used by get_global_news for macro headlines. Extend or
    # replace to broaden geographic / sector coverage.
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "ECB Bank of England BOJ central bank policy",
        "oil commodities supply chain energy",
    ],
    # Chinese / Asian market news queries — used by eastmoney global news vendor
    # and can be merged into global_news_queries for yfinance-based searches too.
    "cn_news_queries": [
        # Macro policy & central bank
        "央行 LPR 货币政策 降准降息 逆回购 MLF",
        "国务院 政治局 经济工作会议 稳增长 刺激政策",
        # A-share market & economy
        "A股 沪深300 上证指数 创业板 科创板 成交额 北向资金",
        "GDP PMI CPI PPI 经济数据 社融 信贷",
        # Geopolitics & trade
        "中美贸易 关税 制裁 科技封锁 出口管制 地缘政治",
        # Commodities & supply chain
        "油价 大宗商品 黄金 铜 锂 稀土 供应链 能源 光伏",
        # Currency & cross-border flows
        "人民币 汇率 美元 美联储 央行 跨境资本流动 外汇储备",
        # Industrial policy — key sectors
        "产业政策 新能源 半导体 芯片 人工智能 AI 大模型 机器人",
        "新能源汽车 锂电池 光伏 储能 风电 碳中和",
        "医药 生物医药 创新药 医疗器械 集采",
        "房地产 楼市 房贷 保障房 城中村改造 基建",
        "数字经济 数据要素 信创 云计算 大数据 算力",
        "消费 内需 家电 汽车 白酒 电商 餐饮旅游",
        # Capital market specific
        "IPO 注册制 退市 减持 回购 分红 市值管理",
        "量化交易 程序化交易 融券 转融通 做空 监管",
        "基金 公募 私募 社保 养老金 保险资金 入市",
    ],
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": "tushare,akshare,yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "tushare,yfinance",   # tushare first for A-shares, yfinance for others
        "news_data": "eastmoney,cls,yfinance",    # 东方财富 + 财联社 (国内直连), yfinance (美股)
        "capital_flow_data": "akshare,tushare,yfinance",  # akshare=东财逐日 primary for A-share fund flow, tushare 兜底
        "social_sentiment_data": "xueqiu,eastmoney_guba",  # 雪球 + 股吧 (国内散户情绪)
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Real-time data sources first, then fall back to tushare/akshare
        "get_realtime_quote": "tencent,tushare,akshare,yfinance",
        "get_top_gainers": "eastmoney,tushare",
        "get_top_net_inflow": "eastmoney,tushare",
        "get_intraday_data": "tencent,akshare,yfinance",
    },
    # Benchmark for alpha calculation in the reflection layer.
    # ``benchmark_ticker`` (when set) overrides the suffix map for all
    # tickers; leave it None to use ``benchmark_map`` for auto-detection
    # based on the ticker's exchange suffix. SPY remains the US default
    # so the reflection label keeps reading "Alpha vs SPY" for US tickers
    # while non-US tickers get their regional index automatically.
    "benchmark_ticker": None,
    "benchmark_map": {
        ".NS":  "^NSEI",    # NSE India (Nifty 50)
        ".BO":  "^BSESN",   # BSE India (Sensex)
        ".T":   "^N225",    # Tokyo (Nikkei 225)
        ".HK":  "^HSI",     # Hong Kong (Hang Seng)
        ".L":   "^FTSE",    # London (FTSE 100)
        ".TO":  "^GSPTSE",  # Toronto (TSX Composite)
        ".AX":  "^AXJO",    # Australia (ASX 200)
        "":     "SPY",      # default for US-listed tickers (no suffix)
    },

    "adjudication_rules": {
        "enabled": True,
        "tech_flow_discount": {"enabled": True, "pe_threshold": 50, "sectors": ["半导体","芯片","AI","软件","新材料","电子"], "weight_reduction": 0.5},
        "sector_rotation_check": {"enabled": True},
        "consensus_reversal": {"enabled": True, "bearish_threshold": 4},
        "conflict_resolution": {"enabled": True, "lookback_days": 7},
    },
})
