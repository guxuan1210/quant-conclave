"""Tests for the vectorized LLM strategy generator + compile_signal."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capitalradar.strategy.generator import (
    StrategyGenerationError,
    compile_signal,
    generate_strategy_code,
    _validate_code,
)

GOOD_CODE = '''
def custom_signal(df, **params):
    import pandas as pd
    import numpy as np
    fast = df["close"].rolling(10).mean()
    slow = df["close"].rolling(30).mean()
    pos = pd.Series(np.nan, index=df.index)
    pos[fast > slow] = 1.0
    pos[fast < slow] = 0.0
    return pos.ffill().fillna(0.0)
'''


def test_compile_signal_returns_callable():
    fn = compile_signal(GOOD_CODE)
    assert callable(fn)
    assert fn.__name__ == "custom_signal"


def test_compiled_signal_outputs_binary_series():
    fn = compile_signal(GOOD_CODE)
    np.random.seed(0)
    n = 100
    close = 100 * np.exp(np.cumsum(np.random.randn(n) * 0.01))
    df = pd.DataFrame({"close": close, "high": close * 1.01, "low": close * 0.99,
                       "volume": np.ones(n)})
    out = fn(df)
    assert isinstance(out, pd.Series)
    assert set(out.unique()).issubset({0.0, 1.0})


@pytest.mark.parametrize("malicious", [
    'import os\n',
    'import sys\n',
    'eval("1+1")\n',
    'exec("x=1")\n',
    'open("/etc/passwd")\n',
    'import subprocess\n',
])
def test_malicious_code_rejected(malicious):
    code = "def custom_signal(df, **params):\n    return df['close'] * 0\n" + malicious
    with pytest.raises(StrategyGenerationError):
        compile_signal(code)


def test_syntax_error_rejected():
    with pytest.raises(StrategyGenerationError):
        compile_signal("def custom_signal(: )  # bad syntax")


def test_missing_custom_signal_rejected():
    with pytest.raises(StrategyGenerationError):
        _validate_code("def other_function(df):\n    return df")


def test_generate_strategy_code_uses_llm(mock_llm_client):
    """generate_strategy_code delegates to create_llm_client and returns code."""
    from capitalradar.strategy import generator as g
    # Mock the LLM response to return a code block
    mock_llm = mock_llm_client.get_llm.return_value
    mock_llm.invoke.return_value = type("R", (), {"content": f"```python\n{GOOD_CODE}\n```"})()

    config = {"llm_provider": "deepseek", "deep_think_llm": "deepseek-v4-flash", "backend_url": None}
    code = g.generate_strategy_code("mean reversion", config)
    assert "def custom_signal" in code

    # The compiled function must work end-to-end
    fn = g.compile_signal(code)
    assert callable(fn)
