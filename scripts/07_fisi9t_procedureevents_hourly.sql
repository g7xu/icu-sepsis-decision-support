-- 07_fisi9t_procedureevents_hourly.sql
-- Materialized view: hourly procedure events per stay.
-- Index on (stay_id, charttime_hour) for fast advance-time and patient-detail queries.

DROP VIEW IF EXISTS mimiciv_derived.fisi9t_procedureevents_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fisi9t_procedureevents_hourly CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fisi9t_procedureevents_hourly AS
WITH cohort AS (
  SELECT DISTINCT subject_id, stay_id
  FROM mimiciv_derived.fisi9t_unique_patient_profile
),
stay_window AS (
  SELECT
    c.subject_id, c.stay_id,
    date_trunc('hour', id.icu_intime) AS start_hour,
    date_trunc('hour', id.icu_outtime) AS end_hour,
    id.icu_intime, id.icu_outtime
  FROM cohort c
  JOIN mimiciv_derived.icustay_detail id ON id.stay_id = c.stay_id
),
hour_grid AS (
  SELECT sw.subject_id, sw.stay_id, gs.gs AS hour_ts
  FROM stay_window sw
  CROSS JOIN LATERAL generate_series(sw.start_hour, sw.end_hour + interval '1 hour', interval '1 hour') gs(gs)
),
events AS (
  SELECT
    p.subject_id, p.stay_id,
    p.storetime AS charttime,
    date_trunc('hour', p.storetime + interval '30 minutes') AS charttime_hour,
    p.caregiver_id, p.itemid, p.value, p.valueuom,
    p.location, p.locationcategory, p.orderid, p.linkorderid,
    p.ordercategoryname, p.ordercategorydescription, p.patientweight,
    p.isopenbag, p.continueinnextdept, p.statusdescription,
    p.originalamount, p.originalrate,
    di.label AS item_label, di.unitname AS item_unitname,
    di.lownormalvalue AS item_lownormalvalue, di.highnormalvalue AS item_highnormalvalue
  FROM mimiciv_icu.procedureevents p
  JOIN cohort c ON c.subject_id = p.subject_id AND c.stay_id = p.stay_id
  JOIN stay_window sw ON sw.stay_id = p.stay_id
  LEFT JOIN mimiciv_icu.d_items di ON di.itemid = p.itemid
  WHERE p.storetime IS NOT NULL
    AND p.storetime >= sw.icu_intime AND p.storetime <= sw.icu_outtime
)
SELECT
  g.subject_id, g.stay_id, g.hour_ts AS charttime_hour,
  e.charttime, e.caregiver_id, e.itemid, e.item_label, e.item_unitname,
  e.item_lownormalvalue, e.item_highnormalvalue,
  e.value, e.valueuom, e.location, e.locationcategory,
  e.orderid, e.linkorderid, e.ordercategoryname, e.ordercategorydescription,
  e.patientweight, e.isopenbag, e.continueinnextdept, e.statusdescription,
  e.originalamount, e.originalrate
FROM hour_grid g
LEFT JOIN events e ON e.stay_id = g.stay_id AND e.charttime_hour = g.hour_ts;

CREATE INDEX ON mimiciv_derived.fisi9t_procedureevents_hourly (stay_id, charttime_hour);
CREATE INDEX ON mimiciv_derived.fisi9t_procedureevents_hourly (subject_id);
