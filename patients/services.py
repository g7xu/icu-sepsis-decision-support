from datetime import datetime, timedelta
from django.db import connection
from django.http import JsonResponse

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
        "sofa_hourly",
        "mimiciv_derived.sofa_hourly",
        "sofa",
        "mimiciv_derived.sofa",
    ],
    "chemistry_hourly": [
        "fisi9t_chemistry_hourly",
        "mimiciv_derived.fisi9t_chemistry_hourly",
        "chemistry_hourly",
        "mimiciv_derived.chemistry_hourly",
    ],
    "coagulation_hourly": [
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
    sql += " LIMIT %s"
    
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


def _serialize_row(row):
    """Convert datetime objects to ISO strings for JSON payload."""
    out = {}
    for k, v in row.items():
        if hasattr(v, 'isoformat'):
            out[k] = v.isoformat()
        elif v is not None and not isinstance(v, (str, int, float, bool, list, dict)):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def get_prediction(subject_id, stay_id, hadm_id, as_of, window_hours=24):
    """
    Get model prediction: risk_score and comorbidity_group for a patient at a given time.

    When MODEL_SERVICE_URL is set: fetches features, POSTs to model service, returns result.
    When not set: returns stub data for local dev.
    """
    from django.conf import settings

    model_url = getattr(settings, 'MODEL_SERVICE_URL', '') or ''
    if not model_url:
        return _get_prediction_stub(subject_id, stay_id, hadm_id, as_of)

    start = as_of - timedelta(hours=window_hours)
    wide = assemble_hourly_wide_table(
        subject_id, stay_id, hadm_id, start, as_of,
        include_sofa=True, include_labs=True,
    )
    if not wide.get('ok'):
        return {"ok": False, "error": wide.get("error", "Feature fetch failed")}

    payload = {
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "as_of": as_of.isoformat(),
        "features": {
            "hourly_wide": [_serialize_row(r) for r in wide.get("rows", [])],
            "columns": wide.get("columns", []),
        },
    }

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
    except httpx.TimeoutException as e:
        return {"ok": False, "error": f"Model service timeout: {e}"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"Model service error {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    risk_score = data.get("risk_score")
    comorbidity_group = data.get("comorbidity_group")
    if risk_score is None or comorbidity_group is None:
        return {"ok": False, "error": "Model response missing risk_score or comorbidity_group"}

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
