"""Model scoring and similarity search for the patients app."""

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from django.conf import settings
from django.db import connection
from django.utils import timezone as django_tz

from .db_utils import DERIVED_TABLE_CANDIDATES, fetch_rows, pick_first_existing
from .models import PredictionResult, SimilarPatientsResult, UniquePatientProfile

logger = logging.getLogger(__name__)

# Feature columns used for cosine similarity (must match CSV export).
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

STANDARDIZATION_EXCLUDE = frozenset({"hours_since_admission", "charttime_hour"})


def _serialize_row(row):
    """Convert datetime-ish values to ISO strings so the row is JSON-safe."""
    def _serialize_any(value):
        if isinstance(value, dict):
            return {k: _serialize_any(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_serialize_any(v) for v in value]
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if value is not None and not isinstance(value, (str, int, float, bool)):
            return str(value)
        return value

    return {k: _serialize_any(v) for k, v in row.items()}


def _normalize_hour(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
    return value


def _row_sort_time(row):
    # Procedures can have multiple rows/hour; prefer the most recent charttime.
    charttime = row.get("charttime") or row.get("charttime_hour")
    return _normalize_hour(charttime) or datetime.min


def _map_display_to_patient_time(as_of, patient):
    """Map simulation display time (March 13/14) to patient's real charttime hour.

    The demo clock runs on 2025-03-13; patients live on their actual MIMIC-IV year,
    so we shift the display hour onto the patient's intime timeline.
    """
    if not (patient and patient.intime and as_of.month == 3 and as_of.day in (13, 14)):
        return as_of
    # 2025-03-13T09:00 = end of sim hour 8; 2025-03-14T00:00 = end of sim hour 23
    sim_hour = 23 if (as_of.day == 14 and as_of.hour == 0) else (as_of.hour - 1) % 24
    adm_hour = patient.intime.hour
    if sim_hour < adm_hour:
        return as_of
    base = patient.intime.replace(minute=0, second=0, microsecond=0)
    return base + timedelta(hours=(sim_hour - adm_hour + 1))


# ----- Payload builders ------------------------------------------------------


def _prediction_payload(subject_id, stay_id, hadm_id, as_of, current_row, history_rows):
    current = _serialize_row(current_row)
    history = [_serialize_row(r) for r in history_rows]
    return {
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "as_of": as_of.isoformat(),
        "records": history + [current],
        "current_feature_vector": current,
        "source_keys": {
            source: {
                "subject_id": source_row.get("subject_id"),
                "stay_id": source_row.get("stay_id"),
                "charttime_hour": (
                    source_row.get("charttime_hour").isoformat()
                    if source_row.get("charttime_hour") is not None else None
                ),
            }
            for source, source_row in current_row.items()
        },
        "history_feature_vectors": history,
    }


def _prediction_payload_feature_matrix(subject_id, stay_id, hadm_id, as_of, current_row, history_rows):
    current = _serialize_row(current_row)
    history = [_serialize_row(r) for r in history_rows]
    return {
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "as_of": as_of.isoformat(),
        "records": history + [current],
        "current_feature_vector": current,
        "source_keys": {
            "subject_id": current_row.get("subject_id"),
            "stay_id": current_row.get("stay_id"),
            "charttime_hour": (
                current_row.get("charttime_hour").isoformat()
                if current_row.get("charttime_hour") is not None else None
            ),
        },
        "history_feature_vectors": history,
    }


# ----- DB feature fetchers ---------------------------------------------------


_REQUIRED_SOURCES = (
    "vitals_hourly",
    "procedures_hourly",
    "chemistry_hourly",
    "coagulation_hourly",
    "sofa_hourly",
)


def _fetch_required_model_sources(subject_id, stay_id, start, end, limit=50000):
    """Fetch required hourly source tables within [start, end]."""
    source_rows = {}
    for source_name in _REQUIRED_SOURCES:
        table = pick_first_existing(DERIVED_TABLE_CANDIDATES[source_name])
        if not table:
            return None, f"Missing required source table for {source_name}"

        fetched = fetch_rows(
            table=table,
            where_sql=(
                "subject_id = %(subject_id)s AND stay_id = %(stay_id)s "
                "AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s"
            ),
            params={"subject_id": subject_id, "stay_id": stay_id, "start": start, "end": end},
            order_sql="charttime_hour",
            limit=limit,
        )
        if not fetched.get("ok"):
            return None, f"Failed fetching {source_name}: {fetched.get('error', 'unknown error')}"
        if not fetched.get("rows"):
            return None, f"No rows for patient in required source {source_name}"

        source_rows[source_name] = fetched["rows"]

    return source_rows, None


def _build_current_vector_from_sources(source_rows, as_of):
    # Intersect hour sets across sources to ensure every required table contributes.
    per_source_hours = {}
    for source_name, rows in source_rows.items():
        hours = set()
        for row in rows:
            if row.get("subject_id") is None or row.get("stay_id") is None:
                continue
            hour = _normalize_hour(row.get("charttime_hour"))
            if hour is None or hour > as_of:
                continue
            hours.add(hour)
        if not hours:
            return None, f"No usable hour keys for {source_name} (requires subject_id, stay_id, charttime_hour)"
        per_source_hours[source_name] = hours

    common_hours = None
    for hours in per_source_hours.values():
        common_hours = hours if common_hours is None else common_hours & hours

    if not common_hours:
        return None, (
            "No common charttime_hour across required sources "
            "(vitals, procedures, chemistry, coagulation, sofa)"
        )

    target_hour = max(common_hours)
    current_vector = {}
    for source_name, rows in source_rows.items():
        hour_rows = [r for r in rows if _normalize_hour(r.get("charttime_hour")) == target_hour]
        if not hour_rows:
            return None, f"Missing {source_name} row at common hour {target_hour}"
        # Pick most recent row when several share an hour (e.g. procedures).
        current_vector[source_name] = sorted(hour_rows, key=_row_sort_time)[-1]

    return current_vector, None


def _fetch_feature_matrix_rows(subject_id, stay_id, start, end, limit=50000):
    table = pick_first_existing(DERIVED_TABLE_CANDIDATES["feature_matrix_hourly"])
    if not table:
        return None, "No feature matrix table found"

    fetched = fetch_rows(
        table=table,
        where_sql=(
            "subject_id = %(subject_id)s AND stay_id = %(stay_id)s "
            "AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s"
        ),
        params={"subject_id": subject_id, "stay_id": stay_id, "start": start, "end": end},
        order_sql="charttime_hour",
        limit=limit,
    )
    if not fetched.get("ok"):
        return None, fetched.get("error", "Feature matrix fetch failed")
    rows = fetched.get("rows", [])
    if not rows:
        return None, "No feature matrix rows for patient in window"
    return rows, None


def _latest_row_at_or_before(rows, as_of):
    """Return the row with the latest charttime_hour <= as_of."""
    as_of_cmp = as_of if not django_tz.is_naive(as_of) else django_tz.make_aware(as_of, timezone.utc)

    best_hour = None
    best_row = None
    for row in rows:
        hour = _normalize_hour(row.get("charttime_hour"))
        if hour is None:
            continue
        hour_cmp = hour if not django_tz.is_naive(hour) else django_tz.make_aware(hour, timezone.utc)
        if hour_cmp <= as_of_cmp and (best_hour is None or hour_cmp > best_hour):
            best_hour = hour_cmp
            best_row = row
    return best_row


# ----- Model client ----------------------------------------------------------


def _parse_model_response(data):
    """Extract (risk_score, comorbidity_group) from either response shape."""
    predictions = data.get("predictions")
    if predictions is None:
        risk_score = data.get("risk_score")
    else:
        risk_score = float(predictions[-1]) if predictions else None
    return risk_score, data.get("comorbidity_group")


def _call_external_model(model_url, payload):
    """POST payload to the external model service. Returns (data, error)."""
    if not model_url:
        logger.warning("Prediction fallback fired: no MODEL_SERVICE_URL configured")
        return None, "Model service not configured"

    try:
        import httpx
    except ImportError:
        msg = "httpx not installed"
        logger.warning("Prediction fallback fired: %s", msg)
        return None, msg

    headers = {"Content-Type": "application/json"}
    api_key = getattr(settings, "MODEL_SERVICE_API_KEY", "") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = getattr(settings, "MODEL_SERVICE_TIMEOUT", 30) or 30

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{model_url.rstrip('/')}/predict",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info("Model service raw response: %s", data)
            return data, None
    except httpx.TimeoutException as e:
        err = f"Model service timeout: {e}"
    except httpx.HTTPStatusError as e:
        err = f"Model service error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        err = f"Model service call failed: {e}"

    logger.warning("Prediction fallback fired: API failure: %s", err)
    return None, err


# ----- Public API ------------------------------------------------------------


def _lookup_patient(subject_id, stay_id, hadm_id):
    try:
        return UniquePatientProfile.objects.get(
            subject_id=subject_id, stay_id=stay_id, hadm_id=hadm_id
        )
    except UniquePatientProfile.DoesNotExist:
        return None


def get_prediction(subject_id, stay_id, hadm_id, as_of, window_hours=24):
    """Score a patient at as_of, returning risk_score + comorbidity_group.

    Tries the external model service first; on any failure (unset URL,
    timeout, HTTP error) falls back to the bundled local joblib model.
    """
    model_url = getattr(settings, "MODEL_SERVICE_URL", "") or ""
    history_hours = int(getattr(settings, "MODEL_HISTORY_HOURS", 6) or 6)

    patient = _lookup_patient(subject_id, stay_id, hadm_id)
    as_of = _map_display_to_patient_time(as_of, patient)

    patient_keys = {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id}

    cached = PredictionResult.objects.filter(**patient_keys, as_of=as_of).first()
    if cached:
        return {
            "ok": True,
            "risk_score": float(cached.risk_score),
            "comorbidity_group": str(cached.comorbidity_group),
        }

    start = as_of - timedelta(hours=window_hours)
    end = as_of

    using_feature_matrix = False
    local_history_rows = []
    matrix_rows, matrix_err = _fetch_feature_matrix_rows(subject_id, stay_id, start, end)
    if matrix_rows:
        using_feature_matrix = True
        current_row = _latest_row_at_or_before(matrix_rows, end)
        if not current_row:
            return {"ok": False, "error": "No feature matrix row at or before as_of"}
        current_hour = _normalize_hour(current_row.get("charttime_hour"))
        local_history_rows = [
            row for row in matrix_rows
            if (_normalize_hour(row.get("charttime_hour")) is not None
                and _normalize_hour(row.get("charttime_hour")) < current_hour)
        ]
    else:
        source_rows, source_err = _fetch_required_model_sources(subject_id, stay_id, start, end)
        if source_err:
            return {"ok": False, "error": f"{matrix_err}; fallback failed: {source_err}"}
        current_row, current_err = _build_current_vector_from_sources(source_rows, end)
        if current_err:
            return {"ok": False, "error": current_err}

    history_rows = local_history_rows[-history_hours:] if using_feature_matrix else []

    # Sticky comorbidity group: first prediction ever written for this patient wins.
    first_prediction = (
        PredictionResult.objects.filter(**patient_keys)
        .order_by("created_at")
        .first()
    )
    sticky_group = first_prediction.comorbidity_group if first_prediction else None

    payload_builder = (
        _prediction_payload_feature_matrix if using_feature_matrix else _prediction_payload
    )
    payload = payload_builder(subject_id, stay_id, hadm_id, as_of, current_row, history_rows)
    payload["demographics"] = {
        "anchor_age": getattr(patient, "anchor_age", None) if patient else None,
        "gender": getattr(patient, "gender", None) if patient else None,
        "race": getattr(patient, "race", None) if patient else None,
        "first_careunit": getattr(patient, "first_careunit", None) if patient else None,
    }

    data, external_error = _call_external_model(model_url, payload)
    if external_error is not None:
        from .local_model import predict_locally
        local_result = predict_locally(payload)
        if not local_result.get("ok"):
            local_error = local_result.get("error", "unknown local model error")
            return {"ok": False, "error": external_error if model_url else local_error}
        data = local_result["raw"]

    risk_score, comorbidity_group = _parse_model_response(data)
    if risk_score is None:
        return {"ok": False, "error": "Model response missing risk_score"}

    if sticky_group:
        comorbidity_group = sticky_group
    elif comorbidity_group is None:
        comorbidity_group = "unknown"

    PredictionResult.objects.update_or_create(
        **patient_keys,
        as_of=as_of,
        defaults={
            "risk_score": float(risk_score),
            "comorbidity_group": str(comorbidity_group),
        },
    )

    return {
        "ok": True,
        "risk_score": float(risk_score),
        "comorbidity_group": str(comorbidity_group),
    }


def get_current_feature_vector(subject_id, stay_id, hadm_id, as_of, window_hours=24):
    """Return the feature-matrix row used for scoring at as_of, or None."""
    patient = _lookup_patient(subject_id, stay_id, hadm_id)
    as_of = _map_display_to_patient_time(as_of, patient)

    start = as_of - timedelta(hours=window_hours)
    end = as_of
    matrix_rows, _ = _fetch_feature_matrix_rows(subject_id, stay_id, start, end)
    if not matrix_rows:
        return None
    return _latest_row_at_or_before(matrix_rows, end)


# ----- Similarity search -----------------------------------------------------


def _row_to_feature_array(row):
    arr = []
    for col in SIMILARITY_FEATURE_COLUMNS:
        v = row.get(col)
        if v is None or (isinstance(v, float) and np.isnan(v)):
            arr.append(0.0)
        else:
            try:
                arr.append(float(v))
            except (TypeError, ValueError):
                arr.append(0.0)
    return np.array(arr, dtype=np.float64)


def _fetch_candidate_rows(exclude_subject_stay_pairs):
    """Return one row per non-cohort patient (latest hour) from RDS."""
    from .cohort import get_cohort_filter

    cohort = get_cohort_filter()
    cohort_tuples = []
    if cohort and cohort.get("type") == "tuples":
        cohort_tuples = [(s, st) for s, st, _ in cohort["values"]]

    exclude_all = list(set(cohort_tuples + list(exclude_subject_stay_pairs)))

    feature_cols = ", ".join(SIMILARITY_FEATURE_COLUMNS)
    if exclude_all:
        placeholders = ", ".join(["(%s, %s)"] * len(exclude_all))
        flat_params = [x for t in exclude_all for x in t]
        where_clause = f"WHERE (subject_id, stay_id) NOT IN (VALUES {placeholders})"
    else:
        where_clause = ""
        flat_params = []

    sql = f"""
    SELECT DISTINCT ON (subject_id, stay_id)
           subject_id, stay_id, hadm_id, charttime_hour, {feature_cols}
    FROM mimiciv_derived.fisi9t_feature_matrix_hourly
    {where_clause}
    ORDER BY subject_id, stay_id, charttime_hour DESC
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, flat_params)
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as e:
        logger.warning("Failed to fetch candidate rows for similarity: %s", e)
        return []


def _fetch_sepsis_by_stay_ids(stay_ids):
    if not stay_ids:
        return {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stay_id FROM mimiciv_derived.sepsis3
                WHERE stay_id = ANY(%s) AND sepsis3 = true
                """,
                [stay_ids],
            )
            return {row[0]: True for row in cursor.fetchall()}
    except Exception as e:
        logger.warning("Could not fetch sepsis outcomes: %s", e)
        return {}


def get_similar_patients(subject_id, stay_id, hadm_id, as_of, top_k=3):
    """Top-k most similar patients by z-scored cosine similarity."""
    patient = _lookup_patient(subject_id, stay_id, hadm_id)
    as_of = _map_display_to_patient_time(as_of, patient)

    patient_keys = {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id}

    cached = SimilarPatientsResult.objects.filter(**patient_keys, as_of=as_of).first()
    if cached:
        return cached.matches[:top_k] if isinstance(cached.matches, list) else cached.matches

    vec_row = get_current_feature_vector(subject_id, stay_id, hadm_id, as_of)
    if vec_row is None:
        logger.warning(
            "Similarity: no feature vector for patient %s/%s/%s at as_of=%s",
            subject_id, stay_id, hadm_id, as_of,
        )
        return []

    candidates = _fetch_candidate_rows(exclude_subject_stay_pairs=[(subject_id, stay_id)])
    if not candidates:
        logger.warning("Similarity: no candidate rows from DB")
        return []

    meta = []
    feature_rows = []
    raw_vectors = []
    for row in candidates:
        meta.append((row["subject_id"], row["stay_id"], row["hadm_id"], row.get("charttime_hour")))
        raw_vectors.append(_row_to_feature_array(row))
        feature_rows.append({
            col: round(float(row[col]), 2) if row.get(col) is not None else None
            for col in SIMILARITY_FEATURE_COLUMNS
        })

    matrix = np.vstack(raw_vectors)
    current_arr = _row_to_feature_array(vec_row)

    # Z-score standardize using the candidate pool (plus the query vector).
    all_vectors = np.vstack([matrix, current_arr.reshape(1, -1)])
    mean = np.mean(all_vectors, axis=0)
    std = np.std(all_vectors, axis=0)
    std = np.where(std < 1e-10, 1.0, std)

    matrix_std = (matrix - mean) / std
    current_std = (current_arr - mean) / std
    for j, col in enumerate(SIMILARITY_FEATURE_COLUMNS):
        if col in STANDARDIZATION_EXCLUDE:
            matrix_std[:, j] = matrix[:, j]
            current_std[j] = current_arr[j]

    current_norm = np.linalg.norm(current_std)
    if current_norm < 1e-10:
        logger.warning("Similarity: zero feature vector for patient %s/%s", subject_id, stay_id)
        return []

    dots = matrix_std @ current_std
    row_norms = np.linalg.norm(matrix_std, axis=1)
    row_norms = np.where(row_norms < 1e-10, 1e-10, row_norms)
    sims = dots / (row_norms * current_norm)

    top_indices = np.argsort(sims)[::-1][:top_k]
    results = []
    for i in top_indices:
        s, st, h, charttime_hour = meta[i]
        results.append({
            "subject_id": s,
            "stay_id": st,
            "hadm_id": h,
            "similarity_score": float(sims[i]),
            "charttime_hour_str": str(charttime_hour) if charttime_hour else None,
            "features": feature_rows[i],
        })

    sepsis_by_stay = _fetch_sepsis_by_stay_ids([r["stay_id"] for r in results])
    for r in results:
        r["had_sepsis"] = sepsis_by_stay.get(r["stay_id"], False)

    SimilarPatientsResult.objects.update_or_create(
        **patient_keys,
        as_of=as_of,
        defaults={"matches": results},
    )
    return results
