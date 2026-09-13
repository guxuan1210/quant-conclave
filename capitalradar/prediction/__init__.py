"""PredictionAgent — ML + LLM forecasting for CapitalRadar.

Public API:
    PredictionAgent.predict(ticker, trade_date, config) → PredictionReport
"""


def __getattr__(name):
    if name == "PredictionAgent":
        from capitalradar.prediction.agent import PredictionAgent
        return PredictionAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["PredictionAgent"]
