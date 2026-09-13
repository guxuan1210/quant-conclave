"""History Agent: LLM-powered analysis of historical CapitalRadar analysis runs.

Analyzes past analysis records to extract investment experiences, lessons,
and patterns. Outputs are saved as experiences in the experience library
(pending_review) for user approval.
"""

from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Annotated, Optional

logger = logging.getLogger(__name__)


def build_history_agent_prompt(
    analysis_states: list[dict],
    config: dict,
    lang: str = "Chinese",
    question: str = "",
    experience_history: list[dict] = None,
) -> str:
    """Build a system prompt for the History Agent given analysis states.

    Args:
        analysis_states: List of loaded full analysis state dicts.
        config: System config.
        lang: Output language.
        question: Optional user question to guide the analysis.
        experience_history: Optional list of existing experiences for context.

    Returns:
        Full system prompt string.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    n = len(analysis_states)

    prompt = f"""You are the **CapitalRadar History Agent** — an AI analyst specializing in reviewing historical stock analysis results and extracting actionable investment experiences.

Current date: {today}
Number of analysis records provided: {n}

## Your Mission

You are reviewing past CapitalRadar deep analysis results. Your goal is to:
1. Analyze the quality of past decisions
2. Identify patterns, biases, and lessons
3. Extract structured experiences that can improve future analysis

"""

    # If user asked a specific question, guide the analysis
    if question:
        prompt += f"## User Question\n{question}\n\n"
        prompt += "Answer this question directly, then extract any relevant experiences.\n\n"

    # Experience history context
    if experience_history:
        prompt += "## Existing Experience Library (already extracted)\n"
        prompt += "These experiences have been previously extracted. Do NOT duplicate them:\n"
        for exp in experience_history[:20]:
            tag = exp.get("lesson_abstract", "")
            content = exp.get("content", "")[:300]
            prompt += f"- [{tag}]: {content}\n"
        prompt += "\n"

    # Build analysis summary
    prompt += "## Analysis Records\n\n"

    # Summary table
    prompt += "### Summary Timeline\n\n"
    prompt += "| # | Date | Ticker | Rating | Signal | 5-Day Outlook | 20-Day Outlook | Confidence |\n"
    prompt += "|---|---|---|---|---|---|---|---|\n"
    for i, state in enumerate(analysis_states, 1):
        date = state.get("trade_date", "N/A")
        ticker = state.get("ticker", state.get("company_of_interest", "N/A"))
        final = state.get("final_trade_decision", "")
        rating = "N/A"
        outlook_5d = "N/A"
        outlook_20d = "N/A"
        confidence = "N/A"
        signal = "N/A"
        import re
        r_match = re.search(r'\*\*Rating\*\*[:\s]*(\w+)', final)
        if r_match:
            rating = r_match.group(1)
        o5_match = re.search(r'\*\*5-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)', final)
        if o5_match:
            outlook_5d = o5_match.group(1).strip()[:60]
        o20_match = re.search(r'\*\*20-Day Outlook\*\*[:\s]*(.+?)(?:\n|$)', final)
        if o20_match:
            outlook_20d = o20_match.group(1).strip()[:60]
        conf_match = re.search(r'\*\*Confidence\*\*[:\s]*(\w+)', final)
        if conf_match:
            confidence = conf_match.group(1).strip()[:20]
        signal = state.get("signal_processed", rating)
        prompt += f"| {i} | {date} | {ticker} | {rating} | {signal} | {outlook_5d} | {outlook_20d} | {confidence} |\n"
    prompt += "\n"

    # Detailed analysis states
    max_detail = 2000 if n > 3 else 3500
    for i, state in enumerate(analysis_states, 1):
        ticker = state.get("ticker", state.get("company_of_interest", "N/A"))
        date = state.get("trade_date", "N/A")
        prompt += f"### Analysis #{i}: {ticker} on {date}\n\n"

        final = state.get("final_trade_decision", "")
        if final:
            prompt += f"**Final Decision:**\n{str(final)[:5000]}\n\n"

        # Key reports
        report_keys = [
            ("capital_flow_report", "Capital Flow Report"),
            ("market_report", "Market Report"),
            ("fundamentals_report", "Fundamentals Report"),
            ("sentiment_report", "Sentiment Report"),
            ("news_report", "News Report"),
        ]
        for key, label in report_keys:
            report = state.get(key, "")
            if report:
                prompt += f"**{label}:**\n{str(report)[:max_detail]}\n\n"

        # Trader plan
        trader = state.get("trader_investment_plan", "")
        if trader:
            prompt += f"**Trader Plan:**\n{str(trader)[:2000]}\n\n"

        # Investment debate
        debate = state.get("investment_debate_state", {})
        if debate:
            judge = debate.get("judge_decision", "")
            if judge:
                prompt += f"**Research Manager Decision:**\n{str(judge)[:2000]}\n\n"

    # Instructions
    prompt += """## Output Requirements

