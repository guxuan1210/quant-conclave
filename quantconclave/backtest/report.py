"""Performance metrics computation and report formatting."""


def compute_performance(results: dict) -> str:
    r = results.get("results", {})
    lines = [
        "## Backtest Report\n",
        f"**Annual Return**: {r.get('annual_return', 0):.1f}%",
        f"**Sharpe Ratio**: {r.get('sharpe', 0):.2f}",
        f"**Max Drawdown**: {r.get('max_drawdown_pct', 0):.1f}%",
        f"**Total Trades**: {r.get('total_trades', 0)}",
        f"**Initial Capital**: ${r.get('initial_capital', 0):,.0f}",
        f"**Final Value**: ${r.get('final_value', 0):,.0f}",
        f"**Total Return**: {r.get('total_return_pct', 0):.1f}%",
        "",
    ]
    return "\n".join(lines)


def render_equity_chart_data(equity_curve: list) -> list:
    return [{"date": p["date"], "value": p["value"]} for p in equity_curve]
