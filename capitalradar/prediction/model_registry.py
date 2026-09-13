"""Model loading, saving, versioning, and fallback for PredictionAgent."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import xgboost as xgb

from capitalradar.prediction.feature_engine import FEATURE_COLUMNS

logger = logging.getLogger(__name__)

_MODEL_METADATA_FILE = "model_metadata.json"
_N_FEATURES = len(FEATURE_COLUMNS)


class ModelRegistry:
    """Load and save XGBoost models with metadata tracking.

    Models are stored as JSON files in ``models_dir`` (default:
    ``~/.capitalradar/models/``).  Metadata (training date, feature list,
    calibration curve, metrics) is persisted in ``model_metadata.json``.

    Usage::

        registry = ModelRegistry()
        model = registry.load_model("direction_5d")
        if model is None:
            # no trained model yet — caller should use fallback
            ...
    """

    def __init__(self, models_dir: str = ""):
        if models_dir:
            self._dir = Path(models_dir)
        else:
            from pathlib import Path as P
            self._dir = P.home() / ".capitalradar" / "models"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._metadata_path = self._dir / _MODEL_METADATA_FILE

    # ── Public API ──────────────────────────────────────────────────

    def load_model(self, name: str) -> xgb.XGBModel | None:
        """Load a trained model by name. Returns None if not found."""
        path = self._dir / f"{name}.json"
        if not path.exists():
            logger.info("Model %s not found at %s", name, path)
            return None

        try:
            if "direction" in name:
                model = xgb.XGBClassifier()
            else:
                model = xgb.XGBRegressor()
            model.load_model(str(path))
            # Validate feature count
            if hasattr(model, "n_features_in_"):
                expected = model.n_features_in_
                if expected != _N_FEATURES:
                    logger.warning(
                        "Model %s expects %d features but FeatureEngine produces %d",
                        name, expected, _N_FEATURES,
                    )
            logger.info("Loaded model %s", name)
            return model
        except Exception as e:
            logger.error("Failed to load model %s: %s", name, e)
            return None

    def load_calibrator(self, name: str):
        """Load an isotonic calibration curve. Returns None if not found."""
        import pickle
        path = self._dir / f"{name}.pkl"
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.error("Failed to load calibrator %s: %s", name, e)
            return None

    def save_model(
        self, model: xgb.XGBModel, name: str, metrics: dict | None = None,
    ) -> None:
        """Save a trained model and update metadata."""
        path = self._dir / f"{name}.json"
        model.save_model(str(path))
        self._update_metadata(name, metrics or {})
        logger.info("Saved model %s", name)

    def save_calibrator(self, calibrator, name: str) -> None:
        """Save an isotonic calibration curve."""
        import pickle
        path = self._dir / f"{name}.pkl"
        with open(path, "wb") as f:
            pickle.dump(calibrator, f)

    def get_metadata(self) -> dict:
        """Return the full metadata dict."""
        if not self._metadata_path.exists():
            return {}
        try:
            return json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def list_available_models(self) -> list[str]:
        """List model names that have saved files."""
        return sorted(
            p.stem for p in self._dir.glob("*.json")
            if p.name != _MODEL_METADATA_FILE
        )

    # ── Internal ────────────────────────────────────────────────────

    def _update_metadata(self, name: str, metrics: dict) -> None:
        meta = self.get_metadata()
        meta[name] = {
            "training_date": datetime.now(timezone.utc).isoformat(),
            "features": FEATURE_COLUMNS,
            "n_features": _N_FEATURES,
            "metrics": metrics,
        }
        self._metadata_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
        )
