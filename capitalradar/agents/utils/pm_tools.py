"""Tool functions for the Portfolio Manager agent node."""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def predict_stock_price(
    ticker: str,
) -> str:
    """Run the PredictionAgent ML+LLM forecast for a specific stock.

    Returns 5-day and 20-day direction probabilities, price range intervals,
    institutional behavior phase classification, and actionable guidance.
    Use this to get ML prediction data before making the final decision."""
    from capitalradar.prediction import PredictionAgent
    from capitalradar.dataflows.config import get_config
    today = datetime.now().strftime("%Y-%m-%d")
    config = get_config()
    try:
        agent = PredictionAgent()
        report = agent.predict(ticker, today, config)
        return report.to_markdown()
    except Exception as e:
        logger.exception("PM predict_stock_price failed for %s", ticker)
        lang = config.get("output_language", "Chinese")
        is_zh = lang.lower().startswith("zh") or "中文" in lang or "chinese" in lang.lower()
        if is_zh:
            return f"# 预测错误\n{ ticker } 的 ML 预测失败：{e}"
        return f"# Prediction Error\nPrediction failed for {ticker}: {e}"
