-- =============================================================================
-- Cohort Selection Queries for ICU Sepsis Demo
-- =============================================================================
-- Criteria:
--   1. From fisi9t_unique_patient_profile (app-compatible, ICD9 patients)
--   2. ICU stay <= 1 day (los <= 1)
--   3. Content-rich only: sample from top stays by vitals + procedure rows
--   4. Stratified: 25 sepsis, 26 non-sepsis (~48.7% sepsis)
-- Output: (subject_id, stay_id, hadm_id) tuples for cohort.py
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Main query: Stratified random sample from content-rich stays (25 sepsis + 26 non-sepsis)
-- -----------------------------------------------------------------------------
-- Copy the output into cohort.py PATIENT_STAYS as:
--   (subject_id, stay_id, hadm_id),

WITH eligible AS (
  SELECT p.subject_id, p.stay_id, p.hadm_id,
         (s.stay_id IS NOT NULL) AS had_sepsis
  FROM mimiciv_derived.fisi9t_unique_patient_profile p
  LEFT JOIN mimiciv_derived.sepsis3 s ON p.stay_id = s.stay_id AND s.sepsis3 = true
  WHERE p.los <= 1
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
  SELECT e.subject_id, e.stay_id, e.hadm_id, e.had_sepsis,
         COALESCE(v.vitals_rows, 0) + COALESCE(p.procedure_rows, 0) AS total_rows
  FROM eligible e
  LEFT JOIN v_counts v ON e.stay_id = v.stay_id
  LEFT JOIN p_counts p ON e.stay_id = p.stay_id
),
top_rich AS (
  SELECT subject_id, stay_id, hadm_id, had_sepsis
  FROM richness
  ORDER BY total_rows DESC
  LIMIT 2000
),
sepsis_pool AS (
  SELECT subject_id, stay_id, hadm_id,
         row_number() OVER (ORDER BY random()) AS rn
  FROM top_rich
  WHERE had_sepsis
),
non_sepsis_pool AS (
  SELECT subject_id, stay_id, hadm_id,
         row_number() OVER (ORDER BY random()) AS rn
  FROM top_rich
  WHERE NOT had_sepsis
),
cohort AS (
  SELECT subject_id, stay_id, hadm_id FROM sepsis_pool WHERE rn <= 25
  UNION ALL
  SELECT subject_id, stay_id, hadm_id FROM non_sepsis_pool WHERE rn <= 26
)
SELECT subject_id, stay_id, hadm_id
FROM cohort
ORDER BY subject_id;
