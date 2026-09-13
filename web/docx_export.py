"""Generate .docx (Word) reports from CapitalRadar session data. Uses only stdlib."""
import logging, time, zipfile, io, re, xml.etree.ElementTree as ET
from datetime import datetime

_log = logging.getLogger(__name__)

_WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT  = "http://schemas.openxmlformats.org/package/2006/content-types"
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# Register namespace prefix so ET.tostring produces <w:p> etc.
ET.register_namespace("w", _WML)
ET.register_namespace("r", _REL)
ET.register_namespace("ct", _CT)

_STYLES = _XML + ET.tostring(ET.fromstring(
    '<w:styles xmlns:w="' + _WML + '">'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/>'
    '<w:pPr><w:jc w:val="both"/></w:pPr>'
    '<w:rPr><w:rFonts w:ascii="仿宋" w:hAnsi="仿宋" w:eastAsia="仿宋"/>'
    '<w:sz w:val="24"/></w:rPr>'
    '</w:style>'
    '</w:styles>'), encoding="unicode")

# ── Persistent session store ──────────────────────────────────────────
# _session_results is cleaned up when the SSE stream ends, but the user
# may click the DOCX download button *after* that.  We keep a persistent
# copy here that survives the stream lifecycle.
#
# Format: {session_id: {"data": {...}, "stored_at": timestamp}}
_PERSISTED: dict = {}
_PERSISTED_MAX_AGE = 3600  # 1 hour TTL


def store_session(session_id: str, data: dict) -> None:
    """Persist session data beyond the SSE stream lifecycle."""
    if not session_id or not data:
        return
    _PERSISTED[session_id] = {"data": data, "stored_at": time.time()}
    _log.debug("Persisted session %s for docx export (%d keys)", session_id, len(data))
    # Clean up expired sessions
    _evict_expired()


def _evict_expired() -> None:
    """Remove sessions older than _PERSISTED_MAX_AGE."""
    now = time.time()
    expired = [sid for sid, v in _PERSISTED.items()
               if now - v["stored_at"] > _PERSISTED_MAX_AGE]
    for sid in expired:
        _PERSISTED.pop(sid, None)


def _get_persisted(session_id: str) -> dict | None:
    """Retrieve persisted session data, or None if expired / not found."""
    _evict_expired()
    entry = _PERSISTED.get(session_id)
    if entry:
        return entry["data"]
    return None


def generate_docx(session_id: str, session_results: dict | None = None) -> bytes:
    """Generate a Word (.docx) report from session results.

    Checks the live in-memory dict first (for streaming), then falls
    back to the persistent store (for post-stream downloads).

    Returns a complete .docx file as bytes, or a minimal document
    with an error message if the session data is unavailable.
    """
    data = None
    if session_results:
        data = session_results.get(session_id)
    if not data:
        data = _get_persisted(session_id)
    if not data:
        return _minimal("No results found for this session. The analysis may still be running or the session has expired.")

    body = ET.Element(f"{{{_WML}}}body")
    _heading(body, "CapitalRadar Analysis Report", 1)
    _para(body, f"Ticker: {data.get('ticker','N/A')}    Date: {data.get('date','N/A')}    Rating: {data.get('rating','Hold')}", spacing_after=200)

    # ── Analyst Reports ──────────────────────────────────────────────
    from capitalradar.catalog import ANALYST_ORDER, ANALYST_ROLES
    reports = data.get("analyst_reports", {})
    titles = {key: ANALYST_ROLES[key].label for key in ANALYST_ORDER}
    if reports:
        _heading(body, "Analyst Reports", 2)
        for key in ANALYST_ORDER:
            r = reports.get(key, "")
            if r:
                _heading(body, titles.get(key, key), 3)
                _md_block(body, r)

    # ── Research Debate ──────────────────────────────────────────────
    rd = data.get("research_debate_rounds", [])
    if rd:
        _heading(body, "Research Debate", 2)
        sn = {"bull": "Bull Researcher", "bear": "Bear Researcher"}
        for r in rd:
            _para(body, f"{sn.get(r['side'], r['side'])} (Round {r['round']})", bold=True)
            _md_block(body, r.get("content", ""))
    rm = data.get("research_manager_decision", "")
    if rm:
        _heading(body, "Research Manager Decision", 3)
        _md_block(body, rm)

    # ── Trader Proposal ──────────────────────────────────────────────
    td = data.get("trader_proposal", "")
    if td:
        _heading(body, "Trader Proposal", 2)
        _md_block(body, td)

    # ── Risk Management Debate ───────────────────────────────────────
    rk = data.get("risk_debate_rounds", [])
    if rk:
        _heading(body, "Risk Management Debate", 2)
        rsn = {"aggressive": "Aggressive", "conservative": "Conservative", "neutral": "Neutral"}
        for r in rk:
            _para(body, f"{rsn.get(r['side'], r['side'])} Risk Analyst (Round {r['round']})", bold=True)
            _md_block(body, r.get("content", ""))
    rj = data.get("risk_judge_decision", "")
    if rj:
        _heading(body, "Risk Manager Decision", 3)
        _md_block(body, rj)

    # ── Final Decision ───────────────────────────────────────────────
    final = data.get("final_decision", "")
    if final:
        _heading(body, "Final Decision", 2)
        _md_block(body, final)

    doc_el = ET.Element(f"{{{_WML}}}document")
    doc_el.append(body)
    doc_xml = _XML + ET.tostring(doc_el, encoding="unicode")

    return _build_zip(doc_xml)


