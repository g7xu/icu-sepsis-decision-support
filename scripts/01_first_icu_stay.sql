-- 01_first_icu_stay.sql
-- View: first ICU stay per subject (no extra storage — computed on query).

DROP VIEW IF EXISTS mimiciv_derived.first_icu_stay CASCADE;

CREATE VIEW mimiciv_derived.first_icu_stay AS
WITH ranked AS (
  SELECT
    i.subject_id,
    i.hadm_id,
    i.stay_id,
    i.first_careunit,
    i.last_careunit,
    i.intime,
    i.outtime,
    i.los,
    row_number() OVER (PARTITION BY i.subject_id ORDER BY i.intime) AS rn
  FROM mimiciv_icu.icustays i
)
SELECT
  subject_id,
  hadm_id,
  stay_id,
  first_careunit,
  last_careunit,
  intime,
  outtime,
  los
FROM ranked
WHERE rn = 1;
