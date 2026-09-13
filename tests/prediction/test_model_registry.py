"""Tests for ModelRegistry."""
import json
import tempfile
from pathlib import Path
import pytest
import xgboost as xgb
import numpy as np
from capitalradar.prediction.model_registry import ModelRegistry


class TestModelRegistry:
    @pytest.fixture
    def tmp_models_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    @pytest.fixture
    def dummy_model(self):
        """Train a minimal XGBoost model for testing."""
        X = np.random.randn(100, 18)  # 18 features = len(FEATURE_COLUMNS)
        y = (X[:, 0] > 0).astype(float)
        model = xgb.XGBClassifier(n_estimators=2, max_depth=2)
        model.fit(X, y)
        return model

    def test_save_and_load_model(self, tmp_models_dir, dummy_model):
        registry = ModelRegistry(models_dir=str(tmp_models_dir))
        registry.save_model(dummy_model, "direction_5d")
        assert (tmp_models_dir / "direction_5d.json").exists()

        loaded = registry.load_model("direction_5d")
        assert loaded is not None
        assert isinstance(loaded, xgb.XGBClassifier)

    def test_load_missing_model_returns_none(self, tmp_models_dir):
        registry = ModelRegistry(models_dir=str(tmp_models_dir))
        result = registry.load_model("direction_5d")
        assert result is None

    def test_metadata_is_persisted(self, tmp_models_dir, dummy_model):
        registry = ModelRegistry(models_dir=str(tmp_models_dir))
        registry.save_model(dummy_model, "direction_5d", metrics={"accuracy": 0.62})
        meta = registry.get_metadata()
        assert "direction_5d" in meta
        assert meta["direction_5d"]["metrics"]["accuracy"] == 0.62
        assert "training_date" in meta["direction_5d"]
