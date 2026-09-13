"""PredictionAgent — ML + LLM forecasting for QuantConclave.

Public API:
    PredictionAgent.predict(ticker, trade_date, config) → PredictionReport
"""


def __getattr__(name):
    if name == "PredictionAgent":
        from quantconclave.prediction.agent import PredictionAgent
        return PredictionAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["PredictionAgent"]
