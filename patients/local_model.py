"""Local fallback prediction model.

When MODEL_SERVICE_URL is unset or the external API call fails, services.py
delegates to ``predict_locally`` here. The model is a scikit-learn estimator
serialized with ``joblib.dump`` and located at ``settings.LOCAL_MODEL_PATH``.

The model is loaded lazily on first use and cached at module scope, so import
cost is paid by the first prediction request rather than at Django startup.
"""

import logging
import os
from typing import Optional, Tuple

import numpy as np
from django.conf import settings

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_LOAD_ERROR: Optional[str] = None


def _model_path() -> str:
    return getattr(settings, "LOCAL_MODEL_PATH", "") or ""


def is_available() -> bool:
    """Return True if a local model file exists at the configured path."""
    path = _model_path()
    return bool(path) and os.path.isfile(path)


def _load_model():
    """Load the joblib model on first call; cache the result (success or error)."""
    global _MODEL, _MODEL_LOAD_ERROR
    if _MODEL is not None:
        return _MODEL
    if _MODEL_LOAD_ERROR is not None:
        raise RuntimeError(_MODEL_LOAD_ERROR)

    path = _model_path()
    if not path:
        _MODEL_LOAD_ERROR = "LOCAL_MODEL_PATH is not configured"
        raise RuntimeError(_MODEL_LOAD_ERROR)
    if not os.path.isfile(path):
        _MODEL_LOAD_ERROR = f"Local model file not found at {path}"
        logger.warning("Local model load failed: %s", _MODEL_LOAD_ERROR)
        raise RuntimeError(_MODEL_LOAD_ERROR)

    try:
        import joblib
    except ImportError as e:
        _MODEL_LOAD_ERROR = f"joblib not installed: {e}"
        raise RuntimeError(_MODEL_LOAD_ERROR)

    try:
        _MODEL = joblib.load(path)
    except Exception as e:
        _MODEL_LOAD_ERROR = f"Failed to load joblib model from {path}: {e}"
        logger.exception("Local model load failed")
        raise RuntimeError(_MODEL_LOAD_ERROR)

    logger.info("Local model loaded from %s", path)
    return _MODEL


# Columns the bundled RandomForest pipeline expects (52 features). Order does
# not matter — the ColumnTransformer routes by name — but every column must be
# present. Missing values become NaN; the pipeline imputes them.
_FEATURE_COLUMNS = [
    # demographics
    "anchor_age", "gender", "race", "first_careunit",
    # vitals (hourly)
    "heart_rate", "sbp", "dbp", "mbp", "sbp_ni", "dbp_ni", "mbp_ni",
    "resp_rate", "temperature", "temperature_site", "spo2",
    # chemistry (hourly)
    "glucose", "bicarbonate", "calcium", "sodium", "potassium",
    # coagulation (hourly)
    "d_dimer", "fibrinogen", "thrombin", "inr", "pt", "ptt",
    # sofa (hourly)
    "sofa_hr",
    # procedures (hourly)
    "pao2fio2ratio_novent", "pao2fio2ratio_vent",
    "rate_epinephrine", "rate_norepinephrine", "rate_dopamine", "rate_dobutamine",
    # sofa 24h aggregates + components
    "meanbp_min", "gcs_min", "uo_24hr",
    "bilirubin_max", "creatinine_max", "platelet_min",
    "respiration", "coagulation", "liver", "cardiovascular", "cns", "renal",
    "respiration_24hours", "coagulation_24hours", "liver_24hours",
    "cardiovascular_24hours", "cns_24hours", "renal_24hours", "sofa_24hours",
]


def _flatten_feature_vector(payload: dict) -> dict:
    """Collapse the prediction payload's current_feature_vector into one flat dict.

    Two payload shapes are possible:
      * Multi-source: ``current_feature_vector`` is keyed by source name
        (vitals_hourly, chemistry_hourly, ...) and each value is a column->value
        dict. We merge them; first non-None value for any column wins.
      * Feature-matrix: ``current_feature_vector`` is already a flat
        column->value dict (single materialized-view row).
    """
    cfv = payload.get("current_feature_vector") or {}
    nested = any(isinstance(v, dict) for v in cfv.values())
    flat: dict = {}
    if nested:
        for source_row in cfv.values():
            if not isinstance(source_row, dict):
                continue
            for col, val in source_row.items():
                if val is not None and col not in flat:
                    flat[col] = val
    else:
        for col, val in cfv.items():
            if val is not None:
                flat[col] = val

    demographics = payload.get("demographics") or {}
    for col, val in demographics.items():
        if val is not None:
            flat[col] = val
    return flat


def _build_local_model_features(payload: dict):
    """Build a 1-row pandas DataFrame in the schema the joblib pipeline expects.

    Returns a DataFrame of shape (1, 52). Missing columns are set to ``np.nan``
    so the pipeline's SimpleImputer can fill them in. Categorical columns are
    passed as strings (the OneHotEncoder ignores unseen categories).
    """
    import pandas as pd

    flat = _flatten_feature_vector(payload)
    row = {col: flat.get(col, np.nan) for col in _FEATURE_COLUMNS}
    return pd.DataFrame([row], columns=_FEATURE_COLUMNS)


def _score(model, X: np.ndarray) -> float:
    """Run the model and extract a single risk-score float in [0, 1]."""
    if hasattr(model, "predict_proba"):
        proba = np.asarray(model.predict_proba(X))
        # Binary classifier: take probability of the positive class (last column).
        return float(proba[0, -1])
    if hasattr(model, "decision_function"):
        return float(np.asarray(model.decision_function(X)).ravel()[0])
    return float(np.asarray(model.predict(X)).ravel()[0])


def predict_locally(payload: dict) -> dict:
    """Run the local model on a payload. Mirrors the external-API return shape.

    Returns either ``{"ok": True, "risk_score": float, "comorbidity_group": None,
    "raw": {...}}`` or ``{"ok": False, "error": "..."}``.
    """
    try:
        model = _load_model()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        features = _build_local_model_features(payload)
    except NotImplementedError as e:
        logger.warning("Local model adapter not implemented: %s", e)
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("Local model feature build failed")
        return {"ok": False, "error": f"Local model feature build failed: {e}"}

    try:
        risk_score = _score(model, features)
    except Exception as e:
        logger.exception("Local model prediction failed")
        return {"ok": False, "error": f"Local model prediction failed: {e}"}

    return {
        "ok": True,
        "risk_score": risk_score,
        "comorbidity_group": None,
        "raw": {"predictions": [risk_score], "source": "local_joblib"},
    }


def reset_cache_for_tests() -> Tuple[object, Optional[str]]:
    """Clear the cached model + load error. Intended for tests / dev reload."""
    global _MODEL, _MODEL_LOAD_ERROR
    prev = (_MODEL, _MODEL_LOAD_ERROR)
    _MODEL = None
    _MODEL_LOAD_ERROR = None
    return prev
