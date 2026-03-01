import logging
from datetime import datetime, timedelta, timezone
import json
from django.db import connection

logger = logging.getLogger(__name__)

# Use the same candidate list, but mapped to string names
DERIVED_TABLE_CANDIDATES = {
    "profile": [
        "fisi9t_unique_patient_profile",
        "mimiciv_derived.fisi9t_unique_patient_profile",
    ],
    "vitals_hourly": [
        "fisi9t_vitalsign_hourly",
        "mimiciv_derived.fisi9t_vitalsign_hourly",
    ],
    "procedures_hourly": [
        "fisi9t_procedureevents_hourly",
        "mimiciv_derived.fisi9t_procedureevents_hourly",
    ],
    "sofa_hourly": [
        "fisi9t_sofa_hourly",
        "mimiciv_derived.fisi9t_sofa_hourly",
    ],
    "feature_matrix_hourly": [
        "fisi9t_feature_matrix_hourly",
        "mimiciv_derived.fisi9t_feature_matrix_hourly",
    ],
    "chemistry_hourly": [
        "fisi9t_chemistry_hourly",
        "mimiciv_derived.fisi9t_chemistry_hourly",
    ],
    "coagulation_hourly": [
        "fisi9t_coagulation_hourly",
        "mimiciv_derived.fisi9t_coagulation_hourly",
    ],
    "sepsis3": [
        "sepsis3",
        "mimiciv_derived.sepsis3",
    ],
}

def _table_exists(table_name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [table_name])
        return cursor.fetchone()[0]

def _pick_first_existing(candidates):
    for name in candidates:
        if _table_exists(name):
            return name
    return None

def _fetch_rows(table, where_sql, params, order_sql=None, limit=5000):
    sql = f"SELECT * FROM {table} WHERE {where_sql}"
    if order_sql:
        sql += f" ORDER BY {order_sql}"
    sql += " LIMIT %(limit)s"
    
    # Params needs to be a list/tuple for Django's raw cursor usually, 
    # but named params dictionary works with some backends. 
    # To be safe and standard with Django raw SQL, let's use the params list style 
    # or named style if we use cursor.execute(sql, params_dict).
    # Django's cursor.execute supports dictionary params if using %(name)s syntax.
    
    # Let's adjust the input SQL to use %s or %(name)s.
    # The calling code below uses :name (SQLAlchemy style). 
    # We will need to adapt the queries to use %(name)s.
    
    final_params = params.copy()
    final_params['limit'] = limit

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, final_params)
            columns = [col[0] for col in cursor.description]
            results = [
                dict(zip(columns, row))
                for row in cursor.fetchall()
            ]
            return {
                "ok": True,
                "table": table,
                "columns": columns,
                "rows": results,
                "row_count": len(results)
            }
    except Exception as e:
        return {
            "ok": False,
            "table": table,
            "error": str(e),
            "rows": [],
            "columns": []
        }

def get_static_feature_sources(subject_id, stay_id, hadm_id, limit=10):
    sources = {}
    profile_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["profile"])
    
    if not profile_table:
        sources["profile"] = {"ok": False, "error": "No profile table found"}
        return sources

    sources["profile"] = _fetch_rows(
        table=profile_table,
        where_sql="subject_id = %(subject_id)s AND stay_id = %(stay_id)s AND hadm_id = %(hadm_id)s",
        params={"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        limit=limit
    )
    return sources


def get_sepsis3_suspected_infection_time(subject_id, stay_id):
    """
    Return suspected_infection_time from mimiciv_derived.sepsis3 for the given
    (subject_id, stay_id), or None if not found. Uses time (hour, minute) for
    logic; date is ignored.
    """
    table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["sepsis3"])
    if not table:
        return None
    result = _fetch_rows(
        table=table,
        where_sql="subject_id = %(subject_id)s AND stay_id = %(stay_id)s",
        params={"subject_id": subject_id, "stay_id": stay_id},
        limit=1,
    )
    if not result.get("ok") or not result.get("rows"):
        return None
    row = result["rows"][0]
    return row.get("suspected_infection_time")


