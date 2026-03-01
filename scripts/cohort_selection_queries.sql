-- =============================================================================
-- Cohort Selection Queries for ICU Sepsis Demo
-- =============================================================================
-- Criteria:
--   1. From fisi9t_unique_patient_profile (app-compatible, ICD9 patients)
--   2. ICU stay <= 1 day (los <= 1)
--   3. Has SOFA data (required for predictions)
--   4. Content-rich: above mean for BOTH vitals_rows AND procedure_rows
--   5. Stratified by admission hour: 3 patients for hours 0-2, 2 patients for hours 3-23
-- Output: (subject_id, stay_id, hadm_id) tuples for cohort.py
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Main query: Random sample per admission hour (3 for hours 0-2, 2 for hours 3-23)
-- -----------------------------------------------------------------------------
-- Copy the output into cohort.py PATIENT_STAYS as:
--   (subject_id, stay_id, hadm_id),

WITH eligible AS (
  SELECT p.subject_id, p.stay_id, p.hadm_id, p.intime
  FROM mimiciv_derived.fisi9t_unique_patient_profile p
  WHERE p.los <= 1
    AND EXISTS (
      SELECT 1 FROM mimiciv_derived.fisi9t_sofa_hourly sf
      WHERE sf.stay_id = p.stay_id
    )
),
v_counts AS (
  SELECT stay_id, COUNT(*) AS vitals_rows
  FROM mimiciv_derived.fisi9t_vitalsign_hourly
  GROUP BY stay_id
),
p_counts AS (
  SELECT stay_id, COUNT(*) AS procedure_rows
  FROM mimiciv_derived.fisi9t_procedureevents_hourly
  GROUP BY stay_id
),
richness AS (
  SELECT e.subject_id, e.stay_id, e.hadm_id, e.intime,
         COALESCE(v.vitals_rows, 0) AS vitals_rows,
         COALESCE(p.procedure_rows, 0) AS procedure_rows
  FROM eligible e
  LEFT JOIN v_counts v ON e.stay_id = v.stay_id
  LEFT JOIN p_counts p ON e.stay_id = p.stay_id
),
means AS (
  SELECT AVG(vitals_rows) AS mean_vitals,
         AVG(procedure_rows) AS mean_procedures
  FROM richness
),
above_mean AS (
  SELECT r.subject_id, r.stay_id, r.hadm_id,
         EXTRACT(HOUR FROM r.intime)::int AS admission_hour
  FROM richness r
  CROSS JOIN means m
  WHERE r.vitals_rows > m.mean_vitals
    AND r.procedure_rows > m.mean_procedures
    AND r.intime IS NOT NULL
),
ranked AS (
  SELECT subject_id, stay_id, hadm_id, admission_hour,
         row_number() OVER (PARTITION BY admission_hour ORDER BY random()) AS rn
  FROM above_mean
),
cohort AS (
  SELECT subject_id, stay_id, hadm_id, admission_hour
  FROM ranked
  WHERE (admission_hour IN (0, 1, 2) AND rn <= 3)
     OR (admission_hour BETWEEN 3 AND 23 AND rn <= 2)
)
SELECT subject_id, stay_id, hadm_id
FROM cohort
ORDER BY admission_hour, subject_id;