# ═══════════════════════════════════════════════════════════════════════
# OPC package assembly
# ═══════════════════════════════════════════════════════════════════════

def _build_zip(doc_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml
        ct = ET.Element("Types", xmlns=_CT)
        ET.SubElement(ct, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
        ET.SubElement(ct, "Default", Extension="xml", ContentType="application/xml")
        ET.SubElement(ct, "Override", PartName="/word/document.xml", ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
        ET.SubElement(ct, "Override", PartName="/word/styles.xml", ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml")
        zf.writestr("[Content_Types].xml", _XML + ET.tostring(ct, encoding="unicode"))
        # _rels/.rels
        rels = ET.Element("Relationships", xmlns=_REL)
        ET.SubElement(rels, "Relationship", Id="rId1", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", Target="word/document.xml")
        zf.writestr("_rels/.rels", _XML + ET.tostring(rels, encoding="unicode"))
        # word/_rels/document.xml.rels
        dr = ET.Element("Relationships", xmlns=_REL)
        ET.SubElement(dr, "Relationship", Id="rId1", Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles", Target="styles.xml")
        zf.writestr("word/_rels/document.xml.rels", _XML + ET.tostring(dr, encoding="unicode"))
        # word/styles.xml
        zf.writestr("word/styles.xml", _STYLES)
        # word/document.xml
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def generate_twopass_docx(results_json: str) -> bytes:
    """Generate a Word (.docx) report from two-pass analysis results JSON.

    The input JSON is an array of {code, name, price, change_pct, prevModel, prevVerdict, newModel, newVerdict}.
    """
    import json
    try:
        rows = json.loads(results_json)
    except (json.JSONDecodeError, TypeError):
        return _minimal("Invalid analysis data.")

    if not isinstance(rows, list) or not rows:
        return _minimal("No analysis results to export.")

    body = ET.Element(f"{{{_WML}}}body")
    _heading(body, "🔬 二次分析报告", 1)
    _para(body, f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}    共 {len(rows)} 只股票", spacing_after=200)

    # Summary
    bull_count = sum(1 for r in rows if r.get("newVerdict") == "看多")
    bear_count = sum(1 for r in rows if r.get("newVerdict") == "看空")
    hold_count = sum(1 for r in rows if r.get("newVerdict") not in ("看多", "看空"))
    _heading(body, "汇总", 2)
    _para(body, f"本次看多: {bull_count} 只 | 看空: {bear_count} 只 | 观望: {hold_count} 只", spacing_after=120)

    # Detail table
    _heading(body, "详细结果", 2)
    table_lines = ["| 代码 | 名称 | 最新价 | 涨跌幅 | 前次模型 | 前次结论 | 本次模型 | 本次结论 |"]
    table_lines.append("|------|------|--------|--------|----------|----------|----------|----------|")
    for r in rows:
        code = r.get("code", "?")
        name = r.get("name", "")
        price = f"{r['price']:.2f}" if r.get("price") else "-"
        chg = r.get("change_pct")
        chg_str = f"{chg:+.2f}%" if chg is not None else "-"
        pm = r.get("prevModel", "?")
        pv = r.get("prevVerdict", "看多")
        nm = r.get("newModel", "?")
        nv = r.get("newVerdict", "?")
        table_lines.append(f"| {code} | {name} | {price} | {chg_str} | {pm} | {pv} | {nm} | {nv} |")
    _build_table(body, table_lines)

    doc_el = ET.Element(f"{{{_WML}}}document")
    doc_el.append(body)
    doc_xml = _XML + ET.tostring(doc_el, encoding="unicode")
    return _build_zip(doc_xml)


def _minimal(msg: str) -> bytes:
    """Return a minimal .docx containing only an error message."""
    body = ET.Element(f"{{{_WML}}}body")
    _para(body, msg)
    doc_el = ET.Element(f"{{{_WML}}}document")
    doc_el.append(body)
    return _build_zip(_XML + ET.tostring(doc_el, encoding="unicode"))


# ═══════════════════════════════════════════════════════════════════════
# Block-level helpers
# ═══════════════════════════════════════════════════════════════════════

def _heading(body, text: str, level: int = 1):
    """Add a heading paragraph. Uses 黑体 (HeiTi) font, left-aligned."""
    sizes = {1: 36, 2: 28, 3: 24}  # half-points: 18pt, 14pt, 12pt
    p = ET.SubElement(body, f"{{{_WML}}}p")
    pr = ET.SubElement(p, f"{{{_WML}}}pPr")
    # Left-align headings (not justified)
    ET.SubElement(pr, f"{{{_WML}}}jc", **{f"{{{_WML}}}val": "left"})
    spacing_before = {1: "360", 2: "240", 3: "160"}.get(level, "120")
    ET.SubElement(pr, f"{{{_WML}}}spacing", before=spacing_before, after="80")
    r = ET.SubElement(p, f"{{{_WML}}}r")
    rp = ET.SubElement(r, f"{{{_WML}}}rPr")
    ET.SubElement(rp, f"{{{_WML}}}rFonts", **{f"{{{_WML}}}ascii": "黑体", f"{{{_WML}}}hAnsi": "黑体", f"{{{_WML}}}eastAsia": "黑体"})
    ET.SubElement(rp, f"{{{_WML}}}b")
    ET.SubElement(rp, f"{{{_WML}}}sz", **{f"{{{_WML}}}val": str(sizes.get(level, 24))})
    ET.SubElement(r, f"{{{_WML}}}t").text = text


def _para(body, text: str, bold: bool = False, sz: int = 24,
          spacing_after: int = 40, spacing_before: int = 0):
    """Add a body paragraph. Uses 仿宋 (FangSong) font, justified alignment."""
    p = ET.SubElement(body, f"{{{_WML}}}p")
    pr = ET.SubElement(p, f"{{{_WML}}}pPr")
    # Justified alignment (两端对齐) for all body text
    ET.SubElement(pr, f"{{{_WML}}}jc", **{f"{{{_WML}}}val": "both"})
    ET.SubElement(pr, f"{{{_WML}}}spacing",
                  before=str(spacing_before), after=str(spacing_after),
                  line="240", lineRule="auto")
    r = ET.SubElement(p, f"{{{_WML}}}r")
    rp = ET.SubElement(r, f"{{{_WML}}}rPr")
    ET.SubElement(rp, f"{{{_WML}}}rFonts", **{f"{{{_WML}}}ascii": "仿宋", f"{{{_WML}}}hAnsi": "仿宋", f"{{{_WML}}}eastAsia": "仿宋"})
    if bold:
        ET.SubElement(rp, f"{{{_WML}}}b")
    ET.SubElement(rp, f"{{{_WML}}}sz", **{f"{{{_WML}}}val": str(sz)})
    ET.SubElement(r, f"{{{_WML}}}t").text = text


# ═══════════════════════════════════════════════════════════════════════
# Markdown → Word conversion
# ═══════════════════════════════════════════════════════════════════════

def _md_block(body, md_text: str):
    """Convert a markdown string into Word paragraphs and tables.

    Handles: headings, bold/italic, bullet lists, numbered lists,
    tables, horizontal rules, blockquotes, code blocks, and links.
    """
    if not md_text:
        return

    lines = md_text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            _para(body, "", sz=20, spacing_after=40)
            i += 1
            continue

        # ── Markdown table ───────────────────────────────────────
        if line.startswith("|") and "|" in line[1:]:
            table_lines = [line]
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                table_lines.append(lines[j].strip())
                j += 1
            _build_table(body, table_lines)
            i = j
            continue

        # ── Headings ─────────────────────────────────────────────
        if line.startswith("#### "):
            _heading(body, _clean(line[5:]), 3)
        elif line.startswith("### "):
            _heading(body, _clean(line[4:]), 3)
        elif line.startswith("## "):
            _heading(body, _clean(line[3:]), 2)
        elif line.startswith("# "):
            _heading(body, _clean(line[2:]), 1)

        # ── Horizontal rule ──────────────────────────────────────
        elif line in ("---", "***", "___", "* * *", "- - -"):
            p = ET.SubElement(body, f"{{{_WML}}}p")
            pr = ET.SubElement(p, f"{{{_WML}}}pPr")
            bdr = ET.SubElement(pr, f"{{{_WML}}}pBdr")
            ET.SubElement(bdr, f"{{{_WML}}}bottom", val="single", sz="4", space="1", color="BBBBBB")
            ET.SubElement(pr, f"{{{_WML}}}spacing", before="100", after="100")

        # ── Bullet list ──────────────────────────────────────────
        elif line.startswith("- ") or line.startswith("* "):
            _para(body, "•  " + _clean(line[2:]), sz=24, spacing_after=60)

        # ── Numbered list ────────────────────────────────────────
        elif re.match(r"^\d+[.)]\s", line):
            content = re.sub(r"^\d+[.)]\s", "", line)
            _para(body, " " + _clean(content), sz=24, spacing_after=60)

        # ── Code block fence (skip the fence markers) ────────────
        elif line.startswith("```"):
            i += 1
            # Collect code block content (plain text, no syntax highlighting)
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if code_lines:
                for cl in code_lines:
                    _para(body, cl, sz=20, spacing_after=20)
            continue

        # ── Blockquote ───────────────────────────────────────────
        elif line.startswith("> "):
            _para(body, _clean(line[2:]), sz=24, spacing_after=80)

        # ── Normal paragraph ─────────────────────────────────────
        else:
            _para(body, _clean(line), sz=24, spacing_after=80)

        i += 1


# ═══════════════════════════════════════════════════════════════════════
# Table builder
# ═══════════════════════════════════════════════════════════════════════

def _build_table(body, table_lines: list):
    """Build a Word table from markdown table lines."""
    rows = []
    for tl in table_lines:
        cells = [c.strip() for c in tl.strip("|").split("|")]
        rows.append(cells)

    # Filter separator rows (e.g. |---|---|)
    data_rows = [r for r in rows if not all(re.match(r'^[-: ]+$', c) for c in r)]
    if not data_rows:
        return

    ncols = max(len(r) for r in data_rows)

    tbl = ET.SubElement(body, f"{{{_WML}}}tbl")
    tblPr = ET.SubElement(tbl, f"{{{_WML}}}tblPr")
    ET.SubElement(tblPr, f"{{{_WML}}}tblW", w="5000", type="pct")
    tblBorders = ET.SubElement(tblPr, f"{{{_WML}}}tblBorders")
    for bn in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        ET.SubElement(tblBorders, f"{{{_WML}}}{bn}", val="single", sz="4", space="0", color="999999")

    for ri, row in enumerate(data_rows):
        tr = ET.SubElement(tbl, f"{{{_WML}}}tr")
        is_header = (ri == 0)
        for ci in range(ncols):
            cell_text = row[ci] if ci < len(row) else ""
            cell_text = _clean(cell_text)
            tc = ET.SubElement(tr, f"{{{_WML}}}tc")
            tcPr = ET.SubElement(tc, f"{{{_WML}}}tcPr")
            ET.SubElement(tcPr, f"{{{_WML}}}tcW", w=str(5000 // ncols), type="dxa")
            if is_header:
                ET.SubElement(tcPr, f"{{{_WML}}}shd", fill="E8E8E8", val="clear")
            p = ET.SubElement(tc, f"{{{_WML}}}p")
            pr = ET.SubElement(p, f"{{{_WML}}}pPr")
            ET.SubElement(pr, f"{{{_WML}}}jc", **{f"{{{_WML}}}val": "left"})
            r = ET.SubElement(p, f"{{{_WML}}}r")
            rp = ET.SubElement(r, f"{{{_WML}}}rPr")
            ET.SubElement(rp, f"{{{_WML}}}rFonts", **{f"{{{_WML}}}ascii": "仿宋", f"{{{_WML}}}hAnsi": "仿宋", f"{{{_WML}}}eastAsia": "仿宋"})
            if is_header:
                ET.SubElement(rp, f"{{{_WML}}}b")
            ET.SubElement(rp, f"{{{_WML}}}sz", **{f"{{{_WML}}}val": "22"})
            ET.SubElement(r, f"{{{_WML}}}t").text = cell_text

    # Blank line after table
    _para(body, "", sz=20, spacing_after=80)


# ═══════════════════════════════════════════════════════════════════════
# Text cleaning — strip markdown syntax for plain Word output
# ═══════════════════════════════════════════════════════════════════════

def _clean(text: str) -> str:
    """Remove all markdown formatting tokens from a line of text.

    Order matters — process outermost/longest patterns first.
    """
    if not text:
        return text

    # 1. Remove images: ![alt](url)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)

    # 2. Convert links to just the link text: [text](url) → text
    text = re.sub(r'\[([^\]]*?)\]\(.*?\)', r'\1', text)

    # 3. Remove horizontal-rule markers (standalone ***, ---, ___)
    if re.match(r'^[\*\-_]{3,}\s*$', text):
        return ''

    # 4. Bold + italic (***text*** or ___text___)
    #    Must be adjacent to non-whitespace per CommonMark spec.
    text = re.sub(r'\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*', r'\1', text)
    text = re.sub(r'___(?!\s)(.+?)(?<!\s)___', r'\1', text)

    # 5. Bold (**text** or __text__)
    text = re.sub(r'\*\*(?!\s)(.+?)(?<!\s)\*\*', r'\1', text)
    text = re.sub(r'__(?!\s)(.+?)(?<!\s)__', r'\1', text)

    # 6. Italic (*text* or _text_) — NOT bullet markers, NOT " * text * "
    #    Opening marker must be followed by non-whitespace;
    #    closing marker must be preceded by non-whitespace.
    text = re.sub(r'(?<!\*)\*(?!\s)([^*]+)(?<!\s)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<!_)_(?!\s)([^_]+)(?<!\s)_(?!_)', r'\1', text)

    # 7. Inline code: `code` → code
    text = re.sub(r'`([^`]+)`', r'\1', text)

    # 8. Strikethrough: ~~text~~ → text
    text = re.sub(r'~~(.+?)~~', r'\1', text)

    # 9. Remove orphaned markdown markers only at start/end of text.
    #    Only strip double/triple markers (**  and  ***) which are clearly
    #    formatting residue — single * can be intentional punctuation.
    text = re.sub(r'^\*{2,3}\s+', '', text)    # leading ** or ***
    text = re.sub(r'\s+\*{2,3}$', '', text)    # trailing ** or ***
    text = re.sub(r'^_{1,3}\s+', '', text)     # leading _, __, ___
    text = re.sub(r'\s+_{1,3}$', '', text)     # trailing _, __, ___
    text = re.sub(r'^~{2}\s+', '', text)       # leading ~~
    text = re.sub(r'\s+~{2}$', '', text)       # trailing ~~

    return text.strip()
