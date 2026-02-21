-- 04_fisi9t_profile.sql
-- Materialized view: profile (demographics + ICU stay + ICD9 titles).

DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fisi9t_profile CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fisi9t_profile AS (
  SELECT
    f.subject_id,
    f.hadm_id,
    f.stay_id,
    a.anchor_age,
    id.gender,
    id.race,
    f.first_careunit,
    f.last_careunit,
    f.intime,
    f.outtime,
    f.los,
    f.seq_num,
    f.icd_code,
    f.icd_version,
    f.long_title
  FROM mimiciv_derived.fis_icd9_titled f
  JOIN mimiciv_derived.age a
    ON a.subject_id = f.subject_id
   AND a.hadm_id = f.hadm_id
  JOIN mimiciv_derived.icustay_detail id
    ON id.stay_id = f.stay_id
);

CREATE INDEX idx_fisi9t_profile_subject_id ON mimiciv_derived.fisi9t_profile (subject_id);
CREATE INDEX idx_fisi9t_profile_stay_id ON mimiciv_derived.fisi9t_profile (stay_id);