def get_hourly_feature_sources(subject_id, stay_id, start, end, include_sofa=True, limit=20000):
    sources = {}
    
    # 1. vitals
    vitals_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["vitals_hourly"])
    if vitals_table:
        sources["vitals_hourly"] = _fetch_rows(
            table=vitals_table,
            where_sql="subject_id = %(subject_id)s AND stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
            params={"subject_id": subject_id, "stay_id": stay_id, "start": start, "end": end},
            order_sql="charttime_hour",
            limit=limit
        )
    else:
        sources["vitals_hourly"] = {"ok": False, "error": "No vitals table found"}

    # 2. procedures
    proc_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["procedures_hourly"])
    if proc_table:
        sources["procedures_hourly"] = _fetch_rows(
            table=proc_table,
            where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
            params={"stay_id": stay_id, "start": start, "end": end},
            order_sql="charttime_hour",
            limit=limit
        )
    else:
        sources["procedures_hourly"] = {"ok": False, "error": "No procedures table found"}

    # 3. chemistry
    chem_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["chemistry_hourly"])
    if chem_table:
        sources["chemistry_hourly"] = _fetch_rows(
            table=chem_table,
            where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
            params={"stay_id": stay_id, "start": start, "end": end},
            order_sql="charttime_hour",
            limit=limit
        )
    else:
        sources["chemistry_hourly"] = {"ok": False, "error": "No chemistry table found"}

    # 4. coagulation
    coag_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["coagulation_hourly"])
    if coag_table:
        sources["coagulation_hourly"] = _fetch_rows(
            table=coag_table,
            where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
            params={"stay_id": stay_id, "start": start, "end": end},
            order_sql="charttime_hour",
            limit=limit
        )
    else:
        sources["coagulation_hourly"] = {"ok": False, "error": "No coagulation table found"}
    # 5. SOFA
    if include_sofa:
        sofa_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["sofa_hourly"])
        if sofa_table:
            sources["sofa_hourly"] = _fetch_rows(
                table=sofa_table,
                where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
                params={"stay_id": stay_id, "start": start, "end": end},
                order_sql="charttime_hour",
                limit=limit
            )
        else:
            sources["sofa_hourly"] = {"ok": False, "error": "No SOFA table found"}

    return sources

def assemble_hourly_wide_table(subject_id, stay_id, hadm_id, start, end, include_sofa=True, limit=20000):
    # Fetch base vitals (required)
    vitals_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["vitals_hourly"])
    if not vitals_table:
        return {"ok": False, "error": "Missing vitals table"}

    vitals = _fetch_rows(
        table=vitals_table,
        where_sql="subject_id = %(subject_id)s AND stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
        params={"subject_id": subject_id, "stay_id": stay_id, "start": start, "end": end},
        order_sql="charttime_hour",
        limit=limit
    )
    
    if not vitals.get("ok"):
        return vitals

    # Fetch optional merge sources
    sofa = None
    if include_sofa:
        sofa_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["sofa_hourly"])
        if sofa_table:
            sofa = _fetch_rows(
                table=sofa_table,
                where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
                params={"stay_id": stay_id, "start": start, "end": end},
                order_sql="charttime_hour",
                limit=limit
            )

    chemistry = None
    coagulation = None
    chem_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["chemistry_hourly"])
    if chem_table:
        chemistry = _fetch_rows(
            table=chem_table,
            where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
            params={"stay_id": stay_id, "start": start, "end": end},
            order_sql="charttime_hour",
            limit=limit
        )
        
    coag_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["coagulation_hourly"])
    if coag_table:
        coagulation = _fetch_rows(
            table=coag_table,
            where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
            params={"stay_id": stay_id, "start": start, "end": end},
            order_sql="charttime_hour",
            limit=limit
        )

    # Merge logic
    wide_by_hour = {}

    def upsert_rows(prefix, rows):
        for r in rows:
            # charttime_hour is a datetime object coming from Django cursor
            hour = r.get("charttime_hour")
            if not hour:
                continue
            
            # Use string representation of hour as key to avoid hash issues if any
            # actually datetime objects are hashable, so it's fine.
            
            base = wide_by_hour.setdefault(hour, {
                "subject_id": subject_id, 
                "stay_id": stay_id, 
                "hadm_id": hadm_id, 
                "charttime_hour": hour
            })
            
            for k, v in r.items():
                if k not in ("subject_id", "stay_id", "hadm_id", "charttime_hour"):
                    base[f"{prefix}__{k}"] = v

    upsert_rows("vitals", vitals.get("rows", []))
    if sofa and sofa.get("ok"):
        upsert_rows("sofa", sofa.get("rows", []))
    if chemistry and chemistry.get("ok"):
        upsert_rows("chemistry", chemistry.get("rows", []))
    if coagulation and coagulation.get("ok"):
        upsert_rows("coagulation", coagulation.get("rows", []))

    # Flatten back to list
    sorted_hours = sorted(wide_by_hour.keys())
    wide_rows = [wide_by_hour[h] for h in sorted_hours]
    
    # Collect all columns seen
    cols = []
    seen_cols = set()
    for r in wide_rows:
        for k in r.keys():
            if k not in seen_cols:
                seen_cols.add(k)
                cols.append(k)

    return {
        "ok": True,
        "table": "hourly_wide_assembled",
        "columns": cols,
        "rows": wide_rows,
        "row_count": len(wide_rows)
    }


