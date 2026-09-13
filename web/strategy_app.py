"""短线策略分析 Standalone FastAPI app (port 8006).
Shares results.db — zero changes to existing project files.
"""

from __future__ import annotations

import json, logging, os, sys
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv(usecwd=True), override=False)
from capitalradar.default_config import DEFAULT_CONFIG

logger = logging.getLogger("strategy_app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

app = FastAPI(title="短线策略分析")

_static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

@app.get("/")
def index():
    p = os.path.join(_static_dir, "strategy.html")
    return HTMLResponse(open(p, encoding="utf-8").read()) if os.path.isfile(p) else HTMLResponse("Not found", 404)


# ═══════════════════════ data helpers ═══════════════════════

def _load_env():
    load_dotenv(find_dotenv(usecwd=True), override=False)

def _get_rows(source: str, days: int) -> list[dict]:
    from web.watchlist_store import _get_conn
    conn = _get_conn(DEFAULT_CONFIG)
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        if source and source != "all":
            rows = conn.execute(
                """SELECT wa.*, ws.name, ws.source
                   FROM watchlist_analysis wa JOIN watchlist_stocks ws ON wa.code = ws.code
                   WHERE wa.analyzed_at >= ? AND ws.source = ? ORDER BY wa.analyzed_at DESC""",
                (cutoff, source)).fetchall()
        else:
            rows = conn.execute(
                """SELECT wa.*, ws.name, ws.source
                   FROM watchlist_analysis wa LEFT JOIN watchlist_stocks ws ON wa.code = ws.code
                   WHERE wa.analyzed_at >= ? ORDER BY wa.analyzed_at DESC""",
                (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def _batch_prices(codes: list[str]) -> dict[str, float | None]:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from capitalradar.dataflows.tencent_realtime import _normalize_symbol
    import requests as _req
    prices: dict[str, float | None] = {}
    def _fetch(batch):
        try:
            norms = [_normalize_symbol(c) for c in batch]
            resp = _req.get(f"http://qt.gtimg.cn/q={','.join(norms)}", timeout=10)
            resp.encoding = "gbk"
            for c in batch:
                n = _normalize_symbol(c); pf = f"v_{n}="
                for ln in resp.text.split("\n"):
                    if pf in ln and '="' in ln:
                        fld = ln.split('="')[1].rstrip('";\r\n').split("~")
                        if len(fld) > 3 and fld[3]: prices[c] = float(fld[3])
                        else: prices.setdefault(c, None)
                        break
                else: prices.setdefault(c, None)
        except Exception:
            for c in batch: prices.setdefault(c, None)
    batches = [codes[i:i+50] for i in range(0, len(codes), 50)]
    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(_fetch, batches))
    return prices

def _compute_returns(rows: list[dict]) -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    codes = list({r["code"] for r in rows})
    prices = _batch_prices(codes)
    results = []
    for r in rows:
        code = r["code"]; ad = (r.get("analyzed_at","") or "")[:10]
        v = r.get("verdict",""); cur = prices.get(code)
        rts = r.get("rt_price","")
        if ad <= today:
            try: bp = float(str(rts).replace(",","")) if rts else None
            except: bp = None
            if bp and bp > 0 and cur and cur > 0:
                if v and "看空" in str(v): rp = round((bp - cur) / bp * 100.0, 2)
                elif v and "观望" in str(v): rp = None
                else: rp = round((cur - bp) / bp * 100.0, 2)
                r["return_pct"] = rp; r["buy_price"] = round(bp,2); r["sell_price"] = round(cur,2)
            else: r["return_pct"] = r["buy_price"] = r["sell_price"] = None
        else: r["return_pct"] = r["buy_price"] = r["sell_price"] = None
        results.append(r)
    return results

def _get_latest_per_stock(source: str = "all") -> list[dict]:
    """Return the most recent analysis for each stock (deduped by code)."""
    rows = _get_rows(source, 30)
    seen = {}
    for r in rows:  # already sorted by analyzed_at DESC from DB
        code = r["code"]
        if code not in seen:
            seen[code] = r
    return list(seen.values())

# ═══════════════════════ single rich endpoint ═══════════════════════

@app.get("/api/strategy/full")
def get_full_analysis(source: str = Query(default="all"), days: str = Query(default="latest")):
    if days == "latest":
        rows = _get_latest_per_stock(source)
    else:
        rows = _get_rows(source, int(days))
    if not rows: return {"error": "No data", "evaluation": {}, "sectors": [], "macro": {}, "records": []}
    enriched = _compute_returns(rows)
    wr = [r for r in enriched if r.get("return_pct") is not None]
    total = len(wr)
    if total == 0: return {"error": "No return data (try more days)", "evaluation": {}, "sectors": [], "macro": {}, "records": []}
    # Build clean records array for frontend display
    records = []
    for r in enriched:
        records.append({
            "code": r["code"], "name": r.get("name", ""),
            "verdict": r.get("verdict", ""), "model_name": r.get("model_name", ""),
            "analyzed_at": (r.get("analyzed_at", "") or "")[:19],
            "rt_price": r.get("rt_price", ""), "current_price": r.get("sell_price"),
            "return_pct": r.get("return_pct"),
        })

    # ── Evaluation ──
    wins = [r for r in wr if r["return_pct"] > 0]; losses = [r for r in wr if r["return_pct"] <= 0]
    win_rate = round(len(wins)/total*100,1)
    avg_ret = round(sum(r["return_pct"] for r in wr)/total,2)
    rets = sorted([r["return_pct"] for r in wr])
    cum_ret = round(sum(rets), 2)
    best = round(rets[-1],2); worst = round(rets[0],2)
    # Std dev + sharpe-like
    import statistics; std_dev = round(statistics.stdev(rets), 2) if len(rets) > 1 else 0
    sharpe = round(avg_ret/std_dev,2) if std_dev > 0 else 0
    max_dd = 0; peak = 0
    for rp in rets:
        peak = max(peak, peak + rp)
        dd = peak - (peak + rp); max_dd = max(max_dd, dd)
    max_dd = round(max_dd, 2)

    by_model = {}; by_verdict = {}; by_source = {}
    for r in wr:
        m = r.get("model_name","?"); by_model.setdefault(m,[]).append(r)
        v = r.get("verdict","观望"); by_verdict.setdefault(v,[]).append(r)
        s = r.get("source","?"); by_source.setdefault(s,[]).append(r)

    def _stats(items): return {"count":len(items),"win_rate":round(len([x for x in items if x["return_pct"]>0])/len(items)*100,1),"avg_return":round(sum(x["return_pct"] for x in items)/len(items),2)}

    evaluation = {
        "total": total, "total_all": len(rows),
        "win_rate": win_rate, "avg_return": avg_ret, "cum_return": cum_ret,
        "best_return": best, "worst_return": worst,
        "std_dev": std_dev, "sharpe": sharpe, "max_drawdown": max_dd,
        "buy_count": sum(1 for r in rows if r.get("verdict")=="看多"),
        "sell_count": sum(1 for r in rows if r.get("verdict")=="看空"),
        "hold_count": sum(1 for r in rows if r.get("verdict") not in ("看多","看空")),
        "by_model": [{"model":k, **_stats(v)} for k,v in sorted(by_model.items(),key=lambda x:-len(x[1]))],
        "by_verdict": [{"verdict":k, **_stats(v)} for k,v in by_verdict.items()],
        "by_source": [{"source":k, **_stats(v)} for k,v in by_source.items()],
        "return_dist": _dist(rets),
        "daily": _daily_breakdown(wr),
    }

    # ── Stock-level ──
    stock_map = {}
    for r in wr:
        c = r["code"]; stock_map.setdefault(c,{"code":c,"name":r.get("name",c),"returns":[],"verdicts":[]})
        stock_map[c]["returns"].append(r["return_pct"]); stock_map[c]["verdicts"].append(r.get("verdict",""))
    stocks_avg = [{"code":s["code"],"name":s["name"],"count":len(s["returns"]),"avg_return":round(sum(s["returns"])/len(s["returns"]),2),
                    "win_rate":round(len([x for x in s["returns"] if x>0])/len(s["returns"])*100,1),
                    "verdicts": list(set(s["verdicts"]))} for s in stock_map.values()]
    stocks_avg.sort(key=lambda x:-(x["avg_return"] or 0))
    evaluation["top_stocks"] = stocks_avg[:15]
    evaluation["bottom_stocks"] = stocks_avg[-15:][::-1] if len(stocks_avg)>=15 else []

    # ── Sectors ──
    code_to_ind = {}
    try:
        _load_env(); import tushare as ts
        token = os.environ.get("TUSHARE_TOKEN","")
        if token:
            pro = ts.pro_api(token)
            ucodes = list({r["code"] for r in wr})
            for i in range(0,len(ucodes),500):
                try:
                    basic = pro.stock_basic(ts_code=",".join(ucodes[i:i+500]), fields="ts_code,industry")
                    if basic is not None and not basic.empty:
                        for _,r2 in basic.iterrows(): code_to_ind[r2["ts_code"]] = r2.get("industry","其他")
                except: pass
    except: pass

    ind_map = {}
    for r in wr:
        ind = code_to_ind.get(r["code"],"其他"); ind_map.setdefault(ind,[]).append(r)
    sectors = []
    for ind, items in ind_map.items():
        scodes = list({r["code"] for r in items})
        sstocks = []
        for c2 in scodes:
            citems = [r for r in items if r["code"]==c2]
            sstocks.append({"code":c2,"name":citems[0].get("name",c2),"count":len(citems),"avg_return":round(sum(r["return_pct"] for r in citems)/len(citems),2)})
        sstocks.sort(key=lambda x:-(x["avg_return"] or 0))
        sectors.append({"industry":ind,"count":len(items),"stock_count":len(scodes),**_stats(items),"top_stocks":sstocks[:5]})
    sectors.sort(key=lambda x:-(x["avg_return"] or 0))

    # ── Macro ──
    macro = {
        "source_distribution": [{"source":k,"count":len(v),"pct":round(len(v)/total*100,1)} for k,v in by_source.items()],
        "verdict_split": {"看多_pct":round(evaluation["buy_count"]/evaluation["total_all"]*100,1) if evaluation["total_all"] else 0,
                          "看空_pct":round(evaluation["sell_count"]/evaluation["total_all"]*100,1) if evaluation["total_all"] else 0,
                          "观望_pct":round(evaluation["hold_count"]/evaluation["total_all"]*100,1) if evaluation["total_all"] else 0},
        "hot_sectors": [s["industry"] for s in sectors[:5] if s["win_rate"]>=50],
        "cold_sectors": [s["industry"] for s in sectors[-5:] if s["win_rate"]<50],
        "best_model": evaluation["by_model"][0]["model"] if evaluation["by_model"] else "",
    }

    return {"records": records, "evaluation": evaluation, "sectors": sectors, "macro": macro}

def _dist(rets):
    buckets = [(-20,-10),(-10,-5),(-5,-3),(-3,-1),(-1,0),(0,1),(1,3),(3,5),(5,10),(10,20)]
    return [{"range":f"{lo}~{hi}%","count":sum(1 for r in rets if lo<=r<hi)} for lo,hi in buckets]

def _daily_breakdown(wr):
    dm = {}
    for r in wr:
        d = (r.get("analyzed_at","") or "")[:10]; dm.setdefault(d,[]).append(r["return_pct"])
    return [{"date":d,"avg_return":round(sum(v)/len(v),2),"count":len(v),"win_rate":round(len([x for x in v if x>0])/len(v)*100,1)} for d,v in sorted(dm.items())]

# ═══════════════════════ daily breakdown ═══════════════════════

@app.get("/api/strategy/daily-breakdown")
def get_daily_breakdown(source: str = Query(default="all"), days: str = Query(default="latest"),
                         model: str = Query(default="")):
    """Return daily-grouped analysis. Optional model filter for per-model breakdown."""
    if days == "latest":
        rows = _get_latest_per_stock(source)
    else:
        rows = _get_rows(source, int(days))
    if not rows: return {"daily": [], "bull_analysis": {}}

    enriched = _compute_returns(rows)

    # Optional model filter
    if model:
        enriched = [r for r in enriched if r.get("model_name","") == model]

    # Dedupe: per day, per stock, keep only the LAST analysis (newest analyzed_at)
    deduped: dict[str, dict] = {}
    for r in enriched:
        d = (r.get("analyzed_at","") or "")[:10]
        key = f"{d}|{r['code']}"
        if key not in deduped or (r.get("analyzed_at","") or "") > (deduped[key].get("analyzed_at","") or ""):
            deduped[key] = r
    enriched = list(deduped.values())

    # Load tushare for industry mapping (once, before daily loop)
    _load_env()
    ind_map: dict[str, str] = {}
    try:
        import tushare as ts; import os as _os
        token = _os.environ.get("TUSHARE_TOKEN","")
        if token:
            pro = ts.pro_api(token)
            ucodes = list({r["code"] for r in enriched})
            for i in range(0,len(ucodes),500):
                try:
                    basic = pro.stock_basic(ts_code=",".join(ucodes[i:i+500]), fields="ts_code,industry")
                    if basic is not None and not basic.empty:
                        for _,r2 in basic.iterrows(): ind_map[r2["ts_code"]] = r2.get("industry","其他")
                except: pass
    except: pass

    # Group by date
    date_map: dict[str, list] = {}
    for r in enriched:
        d = (r.get("analyzed_at","") or "")[:10]
        date_map.setdefault(d, []).append(r)

    daily = []
    for d in sorted(date_map.keys(), reverse=True):
        items = date_map[d]
        with_ret = [r for r in items if r.get("return_pct") is not None]
        bulls = [r for r in items if r.get("verdict") == "看多"]
        bears = [r for r in items if r.get("verdict") == "看空"]
        holds = [r for r in items if r.get("verdict") not in ("看多","看空")]
        bulls_wr = [r for r in bulls if r.get("return_pct") is not None]
        bears_wr = [r for r in bears if r.get("return_pct") is not None]

        # Models per verdict
        models = {}
        for r in items:
            m = r.get("model_name","?"); v = r.get("verdict","观望")
            models.setdefault(m, {"看多":0,"看空":0,"观望":0})
            models[m][v] = models[m].get(v,0) + 1

        # Sectors per verdict (uses pre-loaded ind_map)
        sectors_v = {}
        for r in items:
            ind = ind_map.get(r["code"],"其他"); v = r.get("verdict","观望")
            sectors_v.setdefault(ind, {"看多":0,"看空":0,"观望":0})
            sectors_v[ind][v] = sectors_v[ind].get(v,0) + 1

        # Top bull stocks
        top_bulls = sorted(bulls_wr, key=lambda r: -(r.get("return_pct") or 0))[:5]
        top_bull_stocks = [{"code":r["code"],"name":r.get("name",""),"return_pct":r.get("return_pct"),"model":r.get("model_name","")} for r in top_bulls]

        daily.append({
            "date": d,
            "total": len(items), "with_returns": len(with_ret),
            "看多_count": len(bulls), "看空_count": len(bears), "观望_count": len(holds),
            "看多_win_rate": round(len([r for r in bulls_wr if r["return_pct"]>0])/len(bulls_wr)*100,1) if bulls_wr else 0,
            "看多_avg_return": round(sum(r["return_pct"] for r in bulls_wr)/len(bulls_wr),2) if bulls_wr else 0,
            "看空_win_rate": round(len([r for r in bears_wr if r["return_pct"]>0])/len(bears_wr)*100,1) if bears_wr else 0,
            "看空_avg_return": round(sum(r["return_pct"] for r in bears_wr)/len(bears_wr),2) if bears_wr else 0,
            "models": {m: v for m,v in sorted(models.items(), key=lambda x: -sum(x[1].values()))[:5]},
            "sectors": {s: v for s,v in sorted(sectors_v.items(), key=lambda x: -sum(x[1].values()))[:5]},
            "top_stocks_看多": top_bull_stocks,
        })

    # Bull-focused analysis
    all_bulls = [r for r in enriched if r.get("verdict") == "看多"]
    bulls_with_ret = [r for r in all_bulls if r.get("return_pct") is not None]
    bull_sectors = {}
    bull_models = {}
    for r in all_bulls:
        ind = ind_map.get(r["code"],"其他"); bull_sectors[ind] = bull_sectors.get(ind,0)+1
        m = r.get("model_name","?"); bull_models[m] = bull_models.get(m,0)+1
    bull_trend = {}
    for r in bulls_with_ret:
        d = (r.get("analyzed_at","") or "")[:10]
        bull_trend.setdefault(d, []).append(r["return_pct"])

    bull_analysis = {
        "total_看多": len(all_bulls),
        "with_returns": len(bulls_with_ret),
        "without_returns": len(all_bulls) - len(bulls_with_ret),
        "win_rate": round(len([r for r in bulls_with_ret if r["return_pct"]>0])/len(bulls_with_ret)*100,1) if bulls_with_ret else 0,
        "avg_return": round(sum(r["return_pct"] for r in bulls_with_ret)/len(bulls_with_ret),2) if bulls_with_ret else 0,
        "by_sector": [{"sector":k,"count":v,"win_rate":round(len([r for r in bulls_with_ret if r.get("code") and ind_map.get(r["code"])==k and r["return_pct"]>0])/max(1,len([r for r in bulls_with_ret if r.get("code") and ind_map.get(r["code"])==k]))*100,1)} for k,v in sorted(bull_sectors.items(),key=lambda x:-x[1])[:15]],
        "by_model": [{"model":k,"count":v} for k,v in sorted(bull_models.items(),key=lambda x:-x[1])],
        "daily_trend": [{"date":d,"count":len(v),"win_rate":round(len([x for x in v if x>0])/len(v)*100,1),"avg_return":round(sum(v)/len(v),2)} for d,v in sorted(bull_trend.items())],
    }

    # Sector→stock mapping for clickable chart detail
    sector_stocks = {}
    all_bulls_enriched = [r for r in enriched if r.get("verdict") == "看多"]
    for r in all_bulls_enriched:
        ind = ind_map.get(r["code"], "其他")
        sector_stocks.setdefault(ind, []).append({
            "code": r["code"], "name": r.get("name", ""),
            "return_pct": r.get("return_pct"), "model": r.get("model_name", "")
        })
    for k in sector_stocks:
        sector_stocks[k].sort(key=lambda x: -(x["return_pct"] or -999))

    return {"daily": daily, "bull_analysis": bull_analysis, "sector_stocks": sector_stocks}


# ═══════════════════════ LLM report ═══════════════════════

class LLMBody(BaseModel):
    evaluation: dict; sectors: list; macro: dict
    provider: str = ""; model: str = ""
    prompt: str = ""

@app.post("/api/strategy/llm-report")
def llm_report(body: LLMBody):
    _load_env()
    prompt = body.prompt
    if not prompt:
        ev=body.evaluation;se=body.sectors;ma=body.macro
        top_s="\n".join(f"- {s.get('code','')} {s.get('name','')}" for s in ev.get("top_stocks",[])[:5])
        mod="\n".join(f"| {m['model']} | {m['count']} | {m['win_rate']}% | {m['avg_return']:+.2f}% |" for m in ev.get("by_model",[]))
        sec="\n".join(f"| {s['industry']} | {s['count']} | {s['stock_count']} | {s['win_rate']}% | {s['avg_return']:+.2f}% |" for s in se[:15])
        prompt=f"""你是A股量化策略分析师，请分析以下短线策略数据。

## 策略评估
有效分析{ev.get('total',0)}次(总{ev.get('total_all',0)}) | 胜率{ev.get('win_rate',0)}% | 均益{ev.get('avg_return',0):+.2f}% | 累计{ev.get('cum_return',0):+.2f}% | 夏普{ev.get('sharpe',0)} | 波动{ev.get('std_dev',0):.2f}%

## 模型表现
{mod}

## 行业板块
{sec}

## 最佳
{top_s}

请简短分析策略效果、模型选择、风险点、操作建议。中文。"""

    try:
        from capitalradar.llm_clients import create_llm_client, resolve_role_llm
        _p = body.provider or resolve_role_llm(DEFAULT_CONFIG, "deep")[0]
        _m = body.model or resolve_role_llm(DEFAULT_CONFIG, "deep")[1]
        _bu = DEFAULT_CONFIG.get("backend_url") or None; _ex = {}
        if _p in ("ollama","ollama2"):
            _bu = (os.environ.get("OLLAMA2_BASE_URL" if _p=="ollama2" else "OLLAMA_BASE_URL") or DEFAULT_CONFIG.get("backend_url") or None)
            _ex["extra_body"] = {"chat_template_kwargs":{"enable_thinking":False}}
        client = create_llm_client(provider=_p, model=_m, base_url=_bu, timeout=300, **_ex)
        llm = client.get_llm(); resp = llm.invoke(prompt)
        text = resp.content if hasattr(resp,"content") else str(resp)
    except Exception:
        logger.exception("LLM report generation failed")
        text = "# AI分析暂时不可用\n\n调用LLM时出错，请检查模型配置后重试。"

    return {"report":text,"model_name":f"{_p}/{_m}" if _m else _p,"generated_at":datetime.now().isoformat(timespec="seconds")}

class DocxRequest(BaseModel):
    report: str; title: str = ""; model: str = ""

@app.post("/api/strategy/llm-report-docx")
def llm_report_docx(body: DocxRequest):
    """Generate DOCX from LLM report markdown."""
    from web.docx_export import _build_zip
    import xml.etree.ElementTree as ET, re
    _WML="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ET.register_namespace("w",_WML)
    _XML='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    body_el=ET.Element(f"{{{_WML}}}body")
    # Title
    p=ET.SubElement(body_el,f"{{{_WML}}}p");pr=ET.SubElement(p,f"{{{_WML}}}pPr");ET.SubElement(pr,f"{{{_WML}}}jc",**{f"{{{_WML}}}val":"center"})
    r=ET.SubElement(p,f"{{{_WML}}}r");rp=ET.SubElement(r,f"{{{_WML}}}rPr");ET.SubElement(rp,f"{{{_WML}}}b");ET.SubElement(rp,f"{{{_WML}}}sz",**{f"{{{_WML}}}val":"36"})
    ET.SubElement(r,f"{{{_WML}}}t").text=body.title or "策略分析报告"
    if body.model:
        p2=ET.SubElement(body_el,f"{{{_WML}}}p");r2=ET.SubElement(p2,f"{{{_WML}}}r");rp2=ET.SubElement(r2,f"{{{_WML}}}rPr");ET.SubElement(rp2,f"{{{_WML}}}sz",**{f"{{{_WML}}}val":"20"})
        ET.SubElement(r2,f"{{{_WML}}}t").text=f"生成模型: {body.model}"
    # Convert markdown to simple paragraphs
    for line in body.report.replace("\r\n", "\n").split("\n"):
        t = line.strip()
        if not t:
            continue
        if t.startswith("### "):
            p3=ET.SubElement(body_el,f"{{{_WML}}}p");pr3=ET.SubElement(p3,f"{{{_WML}}}pPr");ET.SubElement(pr3,f"{{{_WML}}}spacing",before="200",after="80")
            r3=ET.SubElement(p3,f"{{{_WML}}}r");rp3=ET.SubElement(r3,f"{{{_WML}}}rPr");ET.SubElement(rp3,f"{{{_WML}}}b");ET.SubElement(rp3,f"{{{_WML}}}sz",**{f"{{{_WML}}}val":"28"})
            ET.SubElement(r3,f"{{{_WML}}}t").text=t[4:]
        elif t.startswith("## "):
            p3=ET.SubElement(body_el,f"{{{_WML}}}p");pr3=ET.SubElement(p3,f"{{{_WML}}}pPr");ET.SubElement(pr3,f"{{{_WML}}}spacing",before="240",after="100")
            r3=ET.SubElement(p3,f"{{{_WML}}}r");rp3=ET.SubElement(r3,f"{{{_WML}}}rPr");ET.SubElement(rp3,f"{{{_WML}}}b");ET.SubElement(rp3,f"{{{_WML}}}sz",**{f"{{{_WML}}}val":"32"})
            ET.SubElement(r3,f"{{{_WML}}}t").text=t[3:]
        elif t.startswith("|") and "|" in t[1:]:
            cells=[c.strip() for c in t.strip("|").split("|")]
            if all(re.match(r'^[-: ]+$',c) for c in cells):continue
            tbl=ET.SubElement(body_el,f"{{{_WML}}}tbl")
            tr_el=ET.SubElement(tbl,f"{{{_WML}}}tr")
            for c in cells:
                tc=ET.SubElement(tr_el,f"{{{_WML}}}tc");tp=ET.SubElement(tc,f"{{{_WML}}}p")
                tr2=ET.SubElement(tp,f"{{{_WML}}}r");ET.SubElement(tr2,f"{{{_WML}}}t").text=c
        else:
            p4=ET.SubElement(body_el,f"{{{_WML}}}p");r4=ET.SubElement(p4,f"{{{_WML}}}r");rp4=ET.SubElement(r4,f"{{{_WML}}}rPr");ET.SubElement(rp4,f"{{{_WML}}}sz",**{f"{{{_WML}}}val":"22"})
            ET.SubElement(r4,f"{{{_WML}}}t").text=t
    doc_el=ET.Element(f"{{{_WML}}}document");doc_el.append(body_el)
    docx=_build_zip(_XML+ET.tostring(doc_el,encoding="unicode"))
    from fastapi.responses import Response
    return Response(content=docx,media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition":"attachment; filename=strategy-report.docx"})

if __name__=="__main__":
    import uvicorn; uvicorn.run(app, host="127.0.0.1", port=8006, log_level="info")
