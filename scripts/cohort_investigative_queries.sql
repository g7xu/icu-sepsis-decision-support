-- =============================================================================
-- Investigative Queries for Cohort Selection
-- =============================================================================
-- Exploratory queries to inspect the pool before running cohort_selection_queries.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Pool size and sepsis rate (los <= 1, has SOFA data)
-- -----------------------------------------------------------------------------
SELECT
  COUNT(*) AS total_stays,
  SUM(CASE WHEN s.stay_id IS NOT NULL THEN 1 ELSE 0 END) AS sepsis_count,
  ROUND(100.0 * SUM(CASE WHEN s.stay_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_sepsis
FROM mimiciv_derived.fisi9t_unique_patient_profile p
LEFT JOIN mimiciv_derived.sepsis3 s ON p.stay_id = s.stay_id AND s.sepsis3 = true
WHERE p.los <= 1
  AND EXISTS (SELECT 1 FROM mimiciv_derived.fisi9t_sofa_hourly sf WHERE sf.stay_id = p.stay_id);


-- -----------------------------------------------------------------------------
-- 2. Sepsis vs non-sepsis counts (los <= 1)
-- -----------------------------------------------------------------------------
SELECT
  CASE WHEN s.stay_id IS NOT NULL THEN 'sepsis' ELSE 'non_sepsis' END AS group_name,
  COUNT(*) AS stay_count
FROM mimiciv_derived.fisi9t_unique_patient_profile p
LEFT JOIN mimiciv_derived.sepsis3 s ON p.stay_id = s.stay_id AND s.sepsis3 = true
-- WHERE p.los <= 1
GROUP BY 1;


-- -----------------------------------------------------------------------------
-- 3. Content-rich: Top stays by vitals + procedure rows (los <= 1, has SOFA)
-- -----------------------------------------------------------------------------
-- Inspect distribution of content richness. Adjust LIMIT as needed.
WITH eligible AS (
  SELECT p.subject_id, p.stay_id, p.hadm_id,
         (s.stay_id IS NOT NULL) AS had_sepsis
  FROM mimiciv_derived.fisi9t_unique_patient_profile p
  LEFT JOIN mimiciv_derived.sepsis3 s ON p.stay_id = s.stay_id AND s.sepsis3 = true
  WHERE p.los <= 1
    AND EXISTS (SELECT 1 FROM mimiciv_derived.fisi9t_sofa_hourly sf WHERE sf.stay_id = p.stay_id)
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
)
SELECT e.subject_id, e.stay_id, e.hadm_id, e.had_sepsis,
       COALESCE(v.vitals_rows, 0) AS vitals_rows,
       COALESCE(p.procedure_rows, 0) AS procedure_rows,
       COALESCE(v.vitals_rows, 0) + COALESCE(p.procedure_rows, 0) AS total_rows
FROM eligible e
LEFT JOIN v_counts v ON e.stay_id = v.stay_id
LEFT JOIN p_counts p ON e.stay_id = p.stay_id
ORDER BY total_rows DESC
LIMIT 20;


-- -----------------------------------------------------------------------------
-- 3b. Patients above mean for each content-rich metric (los <= 1, has SOFA)
-- -----------------------------------------------------------------------------
WITH eligible AS (
  SELECT p.subject_id, p.stay_id, p.hadm_id
  FROM mimiciv_derived.fisi9t_unique_patient_profile p
  WHERE p.los <= 1
    AND EXISTS (SELECT 1 FROM mimiciv_derived.fisi9t_sofa_hourly sf WHERE sf.stay_id = p.stay_id)
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
  SELECT e.stay_id,
         COALESCE(v.vitals_rows, 0) AS vitals_rows,
         COALESCE(p.procedure_rows, 0) AS procedure_rows,
         COALESCE(v.vitals_rows, 0) + COALESCE(p.procedure_rows, 0) AS total_rows
  FROM eligible e
  LEFT JOIN v_counts v ON e.stay_id = v.stay_id
  LEFT JOIN p_counts p ON e.stay_id = p.stay_id
),
means AS (
  SELECT AVG(vitals_rows)::numeric(10,2) AS mean_vitals,
         AVG(procedure_rows)::numeric(10,2) AS mean_procedures,
         AVG(total_rows)::numeric(10,2) AS mean_total
  FROM richness
)
SELECT
  (SELECT mean_vitals FROM means) AS mean_vitals_rows,
  COUNT(*) FILTER (WHERE r.vitals_rows > (SELECT mean_vitals FROM means)) AS above_mean_vitals,
  (SELECT mean_procedures FROM means) AS mean_procedure_rows,
  COUNT(*) FILTER (WHERE r.procedure_rows > (SELECT mean_procedures FROM means)) AS above_mean_procedures,
  (SELECT mean_total FROM means) AS mean_total_rows,
  COUNT(*) FILTER (WHERE r.total_rows > (SELECT mean_total FROM means)) AS above_mean_total,
  COUNT(*) AS total_stays
FROM richness r;


-- -----------------------------------------------------------------------------
-- 4. Content-rich pool: sepsis/non-sepsis counts in top N (has SOFA)
-- -----------------------------------------------------------------------------
-- Check if top 2000 has enough sepsis and non-sepsis for the cohort (25 + 26)
WITH eligible AS (
  SELECT p.subject_id, p.stay_id, p.hadm_id,
         (s.stay_id IS NOT NULL) AS had_sepsis
  FROM mimiciv_derived.fisi9t_unique_patient_profile p
  LEFT JOIN mimiciv_derived.sepsis3 s ON p.stay_id = s.stay_id AND s.sepsis3 = true
  WHERE p.los <= 1
    AND EXISTS (SELECT 1 FROM mimiciv_derived.fisi9t_sofa_hourly sf WHERE sf.stay_id = p.stay_id)
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
top_2000 AS (
  SELECT * FROM richness ORDER BY total_rows DESC LIMIT 2000
)
SELECT
  had_sepsis,
  COUNT(*) AS stay_count
FROM top_2000
GROUP BY had_sepsis;


-- -----------------------------------------------------------------------------
-- 4b. Admission hour distribution: at least 3 patients per hour? (above mean vitals + procedures)
-- -----------------------------------------------------------------------------
-- Checks if each simulation hour (0-23) has >= 3 eligible patients admitted.
-- Hours with < 3 may make the demo feel sparse when advancing time.
WITH eligible AS (
  SELECT p.subject_id, p.stay_id, p.hadm_id, p.intime,
         (s.stay_id IS NOT NULL) AS had_sepsis
  FROM mimiciv_derived.fisi9t_unique_patient_profile p
  LEFT JOIN mimiciv_derived.sepsis3 s ON p.stay_id = s.stay_id AND s.sepsis3 = true
  WHERE p.los <= 1
    AND EXISTS (SELECT 1 FROM mimiciv_derived.fisi9t_sofa_hourly sf WHERE sf.stay_id = p.stay_id)
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
  SELECT e.subject_id, e.stay_id, e.hadm_id, e.intime, e.had_sepsis,
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
  SELECT r.subject_id, r.stay_id, r.hadm_id, r.intime
  FROM richness r
  CROSS JOIN means m
  WHERE r.vitals_rows > m.mean_vitals
    AND r.procedure_rows > m.mean_procedures
),
by_hour AS (
  SELECT EXTRACT(HOUR FROM intime)::int AS admission_hour,
         COUNT(*) AS stay_count
  FROM above_mean
  WHERE intime IS NOT NULL
  GROUP BY EXTRACT(HOUR FROM intime)
),
all_hours AS (
  SELECT generate_series(0, 23) AS hour
)
SELECT h.hour AS admission_hour,
       COALESCE(b.stay_count, 0) AS stay_count,
       CASE WHEN COALESCE(b.stay_count, 0) >= 3 THEN 'OK' ELSE 'LOW (<3)' END AS status
FROM all_hours h
LEFT JOIN by_hour b ON h.hour = b.admission_hour
ORDER BY h.hour;


-- -----------------------------------------------------------------------------
-- 4c. Summary: how many hours have < 3 patients? (above mean vitals + procedures)
-- -----------------------------------------------------------------------------
WITH eligible AS (
  SELECT p.stay_id, p.intime
  FROM mimiciv_derived.fisi9t_unique_patient_profile p
  WHERE p.los <= 1
    AND EXISTS (SELECT 1 FROM mimiciv_derived.fisi9t_sofa_hourly sf WHERE sf.stay_id = p.stay_id)
),
v_counts AS (SELECT stay_id, COUNT(*) AS vitals_rows FROM mimiciv_derived.fisi9t_vitalsign_hourly GROUP BY stay_id),
p_counts AS (SELECT stay_id, COUNT(*) AS procedure_rows FROM mimiciv_derived.fisi9t_procedureevents_hourly GROUP BY stay_id),
richness AS (
  SELECT e.stay_id, e.intime,
         COALESCE(v.vitals_rows, 0) AS vitals_rows,
         COALESCE(p.procedure_rows, 0) AS procedure_rows
  FROM eligible e
  LEFT JOIN v_counts v ON e.stay_id = v.stay_id
  LEFT JOIN p_counts p ON e.stay_id = p.stay_id
),
means AS (SELECT AVG(vitals_rows) AS mean_vitals, AVG(procedure_rows) AS mean_procedures FROM richness),
above_mean AS (
  SELECT r.stay_id, r.intime FROM richness r CROSS JOIN means m
  WHERE r.vitals_rows > m.mean_vitals AND r.procedure_rows > m.mean_procedures
),
by_hour AS (
  SELECT EXTRACT(HOUR FROM intime)::int AS h, COUNT(*) AS cnt
  FROM above_mean WHERE intime IS NOT NULL GROUP BY 1
)
SELECT
  COUNT(*) FILTER (WHERE COALESCE(b.cnt, 0) < 3) AS hours_with_fewer_than_3,
  24 - COUNT(*) FILTER (WHERE COALESCE(b.cnt, 0) < 3) AS hours_with_3_or_more,
  MIN(COALESCE(b.cnt, 0)) AS min_per_hour,
  MAX(COALESCE(b.cnt, 0)) AS max_per_hour
FROM generate_series(0, 23) g(h)
LEFT JOIN by_hour b ON g.h = b.h;


-- -----------------------------------------------------------------------------
-- 5. Check procedure/vitals counts for specific stay_ids
-- -----------------------------------------------------------------------------
-- Replace the stay_ids with your chosen cohort's stay_ids
/*
SELECT p.subject_id, p.stay_id, p.hadm_id,
       (SELECT COUNT(*) FROM mimiciv_derived.fisi9t_vitalsign_hourly v 
        WHERE v.stay_id = p.stay_id) AS vitals_rows,
       (SELECT COUNT(*) FROM mimiciv_derived.fisi9t_procedureevents_hourly pr 
        WHERE pr.stay_id = p.stay_id) AS procedure_rows
FROM mimiciv_derived.fisi9t_unique_patient_profile p
WHERE p.stay_id IN (35475449, 32289289, 31666009)
ORDER BY vitals_rows DESC, procedure_rows DESC;
*/