def _serialize_row(row):
    """Convert datetime objects to ISO strings for JSON payload."""
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


def _as_of_key(as_of):
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    return as_of.astimezone(timezone.utc).strftime("%Y%m%dT%H0000Z")


def _build_patient_prefix(prefix, subject_id, stay_id, hadm_id):
    return f"{prefix.rstrip('/')}/patients/{subject_id}_{stay_id}_{hadm_id}"


def _get_s3_client(settings):
    try:
        import boto3
    except ImportError:
        return None, "boto3 not installed. Run: pip install boto3"

    region = getattr(settings, "MODEL_S3_REGION", "") or None
    access_key = getattr(settings, "AWS_ACCESS_KEY_ID", "") or None
    secret_key = getattr(settings, "AWS_SECRET_ACCESS_KEY", "") or None
    session_token = getattr(settings, "AWS_SESSION_TOKEN", "") or None

    if not access_key or not secret_key:
        return None, "Missing AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY in .env"

    client_kwargs = {
        "region_name": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }
    if session_token:
        client_kwargs["aws_session_token"] = session_token

    return boto3.client("s3", **client_kwargs), None


def _s3_key_exists(s3, bucket, key):
    """Check if an S3 object exists without downloading it."""
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def _write_json_to_s3(s3, bucket, key, payload, skip_if_exists=False):
    """Write JSON to S3. If skip_if_exists=True, do nothing when key already exists."""
    if skip_if_exists and _s3_key_exists(s3, bucket, key):
        return
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )


