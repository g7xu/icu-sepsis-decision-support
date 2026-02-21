-- 02_fis_icd9.sql
-- View: first ICU stay + ICD9 diagnoses (ICD9-only subjects). No extra storage.

DROP VIEW IF EXISTS mimiciv_derived.fis_icd9 CASCADE;

CREATE VIEW mimiciv_derived.fis_icd9 AS
WITH icd9_only_patients AS (
  SELECT mimiciv_hosp.diagnoses_icd.subject_id
  FROM mimiciv_hosp.diagnoses_icd
  GROUP BY mimiciv_hosp.diagnoses_icd.subject_id
  HAVING max(mimiciv_hosp.diagnoses_icd.icd_version) = 9
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
JOIN icd9_only_patients p ON p.subject_id = f.subject_id
JOIN mimiciv_hosp.diagnoses_icd d
  ON d.subject_id = f.subject_id AND d.hadm_id = f.hadm_id
WHERE d.icd_version = 9;
