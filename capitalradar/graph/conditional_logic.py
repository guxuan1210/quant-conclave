# capitalradar/graph/conditional_logic.py

from capitalradar.agents.utils.agent_states import AgentState


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1, max_analyst_tool_calls=12, max_pm_tool_calls=5):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
        self.max_analyst_tool_calls = max_analyst_tool_calls
        self.max_pm_tool_calls = max_pm_tool_calls

    def _count_tool_calls(self, state: AgentState) -> int:
        """Count how many times the current analyst has issued tool calls.

        Since messages are cleared between analysts via create_msg_delete(),
        this count is naturally per-analyst.  AIMessages with empty tool_calls
        (the final report message) are not counted.
        """
        count = 0
        for m in state["messages"]:
            if hasattr(m, "tool_calls") and m.tool_calls:
                count += 1
        return count

    def _exceeded_limit(self, state: AgentState) -> bool:
        """Return True when the current analyst has exceeded its tool-call budget."""
        return self._count_tool_calls(state) >= self.max_analyst_tool_calls

    def should_continue_capital_flow(self, state: AgentState):
        """Determine if capital flow analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and not self._exceeded_limit(state):
            return "tools_capital_flow"
        return "Msg Clear Capital Flow"

    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and not self._exceeded_limit(state):
            return "tools_market"
        return "Msg Clear Market"

    def should_continue_social(self, state: AgentState):
        """Determine if sentiment-analyst tool round should continue.

        Method name keeps the legacy ``social`` suffix to match the
        ``AnalystType.SOCIAL = "social"`` wire value (saved-config
        back-compat); the returned ``clear_node`` label uses the v0.2.5
        rename so it matches the node registered by the execution plan.
        """
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and not self._exceeded_limit(state):
            return "tools_social"
        return "Msg Clear Sentiment"

    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and not self._exceeded_limit(state):
            return "tools_news"
        return "Msg Clear News"

    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and not self._exceeded_limit(state):
            return "tools_fundamentals"
        return "Msg Clear Fundamentals"

    def should_continue_competitor(self, state: AgentState):
        """Determine if competitor analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and not self._exceeded_limit(state):
            return "tools_competitor"
        return "Msg Clear Competitor"

    def should_continue_partner(self, state: AgentState):
        """Determine if partner analysis should continue."""
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and not self._exceeded_limit(state):
            return "tools_partner"
        return "Msg Clear Partner"

    def should_continue_pm(self, state: AgentState) -> str:
        """Route PM to its tool node or to END.

        PM gets up to ``max_pm_tool_calls`` tool rounds then
        falls through to structured-output phase.
        """
        messages = state["messages"]
        last_message = messages[-1]
        if last_message.tool_calls and self._count_tool_calls(state) < self.max_pm_tool_calls:
            return "tools_portfolio_manager"
        return "END"

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""

        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Portfolio Manager"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"
