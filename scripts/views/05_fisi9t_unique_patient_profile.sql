-- 05_fisi9t_unique_patient_profile.sql
-- Materialized view: the exact 60 cohort patients (March 13th).
--
-- Queries base tables directly (icustays + age + icustay_detail).
-- Does NOT use the intermediate ICD chain (scripts 01-04) — those columns
-- are not needed here and were causing full-table scans over millions of rows.
--
-- To update the cohort: edit the stay_id IN (...) list to match cohort.py.

DROP VIEW IF EXISTS mimiciv_derived.fisi9t_unique_patient_profile CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fisi9t_unique_patient_profile CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fisi9t_unique_patient_profile AS
SELECT
  i.subject_id,
  a.anchor_age,
  id.gender,
  id.race,
  i.hadm_id,
  i.stay_id,
  i.first_careunit,
  i.last_careunit,
  i.intime,
  i.outtime,
  i.los
FROM mimiciv_icu.icustays i
JOIN mimiciv_derived.age a
  ON a.subject_id = i.subject_id AND a.hadm_id = i.hadm_id
JOIN mimiciv_derived.icustay_detail id
  ON id.stay_id = i.stay_id
WHERE i.stay_id IN (
  -- cohort.py PATIENT_STAYS — 60 patients, March 13th
  35475449, 32289289, 31666009, 30578301, 34717765,
  31105947, 32686693, 33731767, 37228395, 39147546,
  30746476, 31841232, 34187539, 36944842, 31704775,
  35252251, 34527365, 30358011, 30850798, 33671272,
  37618708, 35059449, 37248737, 32365302, 32115271,
  36686900, 32482524, 35853856, 30434920, 32682847,
  34349625, 38817873, 30788906, 32411992, 32333651,
  34656650, 30788026, 32437903, 34745126, 37980939,
  37391388, 36444941, 31738657, 33651413, 39493447,
  30412117, 34203168, 34943317, 38284045, 31453779,
  33091914, 38329757, 39331586, 34188073, 34513562,
  31962240, 36873691, 34520611, 36046550, 33702266
);

CREATE INDEX ON mimiciv_derived.fisi9t_unique_patient_profile (subject_id);
CREATE INDEX ON mimiciv_derived.fisi9t_unique_patient_profile (stay_id);
CREATE INDEX ON mimiciv_derived.fisi9t_unique_patient_profile (hadm_id);
CREATE INDEX ON mimiciv_derived.fisi9t_unique_patient_profile (intime);
