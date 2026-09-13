# quantconclave/graph/propagation.py

from typing import Dict, Any, List, Optional
from quantconclave.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from quantconclave.catalog import ANALYST_ROLES


class Propagator:
    """Handles state initialization and propagation through the graph."""

    def __init__(self, max_recur_limit=100):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self,
        ticker: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        checkpoint_1_enabled: bool = False,
        checkpoint_2_enabled: bool = False,
    ) -> Dict[str, Any]:
        """Create the initial state for the agent graph."""
        return {
            "messages": [("human", ticker)],
            "company_of_interest": ticker,
            "asset_type": asset_type,
            "trade_date": str(trade_date),
            "past_context": past_context,
            "human_feedback": "",
            "checkpoint_1_question": "",
            "checkpoint_2_question": "",
            "checkpoint_1_enabled": checkpoint_1_enabled,
            "checkpoint_2_enabled": checkpoint_2_enabled,
            "sector_rotation_data": None,
            "adjudication_flags": [],
            "adjudication_notes": {},
            "prediction_report": "",
            "skill_version": 0,
            "investment_debate_state": InvestDebateState(
                {
                    "bull_history": "",
                    "bear_history": "",
                    "history": "",
                    "current_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            "risk_debate_state": RiskDebateState(
                {
                    "aggressive_history": "",
                    "conservative_history": "",
                    "neutral_history": "",
                    "history": "",
                    "latest_speaker": "",
                    "current_aggressive_response": "",
                    "current_conservative_response": "",
                    "current_neutral_response": "",
                    "judge_decision": "",
                    "count": 0,
                }
            ),
            **{role.report_key: "" for role in ANALYST_ROLES.values()},
        }

    def get_graph_args(self, callbacks: Optional[List] = None) -> Dict[str, Any]:
        """Get arguments for the graph invocation.

        Args:
            callbacks: Optional list of callback handlers for tool execution tracking.
                       Note: LLM callbacks are handled separately via LLM constructor.
        """
        config = {"recursion_limit": self.max_recur_limit}
        if callbacks:
            config["callbacks"] = callbacks
        return {
            "stream_mode": "values",
            "config": config,
        }
