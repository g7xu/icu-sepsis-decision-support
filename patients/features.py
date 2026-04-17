"""Feature-source extraction and wide-table assembly."""

from .db_utils import DERIVED_TABLE_CANDIDATES, fetch_rows, pick_first_existing


def _fetch_hourly(table_key, params, include_subject=False, limit=20000):
    """Fetch hourly rows from the first existing candidate table for table_key."""
    table = pick_first_existing(DERIVED_TABLE_CANDIDATES[table_key])
    if not table:
        return {"ok": False, "error": f"No {table_key} table found"}

    where = (
        "subject_id = %(subject_id)s AND stay_id = %(stay_id)s "
        "AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s"
        if include_subject
        else "stay_id = %(stay_id)s AND charttime_hour >= %(start)s AND charttime_hour <= %(end)s"
    )
    return fetch_rows(
        table=table,
        where_sql=where,
        params=params,
        order_sql="charttime_hour",
        limit=limit,
    )


def get_static_feature_sources(subject_id, stay_id, hadm_id, limit=10):
    profile_table = pick_first_existing(DERIVED_TABLE_CANDIDATES["profile"])
    if not profile_table:
        return {"profile": {"ok": False, "error": "No profile table found"}}

    return {
        "profile": fetch_rows(
            table=profile_table,
            where_sql="subject_id = %(subject_id)s AND stay_id = %(stay_id)s AND hadm_id = %(hadm_id)s",
            params={"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
            limit=limit,
        )
    }


def get_hourly_feature_sources(subject_id, stay_id, start, end, include_sofa=True, limit=20000):
    base = {"subject_id": subject_id, "stay_id": stay_id, "start": start, "end": end}
    stay_only = {"stay_id": stay_id, "start": start, "end": end}

    sources = {
        "vitals_hourly": _fetch_hourly("vitals_hourly", base, include_subject=True, limit=limit),
        "procedures_hourly": _fetch_hourly("procedures_hourly", stay_only, limit=limit),
        "chemistry_hourly": _fetch_hourly("chemistry_hourly", stay_only, limit=limit),
        "coagulation_hourly": _fetch_hourly("coagulation_hourly", stay_only, limit=limit),
    }
    if include_sofa:
        sources["sofa_hourly"] = _fetch_hourly("sofa_hourly", stay_only, limit=limit)
    return sources


def assemble_hourly_wide_table(subject_id, stay_id, hadm_id, start, end, include_sofa=True, limit=20000):
    base = {"subject_id": subject_id, "stay_id": stay_id, "start": start, "end": end}
    stay_only = {"stay_id": stay_id, "start": start, "end": end}

    vitals = _fetch_hourly("vitals_hourly", base, include_subject=True, limit=limit)
    if not vitals.get("ok"):
        return vitals

    optional_sources = [
        ("chemistry", _fetch_hourly("chemistry_hourly", stay_only, limit=limit)),
        ("coagulation", _fetch_hourly("coagulation_hourly", stay_only, limit=limit)),
    ]
    if include_sofa:
        optional_sources.append(("sofa", _fetch_hourly("sofa_hourly", stay_only, limit=limit)))

    # Merge by charttime_hour
    wide_by_hour = {}

    def upsert_rows(prefix, rows):
        for r in rows:
            hour = r.get("charttime_hour")
            if not hour:
                continue
            base_row = wide_by_hour.setdefault(hour, {
                "subject_id": subject_id,
                "stay_id": stay_id,
                "hadm_id": hadm_id,
                "charttime_hour": hour,
            })
            for k, v in r.items():
                if k not in ("subject_id", "stay_id", "hadm_id", "charttime_hour"):
                    base_row[f"{prefix}__{k}"] = v

    upsert_rows("vitals", vitals.get("rows", []))
    for prefix, result in optional_sources:
        if result and result.get("ok"):
            upsert_rows(prefix, result.get("rows", []))

    sorted_hours = sorted(wide_by_hour.keys())
    wide_rows = [wide_by_hour[h] for h in sorted_hours]

    cols = []
    seen = set()
    for r in wide_rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)

    return {
        "ok": True,
        "table": "hourly_wide_assembled",
        "columns": cols,
        "rows": wide_rows,
        "row_count": len(wide_rows),
    }
