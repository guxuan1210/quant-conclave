"""Backtrader strategy template definitions."""

TEMPLATES = [
    {
        "id": "ma_cross",
        "name": "MA Cross",
        "description": "Fast MA crosses above slow MA = buy, crosses below = sell",
        "strategy_class": "MACrossStrategy",
        "parameters": [
            {"key": "fast_period", "label": "Fast Period", "type": "int", "default": 5, "min": 2, "max": 50},
            {"key": "slow_period", "label": "Slow Period", "type": "int", "default": 20, "min": 10, "max": 200},
        ],
    },
    {
        "id": "macd",
        "name": "MACD Cross",
        "description": "MACD line crosses above signal = buy, below = sell",
        "strategy_class": "MACDStrategy",
        "parameters": [
            {"key": "fast", "label": "Fast MA", "type": "int", "default": 12, "min": 5, "max": 30},
            {"key": "slow", "label": "Slow MA", "type": "int", "default": 26, "min": 15, "max": 60},
            {"key": "signal", "label": "Signal MA", "type": "int", "default": 9, "min": 5, "max": 20},
        ],
    },
    {
        "id": "rsi",
        "name": "RSI Overbought/Oversold",
        "description": "Buy when RSI below oversold, sell when above overbought",
        "strategy_class": "RSIStrategy",
        "parameters": [
            {"key": "period", "label": "RSI Period", "type": "int", "default": 14, "min": 5, "max": 30},
            {"key": "oversold", "label": "Oversold", "type": "int", "default": 30, "min": 10, "max": 45},
            {"key": "overbought", "label": "Overbought", "type": "int", "default": 70, "min": 55, "max": 90},
        ],
    },
    {
        "id": "bollinger",
        "name": "Bollinger Bands",
        "description": "Buy when price touches lower band, sell at upper band",
        "strategy_class": "BollingerStrategy",
        "parameters": [
            {"key": "period", "label": "Period", "type": "int", "default": 20, "min": 5, "max": 50},
            {"key": "std_dev", "label": "Std Dev", "type": "float", "default": 2.0, "min": 1.0, "max": 4.0},
        ],
    },
    {
        "id": "turtle",
        "name": "Turtle Trading",
        "description": "Buy at N-day high breakout, sell at N-day low or ATR stop",
        "strategy_class": "TurtleStrategy",
        "parameters": [
            {"key": "entry_period", "label": "Entry Period", "type": "int", "default": 20, "min": 10, "max": 60},
            {"key": "stop_atr", "label": "Stop ATR", "type": "float", "default": 2.0, "min": 0.5, "max": 5.0},
        ],
    },
    {
        "id": "ma_arrange",
        "name": "MA Bullish Arrangement",
        "description": "Buy when short > mid > long MA, sell when arrangement breaks",
        "strategy_class": "MAArrangeStrategy",
        "parameters": [
            {"key": "short_ma", "label": "Short MA", "type": "int", "default": 5, "min": 2, "max": 20},
            {"key": "mid_ma", "label": "Mid MA", "type": "int", "default": 20, "min": 10, "max": 60},
            {"key": "long_ma", "label": "Long MA", "type": "int", "default": 60, "min": 30, "max": 200},
        ],
    },
    {
        "id": "volume_breakout",
        "name": "Volume Breakout",
        "description": "Buy when volume exceeds N-day avg by M multiple AND price hits new high",
        "strategy_class": "VolumeBreakoutStrategy",
        "parameters": [
            {"key": "volume_mult", "label": "Volume Multiplier", "type": "float", "default": 1.5, "min": 1.0, "max": 5.0},
            {"key": "price_period", "label": "Price Period", "type": "int", "default": 20, "min": 5, "max": 60},
        ],
    },
]


def get_template(template_id: str) -> dict | None:
    return next((t for t in TEMPLATES if t["id"] == template_id), None)


def list_templates() -> list[dict]:
    return TEMPLATES
