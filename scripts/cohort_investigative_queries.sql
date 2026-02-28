-- =============================================================================
-- Investigative Queries for Cohort Selection
-- =============================================================================
-- Exploratory queries to inspect the pool before running cohort_selection_queries.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Pool size and sepsis rate (los <= 1)
-- -----------------------------------------------------------------------------
SELECT
  COUNT(*) AS total_stays,
  SUM(CASE WHEN s.stay_id IS NOT NULL THEN 1 ELSE 0 END) AS sepsis_count,
  ROUND(100.0 * SUM(CASE WHEN s.stay_id IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_sepsis
FROM mimiciv_derived.fisi9t_unique_patient_profile p
LEFT JOIN mimiciv_derived.sepsis3 s ON p.stay_id = s.stay_id AND s.sepsis3 = true
WHERE p.los <= 1;


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
-- 3. Content-rich: Top stays by vitals + procedure rows (los <= 1)
-- -----------------------------------------------------------------------------
-- Inspect distribution of content richness. Adjust LIMIT as needed.
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
)
SELECT e.subject_id, e.stay_id, e.hadm_id, e.had_sepsis,
       COALESCE(v.vitals_rows, 0) AS vitals_rows,
       COALESCE(p.procedure_rows, 0) AS procedure_rows,
       COALESCE(v.vitals_rows, 0) + COALESCE(p.procedure_rows, 0) AS total_rows
FROM eligible e
LEFT JOIN v_counts v ON e.stay_id = v.stay_id
LEFT JOIN p_counts p ON e.stay_id = p.stay_id
ORDER BY total_rows DESC
LIMIT 1000;


-- -----------------------------------------------------------------------------
-- 4. Content-rich pool: sepsis/non-sepsis counts in top N
-- -----------------------------------------------------------------------------
-- Check if top 2000 has enough sepsis and non-sepsis for the cohort (25 + 26)
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
top_2000 AS (
  SELECT * FROM richness ORDER BY total_rows DESC LIMIT 2000
)
SELECT
  had_sepsis,
  COUNT(*) AS stay_count
FROM top_2000
GROUP BY had_sepsis;


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
