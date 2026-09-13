from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from quantconclave.agents.utils.core_stock_tools import (
    get_stock_data
)
from quantconclave.agents.utils.technical_indicators_tools import (
    get_indicators
)
from quantconclave.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from quantconclave.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news
)
from quantconclave.agents.utils.capital_flow_tools import (
    get_money_flow,
    get_macro_context,
    detect_ah_relationship,
    get_hk_stock_data,
    get_ah_premium,
    get_southbound_flow,
    get_broker_recommend,
    get_weekly_data,
    get_monthly_data,
    get_fund_holdings,
    get_hsgt_flow,
    get_market_flow,
    get_margin_trading,
    get_share_pledge,
    get_share_unlock,
    get_stock_buyback,
    get_holder_changes,
    get_institutional_holders,
    get_major_holders,
    get_analyst_recommendations,
    get_dragon_tiger_list,
)
from quantconclave.agents.utils.intraday_tools import (
    get_intraday_data,
    get_realtime_quote,
)
from quantconclave.agents.utils.social_sentiment_tools import (
    get_xueqiu_sentiment,
    get_guba_sentiment,
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from quantconclave.dataflows.config import get_config
    lang = get_config().get("output_language", "Chinese")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(ticker: str, asset_type: str = "stock") -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    instrument_label = "asset" if asset_type == "crypto" else "instrument"
    extra_hint = (
        " Treat it as a crypto asset rather than a company, and do not assume company fundamentals are available."
        if asset_type == "crypto"
        else ""
    )
    # Append active skills from the analyst-core skill system
    skill_section = ""
    try:
        from quantconclave.graph.skill_generator import get_active_skill_path
        import os
        sp = get_active_skill_path()
        if sp and os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as sf:
                sc = sf.read()
            rs = sc.find("## Experience-Derived Rules")
            cs = sc.find("## Core Principles")
            if rs >= 0 and cs > rs:
                skill_section = "\n\n---\n" + sc[rs:cs].strip()
    except Exception:
        pass
    return (
        f"The {instrument_label} to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`, `-USD`)."
        + extra_hint
        + skill_section
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
