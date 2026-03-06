"""
Similar Cases — cosine similarity against non-cohort reference patients.

Reads pre-populated sim_cache_similarity_reference table (~1,500 rows) into
memory once, then compares the current patient's feature vector against all
reference rows via cosine similarity.

The cache table is populated by: python manage.py preload_cohort_cache (step 8).
Gracefully degrades: if the table doesn't exist or is empty, the feature
simply shows "No similar patients found" in the template.
"""

import logging

import numpy as np
from django.db import connection
from faker import Faker

from .cohort import PATIENT_STAYS
from .services import _pick_first_existing, _table_exists

logger = logging.getLogger(__name__)

# ── 47 feature columns (matches team reference implementation) ──────────────

SIMILARITY_FEATURE_COLUMNS = [
    "heart_rate", "sbp", "dbp", "mbp", "sbp_ni", "dbp_ni", "mbp_ni",
    "resp_rate", "temperature", "spo2", "glucose",
    "bicarbonate", "calcium", "sodium", "potassium",
    "d_dimer", "fibrinogen", "thrombin", "inr", "pt", "ptt",
    "sofa_hr", "pao2fio2ratio_novent", "pao2fio2ratio_vent",
    "rate_epinephrine", "rate_norepinephrine", "rate_dopamine", "rate_dobutamine",
    "meanbp_min", "gcs_min", "uo_24hr", "bilirubin_max", "creatinine_max", "platelet_min",
    "respiration", "coagulation", "liver", "cardiovascular", "cns", "renal",
    "respiration_24hours", "coagulation_24hours", "liver_24hours",
    "cardiovascular_24hours", "cns_24hours", "renal_24hours", "sofa_24hours",
]

# Column mapping from our sim_*/demo_cache tables → SIMILARITY_FEATURE_COLUMNS
# Keys that exist in our tables and directly map to feature columns.
_VITALS_MAP = {
    "heart_rate": "heart_rate",
    "sbp": "sbp",
    "dbp": "dbp",
    "mbp": "mbp",
    "sbp_ni": "sbp_ni",
    "dbp_ni": "dbp_ni",
    "mbp_ni": "mbp_ni",
    "resp_rate": "resp_rate",
    "temperature": "temperature",
    "spo2": "spo2",
    "glucose": "glucose",
}

_CHEMISTRY_MAP = {
    "bicarbonate": "bicarbonate",
    "calcium": "calcium",
    "sodium": "sodium",
    "potassium": "potassium",
}

_COAGULATION_MAP = {
    "d_dimer": "d_dimer",
    "fibrinogen": "fibrinogen",
    "thrombin": "thrombin",
    "inr": "inr",
    "pt": "pt",
    "ptt": "ptt",
}

_SOFA_MAP = {
    "sofa_24hours": "sofa_24hours",
    "respiration": "respiration",
    "coagulation": "coagulation",
    "liver": "liver",
    "cardiovascular": "cardiovascular",
    "cns": "cns",
    "renal": "renal",
}

# ── Module-level cache ──────────────────────────────────────────────────────

# Tuple of (meta_list, numpy_matrix, feature_dicts_list) or None
_reference_cache = None

# Set of cohort stay_ids (used for exclusion)
_COHORT_STAY_IDS = {t[1] for t in PATIENT_STAYS} if PATIENT_STAYS else set()

_CACHE_TABLE = "simulation.sim_cache_similarity_reference"

_faker = Faker()


def _fake_name(subject_id, gender=None):
    """Generate a deterministic fake name from subject_id. Same ID → same name."""
    _faker.seed_instance(subject_id)
    if gender == "F":
        return _faker.name_female()
    elif gender == "M":
        return _faker.name_male()
    return _faker.name()


# ── Reference matrix loading ───────────────────────────────────────────────

def load_reference_matrix():
    """Load non-cohort feature vectors from the cache table into memory.

    Reads simulation.sim_cache_similarity_reference (populated by
    preload_cohort_cache step 8). Idempotent — returns cached result
    on subsequent calls.
    """
    global _reference_cache
    if _reference_cache is not None:
        return _reference_cache

    if not _table_exists(_CACHE_TABLE):
        logger.warning("[similarity] %s does not exist — run preload_cohort_cache", _CACHE_TABLE)
        return None

    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {_CACHE_TABLE}")
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        logger.warning("[similarity] Failed to read %s: %s", _CACHE_TABLE, exc)
        return None

    if not rows:
        logger.warning("[similarity] %s is empty — run preload_cohort_cache", _CACHE_TABLE)
        return None

    result = _build_cache_from_rows(rows)
    _reference_cache = result
    logger.info("[similarity] Loaded %d reference vectors from cache table", len(result[0]))
    return _reference_cache


def _build_cache_from_rows(rows):
    """Convert a list of row dicts into the (meta, matrix, feature_dicts) cache tuple.

    Demographics (admission_age, gender, race) are stored in meta so
    get_similar_patients() can use them directly without a separate profile query.
    """
    meta = []
    arrays = []
    feature_dicts = []
    for row in rows:
        meta.append({
            "subject_id": row.get("subject_id"),
            "stay_id": row.get("stay_id"),
            "hadm_id": row.get("hadm_id"),
            "admission_age": row.get("admission_age"),
            "gender": row.get("gender"),
            "race": row.get("race"),
        })
        arrays.append(_row_to_feature_array(row))
        feature_dicts.append(_row_to_feature_dict(row))

    matrix = np.vstack(arrays)
    return (meta, matrix, feature_dicts)


# ── Feature array / dict helpers ───────────────────────────────────────────

