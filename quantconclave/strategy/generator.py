"""LLM strategy code generator: natural language -> vectorized signal code.

The old generator produced backtrader ``bt.Strategy`` classes that were never
executable by the engine. This version generates a vectorized signal function
``custom_signal(df, **params) -> pd.Series`` that returns target positions,
which ``compile_signal`` turns into a callable the vectorized backtest engine
can execute directly.
"""

from __future__ import annotations

import ast
import logging
import re
from typing import Callable

from quantconclave.llm_clients import create_llm_client, resolve_role_llm
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

GENERATION_TEMPLATE = """You are a quantitative strategy code generator for vectorized backtesting.

Given a user description, output ONLY one valid Python function:

def custom_signal(df, **params) -> pd.Series:
    # df: single-symbol OHLCV DataFrame, ascending by date
    #     columns: open, high, low, close, volume
    # Returns: target position Series in {0.0, 1.0} (0 = flat, 1 = fully long)

Rules:
- Use ONLY numpy/pandas. You may `from quantconclave.quant.indicators import rsi, cci, ...`
- Vectorized operations only (df["close"].rolling(...).mean(), .ewm(), ...). NO for-loops over rows.
- Holding state machine: use this pattern for entry/exit signals:
      pos = pd.Series(np.nan, index=df.index)
      pos[entry_mask] = 1.0
      pos[exit_mask] = 0.0
      position = pos.ffill().fillna(0.0)
  where entry_mask/exit_mask are boolean pandas Series.
- Output ONLY the code block, no explanation.
- Must be parseable by Python's ast.parse().

Description: {description}"""


class StrategyGenerationError(Exception):
    pass


# Blacklist dangerous constructs in LLM-generated code.
_BLOCKED = (
    "import os",
    "import sys",
    "import subprocess",
    "import shutil",
    "__import__",
    "eval(",
    "exec(",
    "open(",
    "compile(",
    "input(",
    "breakpoint(",
)


def _validate_code(code: str) -> None:
    """Validate generated code: safe AST + defines custom_signal."""
    for blocked in _BLOCKED:
        if blocked in code:
            raise StrategyGenerationError(f"Blocked construct: {blocked!r}")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise StrategyGenerationError(f"Invalid Python: {e}")
    has_signal = any(
        isinstance(n, ast.FunctionDef) and n.name == "custom_signal"
        for n in ast.walk(tree)
    )
    if not has_signal:
        raise StrategyGenerationError("Must define a custom_signal(df, **params) function")
    return


def generate_strategy_code(description: str, config: dict) -> str:
    provider, model, _ = resolve_role_llm(config, "deep")
    client = create_llm_client(
        provider=provider, model=model,
        base_url=config.get("backend_url"), timeout=60,
    )
    llm = client.get_llm()
    response = llm.invoke([
        SystemMessage(content="You are a quantitative strategy code generator."),
        HumanMessage(content=GENERATION_TEMPLATE.replace("{description}", description)),
    ])
    code = response.content if hasattr(response, "content") else str(response)
    match = re.search(r'```(?:python)?\s*\n(.*?)\n```', code, re.DOTALL)
    if match:
        code = match.group(1).strip()
    _validate_code(code)
    return code


def compile_signal(code: str) -> Callable:
    """Compile generated vectorized-signal code into a callable.

    Executes in a restricted namespace pre-populated with numpy, pandas and the
    quant indicator library, and returns the ``custom_signal`` function.
    """
    _validate_code(code)
    import numpy as np
    import pandas as pd
    from quantconclave.quant import indicators

    namespace: dict = {
        "np": np,
        "pd": pd,
        "indicators": indicators,
    }
    exec(compile(code, "<strategy>", "exec"), namespace)
    fn = namespace.get("custom_signal")
    if fn is None or not callable(fn):
        raise StrategyGenerationError("custom_signal was not defined by the generated code")
    return fn
