"""Centralized ticker normalization and company-name resolution.

All paths that need to map a ticker symbol to a company name (Analyze
pipeline, Advisory chat, scheduler, results store) should go through
this module so that normalization and caching are consistent.
"""

import re
import threading
from typing import Tuple

# A-share suffix normalization: .SS -> .SH, .SHE/.SZE -> .SZ
# DB stores tickers WITH suffixes (e.g. "002027.SZ"), so we preserve them.
_A_SHARE_RE = re.compile(r'^(\d{6})\.(SH|SZ|SS|SHE|SZE)$', re.IGNORECASE)
_SUFFIX_MAP = {'SS': 'SH', 'SHE': 'SZ', 'SZE': 'SZ'}

# In-process cache  {normalized_ticker: company_name}
_cache: dict[str, str] = {}
_lock = threading.Lock()


def normalize_ticker(raw_ticker: str) -> str:
    """Normalize a ticker to the canonical DB storage format.

    - Strip whitespace, uppercase
    - A-shares (6-digit): normalize suffix .SS->.SH, .SHE/.SZE->.SZ
    - Bare 6-digit codes are kept as-is (no suffix added)
    - US/international: preserve as-is (e.g. AAPL, CNC.TO)

    >>> normalize_ticker('600519.SH')
    '600519.SH'
    >>> normalize_ticker('600519.SS')
    '600519.SH'
    >>> normalize_ticker('000858.SZ')
    '000858.SZ'
    >>> normalize_ticker('002027')
    '002027'
    >>> normalize_ticker('AAPL')
    'AAPL'
    """
    t = raw_ticker.strip().upper()
    if not t:
        return t
    m = _A_SHARE_RE.match(t)
    if m:
        code = m.group(1)
        suffix = m.group(2).upper()
        canonical_suffix = _SUFFIX_MAP.get(suffix, suffix)
        return f'{code}.{canonical_suffix}'
    return t


def resolve_company_name(ticker: str) -> Tuple[str, str]:
    """Resolve (normalized_ticker, company_name) with in-memory caching.

    Uses yfinance for US/international tickers.  For Chinese A-shares,
    falls back to a built-in lookup table when yfinance returns an
    English-only name or is unavailable / rate-limited.

    Returns ('', '') on empty input.  On resolution failure the
    company_name is an empty string -- callers should treat that as
    "unresolved, use ticker as fallback display".
    """
    normalized = normalize_ticker(ticker)
    if not normalized:
        return '', ''

    with _lock:
        cached = _cache.get(normalized)
        if cached is not None:
            return normalized, cached

    name = ''
    # Try yfinance first
    try:
        import yfinance as yf
        import sys, io
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            info = yf.Ticker(normalized).info
            name = str(info.get('shortName') or info.get('longName') or '')
        finally:
            sys.stderr = old_stderr
    except Exception:
        pass

    # yfinance returned an English name or failed — prefer built-in CN lookup
    if _is_cn_ticker(normalized):
        cn_name = _CN_TICKER_NAMES.get(normalized, '')
        if cn_name:
            name = cn_name

    with _lock:
        _cache[normalized] = name
    return normalized, name


def _is_cn_ticker(ticker: str) -> bool:
    """Check if a ticker is a Chinese A-share."""
    t = ticker.strip().upper()
    if any(t.endswith(s) for s in ('.SH', '.SZ', '.SS', '.BJ')):
        return True
    if t.isdigit() and len(t) == 6:
        return True
    return False


# Built-in lookup for common A-share tickers that yfinance may not
# resolve reliably.  Updated as new tickers are encountered.
_CN_TICKER_NAMES: dict[str, str] = {
    # 上证主板
    '600519.SH': '贵州茅台',
    '600036.SH': '招商银行',
    '600276.SH': '恒瑞医药',
    '600900.SH': '长江电力',
    '600585.SH': '海螺水泥',
    '600809.SH': '山西汾酒',
    '600030.SH': '中信证券',
    '601318.SH': '中国平安',
    '601012.SH': '隆基绿能',
    '601088.SH': '中国神华',
    '601899.SH': '紫金矿业',
    '601857.SH': '中国石油',
    # 深证主板
    '000001.SZ': '平安银行',
    '000333.SZ': '美的集团',
    '000651.SZ': '格力电器',
    '000568.SZ': '泸州老窖',
    '000858.SZ': '五粮液',
    '002415.SZ': '海康威视',
    '002475.SZ': '立讯精密',
    '002594.SZ': '比亚迪',
    # 科创板
    '688981.SH': '中芯国际',
    '688380.SH': '中微半导',
    '688256.SH': '寒武纪',
    '688545.SH': '兴福电子',
    '688630.SH': '芯碁微装',
    '688183.SH': '生益电子',
    # 沪市其他
    '601127.SH': '赛力斯',
}


def clear_name_cache() -> None:
    """Evict the in-process name cache (useful for tests)."""
    with _lock:
        _cache.clear()


def verify_cn_stock(query: str) -> str:
    """Check the built-in A-share lookup table for a ticker or company name.

    Returns the canonical ticker (e.g. '688380.SH') on match, or empty string.
    Case-insensitive on company names.
    """
    t = query.strip().upper()
    # Direct ticker lookup
    if t in _CN_TICKER_NAMES:
        return _CN_TICKER_NAMES[t]
    if normalize_ticker(t) in _CN_TICKER_NAMES:
        return _CN_TICKER_NAMES[normalize_ticker(t)]
    # Company name search (case-insensitive)
    for tk, cn_name in _CN_TICKER_NAMES.items():
        if cn_name.lower() == query.strip().lower():
            return tk
    return ''


def list_known_cn_tickers() -> dict[str, str]:
    """Return a copy of the known CN ticker → name mapping."""
    return dict(_CN_TICKER_NAMES)


def ticker_to_search_query(user_query: str, ticker: str) -> str:
    """Enrich a web-search query with the resolved company name.

    When the LLM asks to search the web for a stock, the query may not
    contain the full company name.  This function prepends the ticker
    and the resolved company name so the search targets the right
    company.

    Returns the enriched query string.
    """
    normalized, name = resolve_company_name(ticker)
    if not normalized:
        return user_query

    q_upper = user_query.upper()
    # Already mentions the ticker -- don't duplicate
    if normalized.upper() in q_upper:
        enriched = user_query
    elif name and name.lower() in user_query.lower():
        enriched = f'{normalized} {user_query}'
    elif name:
        enriched = f'{normalized} {name} {user_query}'
    else:
        enriched = f'{normalized} {user_query}'
    return enriched
