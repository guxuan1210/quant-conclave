"""Paper-trading investment-effect evaluation (前瞻纸面交易评测).

Core, web-independent logic for the fixed-sample weekly sampling, full-pipeline
prediction, single-model ablation, Nth-trading-day settlement, and the metrics /
validity gate. The web layer (``web/eval_store.py``, ``web/routes/evaluation.py``)
persists and exposes these primitives.
"""

from .config import get_eval_config

__all__ = ["get_eval_config"]
