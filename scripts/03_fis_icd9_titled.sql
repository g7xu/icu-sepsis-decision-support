-- 03_fis_icd9_titled.sql
-- Materialized view: ICD9 diagnoses joined to titles.

DROP MATERIALIZED VIEW IF EXISTS fis_icd9_titled CASCADE;

CREATE MATERIALIZED VIEW fis_icd9_titled AS (
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
  FROM fis_icd9 f
  JOIN d_icd_diagnoses d
    ON f.icd_code = d.icd_code
   AND f.icd_version = d.icd_version
);

CREATE INDEX idx_fis_icd9_titled_subject_id ON fis_icd9_titled (subject_id);
CREATE INDEX idx_fis_icd9_titled_stay_id ON fis_icd9_titled (stay_id);
