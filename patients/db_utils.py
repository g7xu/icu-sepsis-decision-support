"""Shared DB helpers for the patients app."""

from django.db import connection


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


def table_exists(table_name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [table_name])
        return cursor.fetchone()[0]


def pick_first_existing(candidates):
    for name in candidates:
        if table_exists(name):
            return name
    return None


def fetch_rows(table, where_sql, params, order_sql=None, limit=5000):
    sql = f"SELECT * FROM {table} WHERE {where_sql}"
    if order_sql:
        sql += f" ORDER BY {order_sql}"
    sql += " LIMIT %(limit)s"

    final_params = {**params, "limit": limit}

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, final_params)
            columns = [col[0] for col in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return {
                "ok": True,
                "table": table,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
            }
    except Exception as e:
        return {
            "ok": False,
            "table": table,
            "error": str(e),
            "rows": [],
            "columns": [],
        }