def _row_to_feature_array(row_dict):
    """Extract 47-element numpy array from a dict. None/NaN → 0.0."""
    arr = []
    for col in SIMILARITY_FEATURE_COLUMNS:
        v = row_dict.get(col)
        if v is None:
            arr.append(0.0)
        else:
            try:
                fv = float(v)
                arr.append(0.0 if np.isnan(fv) else fv)
            except (TypeError, ValueError):
                arr.append(0.0)
    return np.array(arr, dtype=np.float64)


def _row_to_feature_dict(row_dict):
    """Extract feature columns for template display (rounded floats)."""
    exclude = {"subject_id", "stay_id", "hadm_id", "admission_age", "gender", "race"}
    out = {}
    for k, v in row_dict.items():
        if k in exclude:
            continue
        if v is None:
            continue
        try:
            fv = float(v)
            if not np.isnan(fv):
                out[k] = round(fv, 2)
        except (TypeError, ValueError):
            s = str(v).strip()
            if s:
                out[k] = s
    return out


def build_vector_from_sim_data(vitals_row, chemistry_row=None, coagulation_row=None, sofa_row=None):
    """Build a feature dict from separate table rows (ORM objects or dicts).

    Maps column names to SIMILARITY_FEATURE_COLUMNS. Missing columns → None.
    Works for both demo_cache dicts and sim_* ORM .values() dicts.
    """
    def _get(obj, key):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    vector = {}
    for src_col, feat_col in _VITALS_MAP.items():
        vector[feat_col] = _get(vitals_row, src_col)
    for src_col, feat_col in _CHEMISTRY_MAP.items():
        vector[feat_col] = _get(chemistry_row, src_col)
    for src_col, feat_col in _COAGULATION_MAP.items():
        vector[feat_col] = _get(coagulation_row, src_col)
    for src_col, feat_col in _SOFA_MAP.items():
        vector[feat_col] = _get(sofa_row, src_col)
    return vector


# ── Core similarity function ───────────────────────────────────────────────

def get_similar_patients(current_vector_dict, subject_id, stay_id, top_k=3):
    """Find top_k most similar patients by cosine similarity.

    Args:
        current_vector_dict: dict with feature column names → values
        subject_id: current patient's subject_id (for exclusion)
        stay_id: current patient's stay_id (for exclusion)
        top_k: number of results

    Returns:
        list of dicts with: subject_id, stay_id, hadm_id, similarity_score,
        had_sepsis, anchor_age, gender, race, features
    """
    cached = load_reference_matrix()
    if cached is None:
        return []

    meta, matrix, feature_dicts = cached

    current_arr = _row_to_feature_array(current_vector_dict)
    current_norm = np.linalg.norm(current_arr)
    if current_norm < 1e-10:
        logger.warning("[similarity] Zero feature vector for %s/%s", subject_id, stay_id)
        return []

    # Cosine similarity: (matrix @ current) / (row_norms * current_norm)
    dots = matrix @ current_arr
    row_norms = np.linalg.norm(matrix, axis=1)
    row_norms = np.where(row_norms < 1e-10, 1e-10, row_norms)
    sims = dots / (row_norms * current_norm)

    # Top k (excluding same patient)
    top_indices = np.argsort(sims)[::-1]
    results = []
    for i in top_indices:
        if len(results) >= top_k:
            break
        m = meta[i]
        if m["subject_id"] == subject_id and m["stay_id"] == stay_id:
            continue
        results.append({
            "subject_id": m["subject_id"],
            "stay_id": m["stay_id"],
            "hadm_id": m["hadm_id"],
            "similarity_score": round(float(sims[i]), 4),
            "features": feature_dicts[i] if i < len(feature_dicts) else {},
        })

    if not results:
        return results

    # Enrich with sepsis outcome and demographics from meta
    result_stay_ids = [r["stay_id"] for r in results]
    sepsis_map = _fetch_sepsis_by_stay_ids(result_stay_ids)

    # Build profile map from cached meta (no extra DB query needed)
    meta_by_stay = {m["stay_id"]: m for m in meta}
    for r in results:
        r["had_sepsis"] = sepsis_map.get(r["stay_id"], False)
        m = meta_by_stay.get(r["stay_id"], {})
        age = m.get("admission_age")
        r["anchor_age"] = int(age) if age is not None else None
        r["display_name"] = _fake_name(r["subject_id"], m.get("gender"))
        r["gender"] = m.get("gender")
        r["race"] = m.get("race")

    return results


# ── DB helpers ─────────────────────────────────────────────────────────────

def _fetch_sepsis_by_stay_ids(stay_ids):
    """Query sepsis3 for given stay_ids. Returns {stay_id: True}."""
    if not stay_ids:
        return {}
    candidates = ["mimiciv_derived.sepsis3", "sepsis3"]
    table = _pick_first_existing(candidates)
    if not table:
        return {}
    try:
        with connection.cursor() as cursor:
            # Check if sepsis3 column exists; some schemas just have the table presence = sepsis
            cursor.execute(
                f"SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = 'sepsis3' AND column_name = 'sepsis3'"
            )
            has_sepsis3_col = cursor.fetchone() is not None

            if has_sepsis3_col:
                cursor.execute(
                    f"SELECT stay_id FROM {table} WHERE stay_id = ANY(%s) AND sepsis3 = true",
                    [stay_ids],
                )
            else:
                # If no sepsis3 boolean column, presence in table = had sepsis
                cursor.execute(
                    f"SELECT DISTINCT stay_id FROM {table} WHERE stay_id = ANY(%s)",
                    [stay_ids],
                )
            return {row[0]: True for row in cursor.fetchall()}
    except Exception as exc:
        logger.warning("[similarity] Failed to fetch sepsis outcomes: %s", exc)
        return {}
