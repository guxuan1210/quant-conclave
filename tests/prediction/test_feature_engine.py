"""Tests for FeatureEngine."""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
from quantconclave.prediction.feature_engine import FeatureEngine


MOCK_OHLCV_CSV = """Date,Open,High,Low,Close,Volume
2026-06-17,63.00,64.00,62.00,63.50,10000000
2026-06-18,63.50,66.50,63.00,66.14,15000000
2026-06-19,66.14,66.50,64.00,64.81,12000000
2026-06-20,64.81,65.50,63.00,63.20,9000000
2026-06-23,63.20,64.00,61.50,63.00,11000000
2026-06-24,63.00,63.50,61.00,61.80,14000000
"""

MOCK_MONEYFLOW_CSV = """ts_code,trade_date,net_amount,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount
601127.SH,20260617,-50000000,80000000,130000000,20000000,15000000
601127.SH,20260618,-30000000,90000000,120000000,18000000,20000000
601127.SH,20260619,-80000000,70000000,150000000,25000000,30000000
601127.SH,20260620,-20000000,85000000,105000000,22000000,18000000
601127.SH,20260623,-60000000,75000000,135000000,19000000,25000000
601127.SH,20260624,-40000000,80000000,120000000,21000000,22000000
"""


class TestFeatureEngine:
    @patch("quantconclave.prediction.feature_engine.route_to_vendor")
    def test_build_features_returns_dataframe(self, mock_route):
        """FeatureEngine should return a DataFrame with one row and correct columns."""
        mock_route.side_effect = lambda method, *args, **kwargs: {
            "get_stock_data": MOCK_OHLCV_CSV,
            "get_money_flow": MOCK_MONEYFLOW_CSV,
        }[method]

        engine = FeatureEngine()
        df = engine.build_features("601127.SH", "2026-06-24")

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1  # one row for the requested date

    @patch("quantconclave.prediction.feature_engine.route_to_vendor")
    def test_build_features_has_required_columns(self, mock_route):
        """Output must include price, volume, capital flow, and technical columns."""
        mock_route.side_effect = lambda method, *args, **kwargs: {
            "get_stock_data": MOCK_OHLCV_CSV,
            "get_money_flow": MOCK_MONEYFLOW_CSV,
        }[method]

        engine = FeatureEngine()
        df = engine.build_features("601127.SH", "2026-06-24")

        required = [
            "return_5d", "return_20d", "volatility_20d",
            "volume_ratio_5d", "volume_ratio_20d",
            "net_flow_5d", "net_flow_20d", "big_order_dir_5d",
            "rsi_14", "macd_hist", "price_vs_ma20_pct", "price_vs_ma50_pct",
            "atr_14", "bb_position",
        ]
        for col in required:
            assert col in df.columns, f"Missing column: {col}"

    @patch("quantconclave.prediction.feature_engine.FeatureEngine._fetch_moneyflow")
    @patch("quantconclave.prediction.feature_engine.route_to_vendor")
    def test_missing_data_returns_partial_features(self, mock_route, mock_flow):
        """Should return partial features with NaN for unavailable data."""
        mock_route.return_value = MOCK_OHLCV_CSV
        mock_flow.return_value = None  # moneyflow unavailable

        engine = FeatureEngine()
        df = engine.build_features("601127.SH", "2026-06-24")
        assert df is not None
        assert pd.isna(df["net_flow_5d"].iloc[0])
