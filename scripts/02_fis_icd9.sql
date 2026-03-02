-- 02_fis_icd9.sql
-- Materialized view: first ICU stay + ICD9 diagnoses (ICD9-only subjects).

DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fis_icd9 CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fis_icd9 AS (
  WITH icd9_only_patients AS (
    SELECT d.subject_id
    FROM mimiciv_hosp.diagnoses_icd d
    GROUP BY d.subject_id
    HAVING max(d.icd_version) = 9
  )
  SELECT
    f.subject_id,
    f.hadm_id,
    f.stay_id,
    f.first_careunit,
    f.last_careunit,
    f.intime,
    f.outtime,
    f.los,
    d.seq_num,
    d.icd_code,
    d.icd_version
  FROM mimiciv_derived.first_icu_stay f
  JOIN icd9_only_patients p
    ON p.subject_id = f.subject_id
  JOIN mimiciv_hosp.diagnoses_icd d
    ON d.subject_id = f.subject_id
   AND d.hadm_id = f.hadm_id
  WHERE d.icd_version = 9
);

CREATE INDEX idx_fis_icd9_subject_id ON mimiciv_derived.fis_icd9 (subject_id);
CREATE INDEX idx_fis_icd9_stay_id ON mimiciv_derived.fis_icd9 (stay_id);
CREATE INDEX idx_fis_icd9_icd_code ON mimiciv_derived.fis_icd9 (icd_code);
