from datetime import datetime, timedelta
import json
from django.db import connection

# Use the same candidate list, but mapped to string names
DERIVED_TABLE_CANDIDATES = {
    "profile": [
        "simulation.sim_patient",
        "fisi9t_unique_patient_profile",
        "mimiciv_derived.fisi9t_unique_patient_profile",
    ],
    "vitals_hourly": [
        "simulation.sim_vitalsign_hourly",
        "fisi9t_vitalsign_hourly",
        "mimiciv_derived.fisi9t_vitalsign_hourly",
    ],
    "procedures_hourly": [
        "simulation.sim_procedureevents_hourly",
        "fisi9t_procedureevents_hourly",
        "mimiciv_derived.fisi9t_procedureevents_hourly",
    ],
    "sofa_hourly": [
        "simulation.sim_sofa_hourly",
        "sofa_hourly",
        "mimiciv_derived.sofa_hourly",
        "sofa",
        "mimiciv_derived.sofa",
    ],
    "chemistry_hourly": [
        "simulation.sim_chemistry_hourly",
        "fisi9t_chemistry_hourly",
        "mimiciv_derived.fisi9t_chemistry_hourly",
        "chemistry_hourly",
        "mimiciv_derived.chemistry_hourly",
    ],
    "coagulation_hourly": [
        "simulation.sim_coagulation_hourly",
        "fisi9t_coagulation_hourly",
        "mimiciv_derived.fisi9t_coagulation_hourly",
        "coagulation_hourly",
        "mimiciv_derived.coagulation_hourly",
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

def get_hourly_feature_sources(subject_id, stay_id, start, end, include_procedures=True, include_sofa=True, limit=20000):
    sources = {}
    
    # 1. Vitals
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

    # 2. Procedures
    if include_procedures:
        proc_table = _pick_first_existing(DERIVED_TABLE_CANDIDATES["procedures_hourly"])
        if proc_table:
            sources["procedures_hourly"] = _fetch_rows(
                table=proc_table,
                where_sql="stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s",
                params={"stay_id": stay_id, "start": start, "end": end},
                order_sql="charttime_hour, charttime, itemid",
                limit=limit
            )
        else:
            sources["procedures_hourly"] = {"ok": False, "error": "No procedures table found"}

    # 3. SOFA
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

def assemble_hourly_wide_table(subject_id, stay_id, hadm_id, start, end, include_sofa=True, include_labs=True, limit=20000):
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
    if include_labs:
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


def _normalize_hour(value):
    """Parse value to a naive datetime for comparison. Strips timezone info if present."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except Exception:
            return None
    # Strip tzinfo so naive/aware comparisons never fail
    if hasattr(value, 'tzinfo') and value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    return value


def _row_sort_time(row):
    # Procedures can have multiple rows/hour; prefer most recent charttime.
    charttime = row.get("charttime")
    if charttime is None:
        charttime = row.get("charttime_hour")
    return _normalize_hour(charttime) or datetime.min


def _fetch_required_model_sources(subject_id, stay_id, start, end, limit=50000):
    """
    Fetch data from each model source table for the given patient and time window.

    Sources are split into required and optional:
    - Required (vitals, chemistry, coagulation, sofa): prediction fails if no rows exist.
    - Optional (procedures): sparse data that many patients lack. Missing rows are
      silently skipped; the corresponding feature columns will be filled with NaN by
      model_local.predict() via pandas reindex(fill_value=NaN).
    """
    # Vitals are the only strictly required source — every patient has hourly vitals.
    required_sources = {
        "vitals_hourly": DERIVED_TABLE_CANDIDATES["vitals_hourly"],
    }
    # Optional sources: labs, procedures, coagulation, and SOFA scores are sparse —
    # not every patient has rows in every hour. Missing rows are silently skipped;
    # the corresponding feature columns will be filled with NaN by
    # model_local.predict() via pandas reindex(fill_value=NaN).
    # The sklearn pipeline (ColumnTransformer + RandomForest) tolerates NaN inputs.
    optional_sources = {
        "chemistry_hourly": DERIVED_TABLE_CANDIDATES["chemistry_hourly"],
        "procedures_hourly": DERIVED_TABLE_CANDIDATES["procedures_hourly"],
        "coagulation_hourly": DERIVED_TABLE_CANDIDATES["coagulation_hourly"],
        "sofa_hourly": DERIVED_TABLE_CANDIDATES["sofa_hourly"],
    }

    source_rows = {}
    for source_name, candidates in {**required_sources, **optional_sources}.items():
        is_optional = source_name in optional_sources
        table = _pick_first_existing(candidates)
        if not table:
            if is_optional:
                continue
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
            if is_optional:
                continue
            return None, f"Failed fetching {source_name}: {fetched.get('error', 'unknown error')}"
        if not fetched.get("rows"):
            if is_optional:
                continue
            return None, f"No rows for patient in required source {source_name}"

        source_rows[source_name] = fetched["rows"]

    return source_rows, None


def _build_current_vector_from_sources(source_rows, as_of):
    """
    Build a single feature vector from multi-source rows.

    Uses vitals_hourly as the anchor: the target hour is the latest vitals hour <= as_of.
    For each optional source present in source_rows, the closest hour <= target_hour is
    used.  If an optional source has no rows at or before the target hour it is silently
    skipped (its feature columns become NaN downstream via pandas reindex).
    """
    # Normalise as_of so naive/aware comparisons never fail
    as_of = _normalize_hour(as_of) or as_of

    # --- determine target hour from vitals (the only required source) ---
    vitals_hours = set()
    for row in source_rows.get("vitals_hourly", []):
        hour = _normalize_hour(row.get("charttime_hour"))
        if hour is not None and hour <= as_of:
            vitals_hours.add(hour)

    if not vitals_hours:
        return None, "No vitals rows at or before as_of — cannot build feature vector"

    target_hour = max(vitals_hours)

    # --- pick one row per source at/before the target hour ---
    current_vector = {}
    for source_name, rows in source_rows.items():
        eligible = [
            r for r in rows
            if (_normalize_hour(r.get("charttime_hour")) or datetime.min) <= target_hour
        ]
        if not eligible:
            # Optional source has no data at or before the target hour — skip it.
            continue
        # Pick the row closest to target_hour; ties broken by most recent charttime.
        best = max(eligible, key=lambda r: (
            _normalize_hour(r.get("charttime_hour")) or datetime.min,
            _row_sort_time(r),
        ))
        current_vector[source_name] = best

    return current_vector, None


def get_prediction(subject_id, stay_id, hadm_id, as_of, window_hours=24):
    """
    Get model prediction: risk_score and comorbidity_group for a patient at a given time.
    Uses in-process joblib model if artifacts are loaded; otherwise stub.
    """
    from . import model_local

    start = as_of - timedelta(hours=window_hours)
    source_rows, source_err = _fetch_required_model_sources(subject_id, stay_id, start, as_of)
    if source_err:
        return {"ok": False, "error": source_err}

    current_row, current_err = _build_current_vector_from_sources(source_rows, as_of)
    if current_err:
        return {"ok": False, "error": current_err}

    if model_local.is_available():
        return model_local.predict(current_row)

    return _get_prediction_stub(subject_id, stay_id, hadm_id, as_of)


def _get_prediction_stub(subject_id, stay_id, hadm_id, as_of):
    """Stub prediction when model artifacts are not loaded."""
    import hashlib
    key = f"{subject_id}_{stay_id}_{hadm_id}_{as_of}"
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    risk_score = round((h % 100) / 100.0, 2)
    return {
        "ok": True,
        "risk_score": risk_score,
        "latent_class": h % 4,  # stub: 4 subgroups
    }
