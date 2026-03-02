"""
Export feature matrix rows for non-cohort patients to CSV.

Used for similarity search: when viewing a prediction, find the 3 most similar
feature vectors from patients outside the demo cohort (cosine or Jaccard similarity).

Usage:
  python manage.py export_similarity_matrix
  python manage.py export_similarity_matrix --output static/similarity_matrix.csv

Output path defaults to SIMILARITY_CSV_PATH from settings (.env), or static/similarity_matrix.csv.
Output: CSV with subject_id, stay_id, hadm_id, charttime_hour + all feature columns.
Excludes the 51 patients in PATIENT_STAYS (cohort.py).
"""
import csv
import os
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from patients.cohort import get_cohort_filter


class Command(BaseCommand):
    help = "Export feature matrix (non-cohort) to CSV for similarity search."

    def add_arguments(self, parser):
        default_path = getattr(settings, "SIMILARITY_CSV_PATH", "static/similarity_matrix.csv")
        parser.add_argument(
            "--output",
            type=str,
            default=default_path,
            help=f"Output CSV path (default from SIMILARITY_CSV_PATH or {default_path})",
        )

    def handle(self, *args, **options):
        output_path = options["output"]
        cohort = get_cohort_filter()
        if not cohort or cohort.get("type") != "tuples":
            self.stderr.write("PATIENT_STAYS (tuples) must be set in cohort.py")
            return

        stay_tuples = [(s, st) for s, st, _ in cohort["values"]]
        placeholders = ", ".join(["(%s, %s)"] * len(stay_tuples))
        flat = [x for t in stay_tuples for x in t]

        sql = f"""
        SELECT subject_id, stay_id, hadm_id, charttime_hour,
               heart_rate, sbp, dbp, mbp, sbp_ni, dbp_ni, mbp_ni,
               resp_rate, temperature, temperature_site, spo2, glucose,
               bicarbonate, calcium, sodium, potassium,
               d_dimer, fibrinogen, thrombin, inr, pt, ptt,
               sofa_hr, pao2fio2ratio_novent, pao2fio2ratio_vent,
               rate_epinephrine, rate_norepinephrine, rate_dopamine, rate_dobutamine,
               meanbp_min, gcs_min, uo_24hr, bilirubin_max, creatinine_max, platelet_min,
               respiration, coagulation, liver, cardiovascular, cns, renal,
               respiration_24hours, coagulation_24hours, liver_24hours,
               cardiovascular_24hours, cns_24hours, renal_24hours, sofa_24hours
        FROM mimiciv_derived.fisi9t_feature_matrix_hourly
        WHERE (subject_id, stay_id) NOT IN (VALUES {placeholders})
        ORDER BY subject_id, stay_id, charttime_hour
        """

        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, flat)
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
        except Exception as e:
            self.stderr.write(f"Query failed: {e}")
            self.stderr.write(
                "Ensure fisi9t_feature_matrix_hourly exists (run scripts/11_fisi9t_feature_matrix_hourly.sql)"
            )
            return

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)

        self.stdout.write(
            self.style.SUCCESS(f"Exported {len(rows)} rows to {output_path}")
        )