You MUST produce TWO outputs in your response:

### 1. Analysis & Answer
Provide a clear, structured analysis of the historical records. Address:
- **Overall Pattern**: Are the decisions consistent? What biases are visible?
- **Quality Assessment**: Which analysts/indicators were most useful?
- **Key Lessons**: What would you do differently?
- **Recommendations**: How can future analysis be improved?

### 2. Extractable Experiences (if applicable)

If you identify clear, reusable lessons, format each as:

```
---EXPERIENCE---
Category: pattern_type (e.g., sector_bias, ticker_streak, flow_signal, timing, risk_management)
Abstract: Short tag (e.g., "perma_bull_tech", "late_sell_601127")
Content: Detailed lesson that can be injected into future analysis prompts
Source Ticker: 601127.SH
Source Date: 2026-03-15
Outcome: missed_take_profit / correct_sell / false_alarm / ...
---END EXPERIENCE---
```

Only extract experiences that are:
1. Reusable (would help in future analyses)
2. Fact-based (backed by the data provided)
3. Non-obvious (not just "do your research")

### 3. Review & Finalize Experiences into Skill (if applicable)

After extracting experiences, you can help the user review them:
1. Call `list_pending_experiences` to show all pending experiences
2. Discuss with the user which ones to approve or reject
3. Call `approve_experience` or `reject_experience` based on user's decision
4. When user confirms, call `generate_analyst_skill` to create a new skill version
5. Tell the user which version was created and that they can activate it in the Skill Control panel

This is how the system learns and improves over time — user feedback becomes skills.

