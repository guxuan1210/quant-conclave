"""Offline trainer for PredictionAgent XGBoost models.

Usage::

    python -m quantconclave.prediction.trainer  # trains on all configured tickers
    python -m quantconclave.prediction.trainer --ticker 000001.SZ  # single ticker
    python -m quantconclave.prediction.trainer --list-only  # list available models
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from argparse import ArgumentParser
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import brier_score_loss, accuracy_score

from quantconclave.prediction.feature_engine import FEATURE_COLUMNS
from quantconclave.prediction.model_registry import ModelRegistry
from quantconclave.prediction.data_pipeline import build_train_samples
from quantconclave.dataflows.config import get_config

logger = logging.getLogger(__name__)

# Default A-share tickers for initial training — balanced 50 across sectors
_DEFAULT_TICKERS = [
    # 金融
    "000001.SZ", "600030.SH", "600036.SH", "601166.SH", "601318.SH", "601398.SH",
    # 消费
    "000568.SZ", "000651.SZ", "000858.SZ", "002304.SZ", "002415.SZ", "002594.SZ",
    "002714.SZ", "600519.SH", "600809.SH", "600887.SH", "601888.SH",
    # 科技
    "002230.SZ", "002371.SZ", "002475.SZ", "002938.SZ", "300015.SZ", "300059.SZ",
    "300124.SZ", "300274.SZ", "300308.SZ", "300502.SZ", "300750.SZ",
    "600276.SH", "600406.SH", "600941.SH", "688008.SH", "688111.SH", "688256.SH", "688981.SH",
    # 制造
    "000333.SZ", "000725.SZ", "002050.SZ", "002129.SZ", "002352.SZ", "002459.SZ",
    "002601.SZ", "002812.SZ", "600031.SH", "600585.SH", "600690.SH", "601012.SH", "601899.SH",
    # 能源/公用
    "600028.SH", "600900.SH", "601088.SH", "601857.SH",
    # 医药
    "300122.SZ", "300347.SZ", "300760.SZ", "600085.SH", "600196.SH",
]

# Model training parameters
_XGB_PARAMS = {
    "n_estimators": 200,
    "max_depth": 5,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "random_state": 42,
    "verbosity": 0,
}


def train_all(
    tickers: list[str] | None = None,
    start_date: str = "2019-01-01",
    end_date: str | None = None,
    save: bool = True,
) -> dict:
    """Collect data from all tickers, train models, and save.

    Returns a dict with keys: direction_5d, direction_20d, price_5d_q10,
    price_5d_q50, price_5d_q90, price_20d_q10, price_20d_q50, price_20d_q90,
    calibrator_5d, calibrator_20d — each mapping to its trained model.
    """
    if tickers is None:
        tickers = _DEFAULT_TICKERS

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # ── Collect training data ──
    logger.info("Collecting training data for %d tickers: %s → %s",
                len(tickers), start_date, end_date)
    all_samples = []
    failed = 0
    for i, ticker in enumerate(tickers):
        logger.info("[%d/%d] %s ...", i + 1, len(tickers), ticker)
        try:
            df = build_train_samples(ticker, start_date, end_date)
            if df is not None and len(df) >= 100:
                df["ticker"] = ticker
                all_samples.append(df)
            else:
                failed += 1
        except Exception as e:
            logger.warning("Skipping %s: %s", ticker, e)
            failed += 1

    if not all_samples:
        raise RuntimeError(f"No training samples collected ({failed}/{len(tickers)} tickers failed)")

    train_df = pd.concat(all_samples, ignore_index=True)
    train_df = train_df.dropna(subset=[
        "target_5d_dir", "target_20d_dir", "target_5d_ret", "target_20d_ret",
    ])
    logger.info("Training set: %d rows, %d features, %d tickers, %d col-failures",
                len(train_df), len(FEATURE_COLUMNS), train_df["ticker"].nunique(), failed)

    X = train_df[FEATURE_COLUMNS].fillna(0).values.astype(np.float32)
    y_5d_dir = train_df["target_5d_dir"].values.astype(int)
    y_20d_dir = train_df["target_20d_dir"].values.astype(int)
    y_5d_ret = train_df["target_5d_ret"].values.astype(np.float32)
    y_20d_ret = train_df["target_20d_ret"].values.astype(np.float32)

    results = {}

    # ── Train direction classifiers ──
    for horizon, y in [("5d", y_5d_dir), ("20d", y_20d_dir)]:
        logger.info("Training direction_%s classifier (%d samples, imbalance=%.1f%%)",
                    horizon, len(y), 100.0 * y.mean())

        X_tr, X_val, y_tr, y_val = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y,
        )

        clf = xgb.XGBClassifier(**{**_XGB_PARAMS, "objective": "binary:logistic"})
        clf.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

        val_pred = clf.predict_proba(X_val)[:, 1]
        acc = accuracy_score(y_val, (val_pred > 0.5).astype(int))
        brier = brier_score_loss(y_val, val_pred)

        # Isotonic calibration on validation set
        cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        cal.fit(val_pred, y_val)
        cal_pred = cal.predict(val_pred)
        cal_brier = brier_score_loss(y_val, cal_pred)

        logger.info("  Val accuracy: %.4f, Brier: %.4f → %.4f (calibrated)", acc, brier, cal_brier)

        results[f"direction_{horizon}"] = clf
        results[f"calibrator_{horizon}"] = cal

        if save:
            registry = ModelRegistry()
            registry.save_model(clf, f"direction_{horizon}",
                               metrics={"accuracy": round(float(acc), 4),
                                        "brier_score_complement": round(float(1.0 - cal_brier), 4)})
            registry.save_calibrator(cal, f"calibrator_{horizon}")

    # ── Train price quantile regressors ──
    for horizon, y in [("5d", y_5d_ret), ("20d", y_20d_ret)]:
        for quantile in [0.10, 0.50, 0.90]:
            q_label = f"q{int(quantile*100)}"
            name = f"price_{horizon}_{q_label}"
            logger.info("Training %s (quantile=%.2f)", name, quantile)

            X_tr, X_val, y_tr, y_val = train_test_split(
                X, y, test_size=0.2, random_state=42,
            )

            reg = xgb.XGBRegressor(**{
                **_XGB_PARAMS, "objective": "reg:quantileerror",
                "quantile_alpha": quantile,
            })
            reg.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

            # Evaluate coverage
            val_preds = reg.predict(X_val)
            if quantile == 0.10:
                coverage = (y_val >= val_preds).mean()
            elif quantile == 0.90:
                coverage = (y_val <= val_preds).mean()
            else:
                coverage = np.nan
            mae = np.mean(np.abs(y_val - val_preds))
            logger.info("  Val MAE: %.4f, coverage: %.3f", mae, coverage)

            results[name] = reg

            if save:
                registry = ModelRegistry()
                registry.save_model(reg, name,
                                   metrics={"mae": round(float(mae), 4),
                                            "coverage": round(float(coverage), 4) if not np.isnan(coverage) else None})

    # Log final metadata
    if save:
        registry = ModelRegistry()
        meta = registry.get_metadata()
        meta["_last_training"] = {
            "date": datetime.now(timezone.utc).isoformat(),
            "n_samples": len(X),
            "n_tickers": train_df["ticker"].nunique(),
            "tickers": tickers,
            "features": FEATURE_COLUMNS,
        }
        registry._metadata_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return results


def list_models() -> list[str]:
    """Print and return available trained models."""
    registry = ModelRegistry()
    models = registry.list_available_models()
    meta = registry.get_metadata()
    last = meta.get("_last_training", {})
    print(f"Models directory: {registry._dir}")
    print(f"Last training: {last.get('date', 'never')}")
    if last:
        print(f"  Samples: {last.get('n_samples', '?')}")
        print(f"  Tickers: {last.get('n_tickers', '?')}")
    print(f"Available models ({len(models)}):")
    for m in models:
        print(f"  {m}")
    return models


# ── CLI entry point ───────────────────────────────────────────────────

def main():
    p = ArgumentParser(description="Train PredictionAgent XGBoost models")
    p.add_argument("--ticker", nargs="*", default=None,
                   help="Ticker(s) to train on (default: 20 A-share blue chips)")
    p.add_argument("--start", default="2019-01-01", help="Start date (default: 2019-01-01)")
    p.add_argument("--end", default=None, help="End date (default: today)")
    p.add_argument("--no-save", action="store_true", help="Don't save models (dry run)")
    p.add_argument("--list-only", action="store_true", help="List available models and exit")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.list_only:
        list_models()
        return

    tickers = args.ticker if args.ticker else _DEFAULT_TICKERS
    t0 = time.time()
    try:
        results = train_all(
            tickers=tickers, start_date=args.start,
            end_date=args.end, save=not args.no_save,
        )
        elapsed = time.time() - t0
        logger.info("Training complete in %.1f min. Models trained: %d",
                    elapsed / 60, len(results))
        list_models()
    except Exception as e:
        logger.exception("Training failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
