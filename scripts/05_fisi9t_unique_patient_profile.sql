-- 05_fisi9t_unique_patient_profile.sql
-- Materialized view: unique patient profile per subject_id.

DROP MATERIALIZED VIEW IF EXISTS fisi9t_unique_patient_profile CASCADE;

CREATE MATERIALIZED VIEW fisi9t_unique_patient_profile AS (
  SELECT DISTINCT ON (subject_id)
    subject_id,
    anchor_age,
    gender,
    race,
    hadm_id,
    stay_id,
    first_careunit,
    last_careunit,
    intime,
    outtime,
    los
  FROM fisi9t_profile p
);

CREATE UNIQUE INDEX idx_fisi9t_unique_profile_subject_id ON fisi9t_unique_patient_profile (subject_id);
CREATE INDEX idx_fisi9t_unique_profile_stay_id ON fisi9t_unique_patient_profile (stay_id);
