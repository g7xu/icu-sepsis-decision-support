-- 01_first_icu_stay.sql
-- Materialized view: first ICU stay per subject.

DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.first_icu_stay CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.first_icu_stay AS (
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
    FROM icustays i
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
  WHERE rn = 1
);

CREATE INDEX idx_first_icu_stay_subject_id ON first_icu_stay (subject_id);
CREATE INDEX idx_first_icu_stay_stay_id ON first_icu_stay (stay_id);