def _read_json_from_s3(s3, bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def _list_s3_keys(s3, bucket, prefix):
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            keys.append(item["Key"])
    return sorted(keys)


def _extract_current_hour_vector(wide_rows, as_of):
    rows = [r for r in wide_rows if r.get("charttime_hour") is not None]
    if not rows:
        return None

    def _hour_value(row):
        value = row.get("charttime_hour")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except Exception:
                return None
        return value

    parsed = [(r, _hour_value(r)) for r in rows]
    parsed = [(r, dt) for r, dt in parsed if dt is not None and dt <= as_of]
    if not parsed:
        return None
    return sorted(parsed, key=lambda x: x[1])[-1][0]


def _prediction_payload(subject_id, stay_id, hadm_id, as_of, current_row, history_rows):
    current_serialized = _serialize_row(current_row)
    history_serialized = [_serialize_row(r) for r in history_rows]
    return {
        "patient": {
            "subject_id": subject_id,
            "stay_id": stay_id,
            "hadm_id": hadm_id,
        },
        "as_of": as_of.isoformat(),
        "records": history_serialized + [current_serialized],
        "current_feature_vector": current_serialized,
        "source_keys": {
            source: {
                "subject_id": source_row.get("subject_id"),
                "stay_id": source_row.get("stay_id"),
                "charttime_hour": (source_row.get("charttime_hour").isoformat()
                    if source_row.get("charttime_hour") is not None else None),
            }
            for source, source_row in current_row.items()
        },
        "history_feature_vectors": history_serialized,
    }


def _prediction_payload_feature_matrix(subject_id, stay_id, hadm_id, as_of, current_row, history_rows):
    current_serialized = _serialize_row(current_row)
    history_serialized = [_serialize_row(r) for r in history_rows]
    return {
        "patient": {
            "subject_id": subject_id,
            "stay_id": stay_id,
            "hadm_id": hadm_id,
        },
        "as_of": as_of.isoformat(),
        "records": history_serialized + [current_serialized],
        "current_feature_vector": current_serialized,
        "source_keys": {
            "subject_id": current_row.get("subject_id"),
            "stay_id": current_row.get("stay_id"),
            "charttime_hour": (current_row.get("charttime_hour").isoformat()
                if current_row.get("charttime_hour") is not None else None),
        },
        "history_feature_vectors": history_serialized,
    }


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
    # Procedures can have multiple rows/hour; prefer most recent charttime.
    charttime = row.get("charttime")
    if charttime is None:
        charttime = row.get("charttime_hour")
    return _normalize_hour(charttime) or datetime.min


def _fetch_required_model_sources(subject_id, stay_id, start, end, limit=50000):
    """
    Fetch required hourly source tables for predictions.
    Uses timestamp range (start/end must use patient's actual year for DB queries).
    """
    required_sources = {
        "vitals_hourly": DERIVED_TABLE_CANDIDATES["vitals_hourly"],
        "procedures_hourly": DERIVED_TABLE_CANDIDATES["procedures_hourly"],
        "chemistry_hourly": DERIVED_TABLE_CANDIDATES["chemistry_hourly"],
        "coagulation_hourly": DERIVED_TABLE_CANDIDATES["coagulation_hourly"],
        "sofa_hourly": DERIVED_TABLE_CANDIDATES["sofa_hourly"],
    }

    source_rows = {}
    for source_name, candidates in required_sources.items():
        table = _pick_first_existing(candidates)
        if not table:
            return None, f"Missing required source table for {source_name}"

        fetched = _fetch_rows(
            table=table,
            where_sql=(
                "subject_id = %(subject_id)s "
                "AND stay_id = %(stay_id)s "
                "AND charttime_hour >= %(start)s "
                "AND charttime_hour <= %(end)s"
            ),
            params={
                "subject_id": subject_id,
                "stay_id": stay_id,
                "start": start,
                "end": end,
            },
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
    # Build per-source hour sets and intersect to ensure all required tables contribute.
    per_source_hours = {}
    for source_name, rows in source_rows.items():
        hours = set()
        for row in rows:
            sid = row.get("subject_id")
            stid = row.get("stay_id")
            hour = _normalize_hour(row.get("charttime_hour"))
            if sid is None or stid is None or hour is None:
                continue
            if hour <= as_of:
                hours.add(hour)
        if not hours:
            return None, f"No usable hour keys for {source_name} (requires subject_id, stay_id, charttime_hour)"
        per_source_hours[source_name] = hours

    common_hours = None
    for hours in per_source_hours.values():
        common_hours = set(hours) if common_hours is None else common_hours.intersection(hours)

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
        # If multiple rows at same hour (e.g. procedures), pick most recent charttime.
        chosen = sorted(hour_rows, key=_row_sort_time)[-1]
        current_vector[source_name] = chosen

    return current_vector, None


def _fetch_feature_matrix_rows(subject_id, stay_id, start, end, limit=50000):
    """
    Fetch feature matrix rows for a patient within a time window.
    Uses year from start/end (caller must pass real patient year for DB queries).
    """
    table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["feature_matrix_hourly"])
    if not table:
        return None, "No feature matrix table found"

    fetched = _fetch_rows(
        table=table,
        where_sql=(
            "subject_id = %(subject_id)s "
            "AND stay_id = %(stay_id)s "
            "AND charttime_hour >= %(start)s "
            "AND charttime_hour <= %(end)s"
        ),
        params={
            "subject_id": subject_id,
            "stay_id": stay_id,
            "start": start,
            "end": end,
        },
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
    valid = []
    for row in rows:
        hour = _normalize_hour(row.get("charttime_hour"))
        if hour is not None and hour <= as_of:
            valid.append((hour, row))
    if not valid:
        return None
    valid.sort(key=lambda t: t[0])
    return valid[-1][1]


def _load_history_vectors_from_s3(s3, bucket, patient_prefix, history_limit, current_key):
    feature_prefix = f"{patient_prefix}/features/"
    keys = _list_s3_keys(s3, bucket, feature_prefix)
    history_keys = [k for k in keys if k != current_key][-history_limit:]
    rows = []
    for key in history_keys:
        try:
            payload = _read_json_from_s3(s3, bucket, key)
            if payload.get("feature_vector"):
                rows.append(payload["feature_vector"])
        except Exception:
            continue
    return rows


def _load_first_comorbidity_group_from_s3(s3, bucket, patient_prefix):
    pred_prefix = f"{patient_prefix}/predictions/"
    keys = _list_s3_keys(s3, bucket, pred_prefix)
    for key in keys:
        try:
            payload = _read_json_from_s3(s3, bucket, key)
            group = payload.get("comorbidity_group")
            if group:
                return str(group)
        except Exception:
            continue
    return None


def get_prediction(subject_id, stay_id, hadm_id, as_of, window_hours=24):
    """
    Get model prediction: risk_score and comorbidity_group for a patient at a given time.

    When MODEL_SERVICE_URL is set: fetches features, POSTs to model service, returns result.
    When not set: returns error indicating model service is not configured.

    The as_of param uses normalized 2025-03-13 for display. For DB queries we look up
    the patient's actual admission year (MIMIC-IV uses shifted dates) and use that
    so we fetch the correct rows from vitals/chemistry/etc.
    """
    from django.conf import settings
    from .models import UniquePatientProfile

    model_url = getattr(settings, "MODEL_SERVICE_URL", "") or ""
    if not model_url:
        return {
            "ok": False,
            "error": "Model service not configured. Set MODEL_SERVICE_URL in .env to enable predictions."
        }

    s3_bucket = getattr(settings, "MODEL_S3_BUCKET", "") or ""
    s3_prefix = getattr(settings, "MODEL_S3_PREFIX", "model-io") or "model-io"
    history_hours = int(getattr(settings, "MODEL_HISTORY_HOURS", 6) or 6)

    # Look up patient's actual admission year for DB queries (MIMIC-IV dates are shifted)
    try:
        patient = UniquePatientProfile.objects.get(
            subject_id=subject_id, stay_id=stay_id, hadm_id=hadm_id
        )
        patient_year = patient.intime.year if patient.intime else 2025
    except UniquePatientProfile.DoesNotExist:
        patient_year = 2025

    # Build start/end with patient's actual year for DB queries
    start_normalized = as_of - timedelta(hours=window_hours)
    start = start_normalized.replace(year=patient_year)
    end = as_of.replace(year=patient_year)

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
            if _normalize_hour(row.get("charttime_hour")) is not None
            and _normalize_hour(row.get("charttime_hour")) < current_hour
        ]
    else:
        source_rows, source_err = _fetch_required_model_sources(subject_id, stay_id, start, end)
        if source_err:
            return {"ok": False, "error": f"{matrix_err}; fallback failed: {source_err}"}
        current_row, current_err = _build_current_vector_from_sources(source_rows, end)
        if current_err:
            return {"ok": False, "error": current_err}

    s3 = None
    patient_prefix = _build_patient_prefix(s3_prefix, subject_id, stay_id, hadm_id)
    current_feature_key = None
    history_rows = []
    first_group = None

    if s3_bucket:
        s3, err = _get_s3_client(settings)
        if err:
            return {"ok": False, "error": err}

        as_of_key = _as_of_key(as_of)
        current_feature_key = f"{patient_prefix}/features/{as_of_key}.json"
        feature_payload = {
            "patient": {
                "subject_id": subject_id,
                "stay_id": stay_id,
                "hadm_id": hadm_id,
            },
            "as_of": as_of.isoformat(),
            "feature_vector": _serialize_row(current_row),
        }
        try:
            _write_json_to_s3(s3, s3_bucket, current_feature_key, feature_payload, skip_if_exists=True)
            history_rows = _load_history_vectors_from_s3(
                s3, s3_bucket, patient_prefix, history_hours, current_feature_key
            )
            first_group = _load_first_comorbidity_group_from_s3(s3, s3_bucket, patient_prefix)
        except Exception as e:
            return {"ok": False, "error": f"S3 IO failed: {e}"}
    else:
        # No S3: use DB-derived history from feature matrix when available
        history_rows = local_history_rows[-history_hours:] if using_feature_matrix else []

    if using_feature_matrix:
        payload = _prediction_payload_feature_matrix(
            subject_id, stay_id, hadm_id, as_of, current_row, history_rows
        )
    else:
        payload = _prediction_payload(
            subject_id, stay_id, hadm_id, as_of, current_row, history_rows
        )

    headers = {"Content-Type": "application/json"}
    api_key = getattr(settings, 'MODEL_SERVICE_API_KEY', '') or ''
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    timeout = getattr(settings, 'MODEL_SERVICE_TIMEOUT', 30) or 30

    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "httpx not installed. Run: pip install httpx"}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{model_url.rstrip('/')}/predict",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.warning("Model service raw response: %s", data)
    except httpx.TimeoutException as e:
        return {"ok": False, "error": f"Model service timeout: {e}"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"Model service error {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Model returns {"predictions": [float, ...]} - one per record; use last as current risk
    predictions = data.get("predictions")
    if predictions is None:
        risk_score = data.get("risk_score")  # fallback to legacy format
    else:
        risk_score = float(predictions[-1]) if predictions else None

    if risk_score is None:
        return {"ok": False, "error": "Model response missing risk_score"}

    # Comorbidity group: use S3 cached value, model response, or placeholder until model team adds it
    comorbidity_group = data.get("comorbidity_group")
    if first_group:
        comorbidity_group = first_group
    elif comorbidity_group is None:
        comorbidity_group = "unknown"  # placeholder until model returns comorbidity_group

    # Persist prediction output for audit/replay
    if s3 and s3_bucket:
        try:
            pred_key = f"{patient_prefix}/predictions/{_as_of_key(as_of)}.json"
            _write_json_to_s3(
                s3,
                s3_bucket,
                pred_key,
                {
                    "patient": {
                        "subject_id": subject_id,
                        "stay_id": stay_id,
                        "hadm_id": hadm_id,
                    },
                    "as_of": as_of.isoformat(),
                    "risk_score": float(risk_score),
                    "comorbidity_group": str(comorbidity_group),
                },
                skip_if_exists=True,
            )
            io_key = f"{patient_prefix}/io/{_as_of_key(as_of)}.json"
            _write_json_to_s3(
                s3,
                s3_bucket,
                io_key,
                {"request": payload, "response": data},
                skip_if_exists=True,
            )
        except Exception:
            # Do not fail prediction response for audit write failures.
            pass

    return {
        "ok": True,
        "risk_score": float(risk_score),
        "comorbidity_group": str(comorbidity_group),
    }


def _get_prediction_stub(subject_id, stay_id, hadm_id, as_of):
    """Stub prediction for local dev when MODEL_SERVICE_URL is not set."""
    import hashlib
    key = f"{subject_id}_{stay_id}_{hadm_id}_{as_of}"
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    risk_score = round((h % 100) / 100.0, 2)
    groups = ["cardiovascular", "renal", "respiratory", "hepatic", "hematologic", "other"]
    comorbidity_group = groups[h % len(groups)]
    return {
        "ok": True,
        "risk_score": risk_score,
        "comorbidity_group": comorbidity_group,
    }