"""
    prompt += f"Write your response in {lang}.\n"
    return prompt


def parse_experiences_from_response(full_response: str) -> list[dict]:
    """Parse ---EXPERIENCE--- blocks from the LLM response."""
    import re
    experiences = []
    pattern = r'---EXPERIENCE---\s*(.*?)---END EXPERIENCE---'
    matches = re.findall(pattern, full_response, re.DOTALL)
    for block in matches:
        exp = {"category": "other"}
        for line in block.strip().split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "category":
                exp["category"] = value
            elif key == "abstract":
                exp["lesson_abstract"] = value
            elif key == "content":
                exp["content"] = value
            elif key == "source ticker":
                exp["source_ticker"] = value
            elif key == "source date":
                exp["source_date"] = value
            elif key == "outcome":
                exp["outcome"] = value
        if "content" in exp:
            experiences.append(exp)
    return experiences


def save_experiences_from_response(
    full_response: str,
    config: dict,
    run_ids: list[str] = None,
) -> list[dict]:
    """Parse LLM response and save extracted experiences to the library."""
    from capitalradar.advisory.experience_store import create_experience

    parsed = parse_experiences_from_response(full_response)
    saved = []
    for exp in parsed:
        try:
            eid = create_experience(
                content=exp.get("content", ""),
                source_ticker=exp.get("source_ticker", ""),
                source_date=exp.get("source_date", ""),
                outcome=exp.get("outcome", ""),
                category=exp.get("category", "other"),
                lesson_abstract=exp.get("lesson_abstract", ""),
            )
            saved.append({**exp, "id": eid})
        except Exception as e:
            logger.error("Failed to save experience: %s", e)
    return saved


def build_history_agent_tools(config: dict):
    """Build tool set for the History Agent.

    The History Agent doesn't need price/trading tools — it analyzes past
    records. But it can benefit from:
    - Web search (to contextualize past analysis periods)
    - Market data (to check current status vs past predictions)
    """
    from langchain_core.tools import tool

    def _clean_ticker(ticker: str) -> str:
        """Normalize ticker text passed by the LLM before vendor lookup."""
        cleaned = (ticker or "").strip().upper()
        while cleaned and cleaned[0] in "$#：:，, ":
            cleaned = cleaned[1:].strip()
        return cleaned

    @tool
    def search_web_for_context(
        query: Annotated[str, "Web search query"],
        max_results: Annotated[int, "Max results"] = 5,
    ) -> str:
        """Search the web for context about a stock or market event mentioned
        in the historical analysis. Use this to verify if a prediction came true
        or understand what happened after the analysis date."""
        results = []
        try:
            from duckduckgo_search import DDGS
            for r in DDGS().text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", "")[:100],
                    "url": r.get("href", "")[:200],
                    "snippet": r.get("body", "")[:300],
                })
        except Exception:
            pass
        if not results:
            return "[Web search unavailable]"
        lines = [f"## Web Search: {query}", ""]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. **{r['title']}**")
            lines.append(f"   {r['snippet']}")
            lines.append(f"   {r['url']}")
            lines.append("")
        return "\n".join(lines)

    @tool
    def check_current_price(
        ticker: Annotated[str, "Stock ticker (e.g. 600519.SH)"],
    ) -> str:
        """Check the current price of a stock to evaluate how past predictions
        have performed. Compares analysis-time price vs current price."""
        from capitalradar.dataflows.interface import route_to_vendor
        clean_ticker = _clean_ticker(ticker)
        if not clean_ticker:
            return "No ticker provided."
        try:
            return route_to_vendor("get_realtime_quote", symbol=clean_ticker)
        except Exception as e:
            return f"Error checking price for {clean_ticker}: {e}"

    # ── Experience Review & Skill Generation Tools ──
    @tool
    def list_pending_experiences(
        category: str = "",
    ) -> str:
        """List all pending_review experiences with their IDs, categories, and abstracts.
        Use this to show the user what experiences are awaiting approval."""
        from capitalradar.advisory.experience_store import list_experiences
        try:
            pending = list_experiences(status="pending_review")
            if not pending:
                return "No pending experiences to review."
            lines = [f"## Pending Experiences ({len(pending)} total)", ""]
            # Group by category
            cats = {}
            for exp in pending:
                c = exp.get("category", "other")
                cats.setdefault(c, []).append(exp)
            for cat, items in sorted(cats.items()):
                lines.append(f"### {cat} ({len(items)})")
                for exp in items:
                    tag = exp.get("lesson_abstract", "") or "no tag"
                    content = str(exp.get("content", ""))[:200]
                    lines.append(f"  - **ID#{exp['id']}** [{tag}]: {content}")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"Error listing experiences: {e}"

    @tool
    def approve_experience(
        ids: str,
    ) -> str:
        """Approve one or more pending experiences by ID. Changes status to 'active'.
        ids: comma-separated list of experience IDs (e.g. '4,5,6')"""
        from capitalradar.advisory.experience_store import approve_experience
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
        if not id_list:
            return "No valid IDs provided."
        approved = 0
        for eid in id_list:
            if approve_experience(eid):
                approved += 1
        return f"Approved {approved}/{len(id_list)} experiences. 可在 Skill 面板点击 \"Generate Version\" 将它们纳入活跃 skill（自动去重、上限 30 条）。"

    @tool
    def reject_experience(
        ids: str,
    ) -> str:
        """Reject (archive) one or more pending experiences by ID.
        ids: comma-separated list of experience IDs (e.g. '4,5,6')"""
        from capitalradar.advisory.experience_store import archive_experience
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
        if not id_list:
            return "No valid IDs provided."
        rejected = 0
        for eid in id_list:
            if archive_experience(eid):
                rejected += 1
        return f"Archived (rejected) {rejected}/{len(id_list)} experiences."

    @tool
    def generate_analyst_skill() -> str:
        """Generate a new version of the CapitalRadar Analyst skill from
        all currently active (approved) experiences. Call this AFTER the user
        has reviewed and approved experiences. Returns version info."""
        from capitalradar.graph.skill_generator import generate_skill_version
        try:
            result = generate_skill_version({})
            return (
                f"✅ Skill v{result['version']} generated!\n"
                f"- Rules included: {result['rule_count']}\n"
                f"- Active experiences: {result['active_exp_count']}\n"
                f"- File: {result['file_path']}\n\n"
                f"Now go to the Analyst Skill Control panel in the web UI to activate this version."
            )
        except Exception as e:
            return f"Skill generation failed: {e}"

    return [
        search_web_for_context,
        check_current_price,
        list_pending_experiences,
        approve_experience,
        reject_experience,
        generate_analyst_skill,
    ]
