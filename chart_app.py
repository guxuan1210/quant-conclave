"""Standalone K-line chart server — port comes from the runtime manifest.

OHLCV fetching is delegated to ``capitalradar.dataflows.ohlcv.fetch_ohlcv``,
which is network-aware: A-shares use the tushare vendor chain first (reliable
on this deployment), non-A-shares use yfinance first, and weekly/monthly bars
are resampled from daily data when yfinance has no native bars.
"""
import logging
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

app = FastAPI(title="CapitalRadar Chart")
_web_dir = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=str(_web_dir / "static")), name="static")


@app.get("/")
def index():
    """Serve the chart page."""
    return FileResponse(str(_web_dir / "static" / "chart.html"))


@app.get("/api/search")
def search_stocks(q: str = Query(min_length=1)):
    """Search stocks via yfinance, with an A-share fallback through tushare."""
    _load_env()
    try:
        results = yf.Search(query=q, news_count=0).quotes
        items = []
        for r in (results or [])[:8]:
            sym = r.get("symbol", "")
            if not sym:
                continue
            items.append({
                "symbol": sym,
                "name": r.get("shortname") or r.get("longname") or sym,
                "exchange": r.get("exchange", ""),
                "type": r.get("quoteType", ""),
            })
        if items:
            return items
    except Exception:
        pass
    # Fallback: A-share fuzzy search via tushare stock_basic
    try:
        from capitalradar.dataflows.tushare_data import _get_pro
        pro = _get_pro()
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,name,industry,market")
        if df is not None and not df.empty:
            m = df[df["name"].str.contains(q, na=False) | df["ts_code"].str.contains(q, na=False)]
            # itertuples() yields NamedTuples — use getattr, not .get()
            return [{"symbol": r.ts_code, "name": r.name,
                     "exchange": getattr(r, "market", "") or "", "type": "stock"}
                    for r in m.head(8).itertuples()]
    except Exception:
        pass
    return []


def _load_env():
    """Load the project .env so TUSHARE_TOKEN / MX_APIKEY are available."""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    except Exception:
        pass


def _get_history(ticker: str, period: str = "1mo", interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV via the shared network-aware helper (A-shares: tushare chain
    first; non-A-shares: yfinance first; weekly/monthly resampled as fallback)."""
    _load_env()
    from capitalradar.dataflows.ohlcv import fetch_ohlcv
    df = fetch_ohlcv(ticker, period=period, interval=interval)
    return df if df is not None else pd.DataFrame()


def _fetch_intraday_candle(ticker: str) -> dict | None:
    """Fetch today's intraday 5-min bars from Tencent and build a synthetic daily candle."""
    import requests, datetime
    try:
        from capitalradar.dataflows.tencent_realtime import _normalize_symbol
        norm = _normalize_symbol(ticker)
        resp = requests.get(f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={norm},m5,,80", timeout=8)
        data = resp.json()
        bars = data.get("data", {}).get(norm, {}).get("m5", [])
        if not bars: return None
        today = datetime.date.today().isoformat()
        opens = [float(b[1]) for b in bars if b[1]]
        closes = [float(b[2]) for b in bars if b[2]]
        highs = [float(b[3]) for b in bars if b[3]]
        lows = [float(b[4]) for b in bars if b[4]]
        vols = [int(b[5]) for b in bars if b[5]]
        if not opens: return None
        return {"Date": today, "Open": opens[0], "High": max(highs), "Low": min(lows),
                "Close": closes[-1], "Volume": sum(vols)}
    except Exception:
        return None


@app.get("/api/history/{ticker}/indicators")
def history_indicators(ticker: str, period: str = Query(default="1mo")):
    """Return OHLCV + MA/MACD/RSI indicators. Injects today's intraday candle."""
    try:
        data = _get_history(ticker, period=period, interval="1d")
        # Inject today's intraday candle if available
        intra = _fetch_intraday_candle(ticker)
        if intra and not data.empty:
            import pandas as pd
            last_hist_date = str(data.index[-1].date()) if hasattr(data.index[-1], "date") else ""
            if str(intra["Date"]) != last_hist_date:
                intra_df = pd.DataFrame([intra]).set_index("Date")
                intra_df.index = pd.to_datetime(intra_df.index)
                data = pd.concat([data, intra_df])
        if data.empty:
            return {"error": "No data", "candles": []}
        return _indicators(data)
    except Exception as e:
        logger.exception("indicators failed for %s", ticker)
        return {"error": str(e), "candles": []}


@app.get("/api/history/{ticker}/weekly")
def history_weekly(ticker: str):
    try:
        data = _get_history(ticker, period="max", interval="1wk")
        return _indicators(data) if not data.empty else {"error": "No data", "candles": []}
    except Exception as e:
        return {"error": str(e), "candles": []}


@app.get("/api/history/{ticker}/monthly")
def history_monthly(ticker: str):
    try:
        data = _get_history(ticker, period="max", interval="1mo")
        return _indicators(data) if not data.empty else {"error": "No data", "candles": []}
    except Exception as e:
        return {"error": str(e), "candles": []}


def _indicators(data):
    data = data.reset_index()
    if "Date" in data.columns:
        data["Date"] = data["Date"].dt.strftime("%Y-%m-%d")
    elif "Datetime" in data.columns:
        data["Date"] = data["Datetime"].dt.strftime("%Y-%m-%d %H:%M")
    closes = data["Close"].astype(float)
    ma5 = closes.rolling(5).mean()
    ma20 = closes.rolling(20).mean()
    ema12 = closes.ewm(span=12).mean()
    ema26 = closes.ewm(span=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9).mean()
    macd_bar = 2 * (dif - dea)
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / loss.replace(0, 1)
    rsi = 100 - (100 / (1 + rs))
    candles = []
    for i, (_, row) in enumerate(data.iterrows()):
        open_val = float(row["Open"])
        high_val = float(row["High"])
        low_val = float(row["Low"])
        close_val = float(row["Close"])
        vol_val = float(row["Volume"])
        if any(v != v for v in [open_val, high_val, low_val, close_val]):
            continue
        candles.append({
            "time": str(row.get("Date", "")),
            "open": round(open_val, 2), "high": round(high_val, 2),
            "low": round(low_val, 2), "close": round(close_val, 2),
            "volume": int(vol_val),
            "ma5": round(float(ma5.iloc[i]), 2) if not pd.isna(ma5.iloc[i]) else None,
            "ma20": round(float(ma20.iloc[i]), 2) if not pd.isna(ma20.iloc[i]) else None,
            "dif": round(float(dif.iloc[i]), 4) if not pd.isna(dif.iloc[i]) else None,
            "dea": round(float(dea.iloc[i]), 4) if not pd.isna(dea.iloc[i]) else None,
            "macd": round(float(macd_bar.iloc[i]), 4) if not pd.isna(macd_bar.iloc[i]) else None,
            "rsi": round(float(rsi.iloc[i]), 1) if not pd.isna(rsi.iloc[i]) else None,
        })
    return {"candles": candles}


if __name__ == "__main__":
    from capitalradar.runtime_manifest import HOST, chart_port
    uvicorn.run(app, host=HOST, port=chart_port())
