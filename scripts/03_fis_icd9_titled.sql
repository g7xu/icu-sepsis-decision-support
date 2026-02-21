-- 03_fis_icd9_titled.sql
-- View: ICD9 diagnoses joined to titles. No extra storage.

DROP VIEW IF EXISTS mimiciv_derived.fis_icd9_titled CASCADE;

CREATE VIEW mimiciv_derived.fis_icd9_titled AS
SELECT
  f.subject_id,
  f.hadm_id,
  f.stay_id,
  f.first_careunit,
  f.last_careunit,
  f.intime,
  f.outtime,
  f.los,
  f.seq_num,
  f.icd_code,
  f.icd_version,
  d.long_title
FROM mimiciv_derived.fis_icd9 f
JOIN mimiciv_hosp.d_icd_diagnoses d
  ON f.icd_code = d.icd_code AND f.icd_version = d.icd_version;
