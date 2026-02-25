"""
In-process sepsis prediction using joblib artifacts (sepsis_rf_pipeline.joblib, feature_cols.joblib).
Loaded at Django startup via patients.apps.PatientsConfig.ready().
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Set by load_model() at startup
model = None
feature_cols: Optional[List[str]] = None

# Categorical columns that stay as-is (not coerced to numeric), matching service_model/app.py
CAT_COLS = {"gender", "race", "first_careunit", "temperature_site", "intime"}


def load_model() -> None:
    """
    Load joblib artifacts from MODEL_ARTIFACTS_DIR. Called from AppConfig.ready().
    If dir or files are missing, logs a warning and leaves model/feature_cols as None.
    """
    global model, feature_cols
    try:
        from django.conf import settings
        artifacts_dir = getattr(settings, "MODEL_ARTIFACTS_DIR", None)
        if not artifacts_dir:
            logger.warning("[model_local] MODEL_ARTIFACTS_DIR not set; skipping model load.")
            return
        import os
        import joblib
        model_path = os.path.join(artifacts_dir, "sepsis_rf_pipeline.joblib")
        cols_path = os.path.join(artifacts_dir, "feature_cols.joblib")
        if not os.path.isfile(model_path) or not os.path.isfile(cols_path):
            logger.warning(
                "[model_local] Artifacts not found at %s (need sepsis_rf_pipeline.joblib, feature_cols.joblib); "
                "prediction will use stub.",
                artifacts_dir,
            )
            return
        model = joblib.load(model_path)
        feature_cols = joblib.load(cols_path)
        logger.info("[model_local] Loaded model and %d feature columns from %s", len(feature_cols), artifacts_dir)
    except Exception as e:
        logger.warning("[model_local] Failed to load model: %s; prediction will use stub.", e)
        model = None
        feature_cols = None


def is_available() -> bool:
    """Return True if the model and feature_cols are loaded and ready for prediction."""
    return model is not None and feature_cols is not None


def _flatten_current_row(current_row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge per-source dicts (vitals_hourly, sofa_hourly, etc.) into one flat record.
    Last-wins for duplicate keys. Convert datetime values to ISO strings.
    """
    flat: Dict[str, Any] = {}
    for source_name, row in current_row.items():
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                flat[k] = v.isoformat()
            else:
                flat[k] = v
    return flat


def _coerce_numeric_columns(df):
    """Convert numeric-looking columns to numbers; keep CAT_COLS as-is. Matches service_model/app.py."""
    import pandas as pd
    for c in df.columns:
        if c not in CAT_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def predict(current_row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run in-process prediction. Call only when is_available() is True.
    current_row: nested dict from _build_current_vector_from_sources (e.g. vitals_hourly, sofa_hourly, ...).
    Returns {"ok": True, "risk_score": float, "latent_class": int|None} or {"ok": False, "error": str}.
    """
    import numpy as np
    import pandas as pd

    if not is_available():
        return {"ok": False, "error": "Model not loaded"}

    try:
        record = _flatten_current_row(current_row)
        df = pd.DataFrame([record])
        df = df.reindex(columns=feature_cols, fill_value=np.nan)
        df = _coerce_numeric_columns(df)

        # Extract the latent_class input feature before prediction
        latent_class_val = df["latent_class"].iloc[0] if "latent_class" in df.columns else None
        if latent_class_val is not None and not pd.isna(latent_class_val):
            latent_class_val = int(latent_class_val)
        else:
            latent_class_val = None

        proba = model.predict_proba(df)[:, 1]
        risk_score = float(proba[0])
        return {
            "ok": True,
            "risk_score": risk_score,
            "latent_class": latent_class_val,
        }
    except Exception as e:
        logger.exception("[model_local] Prediction failed: %s", e)
        return {"ok": False, "error": str(e)}
