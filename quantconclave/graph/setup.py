# quantconclave/graph/setup.py

from typing import Any, Dict
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from quantconclave.agents import *
from quantconclave.agents.utils.agent_states import AgentState
from quantconclave.agents.utils.pm_tools import predict_stock_price

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic
from .parallel_analyst_runner import create_parallel_analyst_runner
from .adjudicator import create_adjudicator_node


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: Dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        analyst_concurrency_limit: int = 1,
        on_progress: callable = None,
        config: dict = None,
    ):
        """Initialize with required components."""
        self._on_progress = on_progress
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.analyst_concurrency_limit = analyst_concurrency_limit
        self._adjudicator_node = create_adjudicator_node(config) if config and config.get("adjudication_rules",{}).get("enabled") else None

    def setup_graph(
        self, selected_analysts=["capital_flow", "market", "social", "news", "fundamentals", "competitor", "partner"]
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "capital_flow": Capital Flow analyst (anchor — runs first)
                - "market": Market analyst
                - "social": Sentiment analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
                - "competitor": Competitor analyst
                - "partner": Partner/Supply Chain analyst
        """
        plan = build_analyst_execution_plan(
            selected_analysts,
            concurrency_limit=self.analyst_concurrency_limit,
        )

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create PM tool node
        pm_tool_node = ToolNode([predict_stock_price])

        # Create workflow
        workflow = StateGraph(AgentState)

        # --- Phase 1: Capital Flow analyst (sequential anchor) ---
        # Capital Flow runs first as a sequential LangGraph node with tool loop
        workflow.add_node("Capital Flow Analyst", create_capital_flow_analyst(self.quick_thinking_llm))
        workflow.add_node("Msg Clear Capital Flow", create_msg_delete())
        workflow.add_node("tools_capital_flow", self.tool_nodes.get("capital_flow", ToolNode([])))

        workflow.add_conditional_edges(
            "Capital Flow Analyst",
            self.conditional_logic.should_continue_capital_flow,
            ["tools_capital_flow", "Msg Clear Capital Flow"],
        )
        workflow.add_edge("tools_capital_flow", "Capital Flow Analyst")

        # --- Phase 2: Parallel analysts (Market, Sentiment, News, Fundamentals, Competitor, Partner) ---
        non_anchor = [k for k in selected_analysts if k != "capital_flow"]
        if non_anchor:
            parallel_runner = create_parallel_analyst_runner(
                self.quick_thinking_llm,
                selected_analysts=non_anchor,
                on_progress=getattr(self, "_on_progress", None),
            )
            workflow.add_node("Parallel Analyst Runner", parallel_runner)
            workflow.add_edge("Msg Clear Capital Flow", "Parallel Analyst Runner")
            if self._adjudicator_node:
                workflow.add_node("Adjudicator", self._adjudicator_node)
                workflow.add_edge("Parallel Analyst Runner", "Adjudicator")
                workflow.add_edge("Adjudicator", "Bull Researcher")
            else:
                workflow.add_edge("Parallel Analyst Runner", "Bull Researcher")
        else:
            # Only Capital Flow selected — skip to debate
            if self._adjudicator_node:
                workflow.add_node("Adjudicator", self._adjudicator_node)
                workflow.add_edge("Msg Clear Capital Flow", "Adjudicator")
                workflow.add_edge("Adjudicator", "Bull Researcher")
            else:
                workflow.add_edge("Msg Clear Capital Flow", "Bull Researcher")

        # --- Phase 3: Research Debate ---
        workflow.add_node("Bull Researcher", bull_researcher_node)
        workflow.add_node("Bear Researcher", bear_researcher_node)
        workflow.add_node("Research Manager", research_manager_node)

        workflow.add_conditional_edges(
            "Bull Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_conditional_edges(
            "Bear Researcher",
            self.conditional_logic.should_continue_debate,
            {
                "Bull Researcher": "Bull Researcher",
                "Research Manager": "Research Manager",
            },
        )
        workflow.add_edge("Research Manager", "Trader")

        # --- Phase 4: Trader ---
        workflow.add_node("Trader", trader_node)
        workflow.add_edge("Trader", "Aggressive Analyst")

        # --- Phase 5: Risk Debate ---
        workflow.add_node("Aggressive Analyst", aggressive_analyst)
        workflow.add_node("Neutral Analyst", neutral_analyst)
        workflow.add_node("Conservative Analyst", conservative_analyst)
        workflow.add_node("Portfolio Manager", portfolio_manager_node)
        workflow.add_node("tools_portfolio_manager", pm_tool_node)

        workflow.add_conditional_edges(
            "Aggressive Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Conservative Analyst": "Conservative Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Conservative Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Neutral Analyst": "Neutral Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )
        workflow.add_conditional_edges(
            "Neutral Analyst",
            self.conditional_logic.should_continue_risk_analysis,
            {
                "Aggressive Analyst": "Aggressive Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        )

        workflow.add_conditional_edges(
            "Portfolio Manager",
            self.conditional_logic.should_continue_pm,
            {
                "tools_portfolio_manager": "tools_portfolio_manager",
                "END": END,
            },
        )
        workflow.add_edge("tools_portfolio_manager", "Portfolio Manager")

        # --- START edge ---
        workflow.add_edge(START, "Capital Flow Analyst")

        return workflow

    def setup_partial_graph(self, aspects):
        """Build a partial graph with only the requested analysts.

        No debate, trader, or risk stages. Directly returns analyst reports.
        aspects: list of analyst keys e.g. ["capital_flow","market","fundamentals"]
        """
        from langgraph.graph import END, START, StateGraph
        from quantconclave.agents.utils.agent_states import AgentState
        from quantconclave.agents import (
            create_capital_flow_analyst, create_market_analyst,
            create_sentiment_analyst, create_news_analyst,
            create_fundamentals_analyst, create_competitor_analyst,
            create_partner_analyst, create_msg_delete,
        )

        analyst_factories = {
            "capital_flow": create_capital_flow_analyst,
            "market": create_market_analyst,
            "social": create_sentiment_analyst,
            "news": create_news_analyst,
            "fundamentals": create_fundamentals_analyst,
            "competitor": create_competitor_analyst,
            "partner": create_partner_analyst,
        }

        workflow = StateGraph(AgentState)
        valid_aspects = [k for k in aspects if k in analyst_factories]
        if not valid_aspects:
            workflow.add_edge(START, END)
            return workflow

        from quantconclave.catalog import ANALYST_ROLES

        for i, key in enumerate(valid_aspects):
            role = ANALYST_ROLES[key]
            workflow.add_node(role.node, analyst_factories[key](self.quick_thinking_llm))
            if key in self.tool_nodes:
                workflow.add_node(role.tool_node, self.tool_nodes[key])

        # Wire START -> first analyst -> next analyst -> ... -> END
        workflow.add_edge(START, ANALYST_ROLES[valid_aspects[0]].node)
        for i in range(len(valid_aspects) - 1):
            cur = ANALYST_ROLES[valid_aspects[i]].node
            nxt = ANALYST_ROLES[valid_aspects[i + 1]].node
            workflow.add_edge(cur, nxt)
        workflow.add_edge(ANALYST_ROLES[valid_aspects[-1]].node, END)

        return workflow
