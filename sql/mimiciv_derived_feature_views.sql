-- Reference: derived feature materialized views and hourly feature tables
-- This file is for documentation/reproducibility of the model input sources.
-- It is not executed automatically by the API.
--
-- Notes:
-- - These statements assume the relevant MIMIC-IV tables/views exist.
-- - Consider creating them under a dedicated schema (e.g. mimiciv_derived) and
--   refreshing materialized views as part of an ETL pipeline.

-- 1) first_icu_stay
CREATE MATERIALIZED VIEW first_icu_stay AS (
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

-- 2) fis_icd9
CREATE MATERIALIZED VIEW fis_icd9 AS (
  WITH icd9_only_patients AS (
    SELECT diagnoses_icd.subject_id
    FROM diagnoses_icd
    GROUP BY diagnoses_icd.subject_id
    HAVING count(DISTINCT diagnoses_icd.icd_version) = 1
       AND max(diagnoses_icd.icd_version) = 9
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
  FROM first_icu_stay f
  JOIN icd9_only_patients p
    ON p.subject_id = f.subject_id
  JOIN diagnoses_icd d
    ON d.subject_id = f.subject_id
   AND d.hadm_id = f.hadm_id
  WHERE d.icd_version = 9
);

-- 3) fis_icd9_titled
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

-- 4) fisi9t_profile
CREATE MATERIALIZED VIEW fisi9t_profile AS (
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
  FROM fis_icd9_titled f
  JOIN age a
    ON a.subject_id = f.subject_id
   AND a.hadm_id = f.hadm_id
  JOIN icustay_detail id
    ON id.stay_id = f.stay_id
);

-- 5) fisi9t_unique_patient_profile
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

-- 6) fisi9t_vitalsign_hourly
CREATE MATERIALIZED VIEW fisi9t_vitalsign_hourly AS (
  WITH cohort AS (
    SELECT DISTINCT
      fisi9t_unique_patient_profile.subject_id,
      fisi9t_unique_patient_profile.stay_id
    FROM fisi9t_unique_patient_profile
  ),
  base AS (
    SELECT
      v.subject_id,
      v.stay_id,
      v.charttime,
      v.heart_rate,
      v.sbp,
      v.dbp,
      v.mbp,
      v.sbp_ni,
      v.dbp_ni,
      v.mbp_ni,
      v.resp_rate,
      v.temperature,
      v.temperature_site,
      v.spo2,
      v.glucose
    FROM vitalsign v
    JOIN cohort c
      ON c.subject_id = v.subject_id
     AND c.stay_id = v.stay_id
  ),
  marked AS (
    SELECT
      b.*,
      CASE
        WHEN lag(b.charttime) OVER (PARTITION BY b.stay_id ORDER BY b.charttime) IS NULL THEN 1
        WHEN (b.charttime - lag(b.charttime) OVER (PARTITION BY b.stay_id ORDER BY b.charttime)) > '00:15:00'::interval THEN 1
        ELSE 0
      END AS is_new_cluster
    FROM base b
  ),
  clustered AS (
    SELECT
      m.*,
      sum(m.is_new_cluster) OVER (PARTITION BY m.stay_id ORDER BY m.charttime) AS cluster_id
    FROM marked m
  ),
  cluster_agg AS (
    SELECT
      c.subject_id,
      c.stay_id,
      c.cluster_id,
      min(c.charttime) AS cluster_time,
      avg(c.heart_rate) FILTER (WHERE c.heart_rate IS NOT NULL) AS heart_rate,
      avg(c.sbp) FILTER (WHERE c.sbp IS NOT NULL) AS sbp,
      avg(c.dbp) FILTER (WHERE c.dbp IS NOT NULL) AS dbp,
      avg(c.mbp) FILTER (WHERE c.mbp IS NOT NULL) AS mbp,
      avg(c.sbp_ni) FILTER (WHERE c.sbp_ni IS NOT NULL) AS sbp_ni,
      avg(c.dbp_ni) FILTER (WHERE c.dbp_ni IS NOT NULL) AS dbp_ni,
      avg(c.mbp_ni) FILTER (WHERE c.mbp_ni IS NOT NULL) AS mbp_ni,
      avg(c.resp_rate) FILTER (WHERE c.resp_rate IS NOT NULL) AS resp_rate,
      avg(c.temperature) FILTER (WHERE c.temperature IS NOT NULL) AS temperature,
      (array_agg(c.temperature_site ORDER BY c.charttime) FILTER (WHERE c.temperature_site IS NOT NULL))[1] AS temperature_site,
      avg(c.spo2) FILTER (WHERE c.spo2 IS NOT NULL) AS spo2,
      avg(c.glucose) FILTER (WHERE c.glucose IS NOT NULL) AS glucose
    FROM clustered c
    GROUP BY c.subject_id, c.stay_id, c.cluster_id
  ),
  cluster_to_hour AS (
    SELECT
      ca.subject_id,
      ca.stay_id,
      date_trunc('hour', ca.cluster_time + interval '30 minutes') AS hour_ts,
      ca.heart_rate,
      ca.sbp,
      ca.dbp,
      ca.mbp,
      ca.sbp_ni,
      ca.dbp_ni,
      ca.mbp_ni,
      ca.resp_rate,
      ca.temperature,
      ca.temperature_site,
      ca.spo2,
      ca.glucose,
      ca.cluster_time
    FROM cluster_agg ca
  ),
  hourly_obs AS (
    SELECT
      cth.subject_id,
      cth.stay_id,
      cth.hour_ts,
      avg(cth.heart_rate) FILTER (WHERE cth.heart_rate IS NOT NULL) AS heart_rate,
      avg(cth.sbp) FILTER (WHERE cth.sbp IS NOT NULL) AS sbp,
      avg(cth.dbp) FILTER (WHERE cth.dbp IS NOT NULL) AS dbp,
      avg(cth.mbp) FILTER (WHERE cth.mbp IS NOT NULL) AS mbp,
      avg(cth.sbp_ni) FILTER (WHERE cth.sbp_ni IS NOT NULL) AS sbp_ni,
      avg(cth.dbp_ni) FILTER (WHERE cth.dbp_ni IS NOT NULL) AS dbp_ni,
      avg(cth.mbp_ni) FILTER (WHERE cth.mbp_ni IS NOT NULL) AS mbp_ni,
      avg(cth.resp_rate) FILTER (WHERE cth.resp_rate IS NOT NULL) AS resp_rate,
      avg(cth.temperature) FILTER (WHERE cth.temperature IS NOT NULL) AS temperature,
      (array_agg(cth.temperature_site ORDER BY cth.cluster_time) FILTER (WHERE cth.temperature_site IS NOT NULL))[1] AS temperature_site,
      avg(cth.spo2) FILTER (WHERE cth.spo2 IS NOT NULL) AS spo2,
      avg(cth.glucose) FILTER (WHERE cth.glucose IS NOT NULL) AS glucose
    FROM cluster_to_hour cth
    GROUP BY cth.subject_id, cth.stay_id, cth.hour_ts
  ),
  stay_window AS (
    SELECT
      c.subject_id,
      c.stay_id,
      date_trunc('hour', id.icu_intime) AS start_hour,
      date_trunc('hour', id.icu_outtime) AS end_hour
    FROM cohort c
    JOIN icustay_detail id
      ON id.stay_id = c.stay_id
  ),
  hour_grid AS (
    SELECT
      sw.subject_id,
      sw.stay_id,
      gs.gs AS hour_ts
    FROM stay_window sw
    CROSS JOIN LATERAL generate_series(
      sw.start_hour,
      sw.end_hour + interval '1 hour',
      interval '1 hour'
    ) gs(gs)
  )
  SELECT
    g.subject_id,
    g.stay_id,
    g.hour_ts AS charttime_hour,
    h.heart_rate,
    h.sbp,
    h.dbp,
    h.mbp,
    h.sbp_ni,
    h.dbp_ni,
    h.mbp_ni,
    h.resp_rate,
    h.temperature,
    h.temperature_site,
    h.spo2,
    h.glucose
  FROM hour_grid g
  LEFT JOIN hourly_obs h
    ON h.stay_id = g.stay_id
   AND h.hour_ts = g.hour_ts
  ORDER BY g.stay_id, g.hour_ts
);

-- 7) fisi9t_procedureevents_hourly
CREATE MATERIALIZED VIEW fisi9t_procedureevents_hourly AS (
  WITH cohort AS (
    SELECT DISTINCT
      upp.subject_id,
      upp.stay_id
    FROM fisi9t_unique_patient_profile upp
  ),
  stay_window AS (
    SELECT
      c.subject_id,
      c.stay_id,
      date_trunc('hour', id.icu_intime) AS start_hour,
      date_trunc('hour', id.icu_outtime) AS end_hour,
      id.icu_intime,
      id.icu_outtime
    FROM cohort c
    JOIN icustay_detail id
      ON id.stay_id = c.stay_id
  ),
  hour_grid AS (
    SELECT
      sw.subject_id,
      sw.stay_id,
      gs.gs AS hour_ts
    FROM stay_window sw
    CROSS JOIN LATERAL generate_series(
      sw.start_hour,
      sw.end_hour + interval '1 hour',
      interval '1 hour'
    ) gs(gs)
  ),
  events AS (
    SELECT
      p.subject_id,
      p.stay_id,
      p.storetime AS charttime,
      date_trunc('hour', p.storetime + interval '30 minutes') AS charttime_hour,
      p.caregiver_id,
      p.itemid,
      p.value,
      p.valueuom,
      p.location,
      p.locationcategory,
      p.orderid,
      p.linkorderid,
      p.ordercategoryname,
      p.ordercategorydescription,
      p.patientweight,
      p.isopenbag,
      p.continueinnextdept,
      p.statusdescription,
      p.originalamount,
      p.originalrate,
      di.label AS item_label,
      di.unitname AS item_unitname,
      di.lownormalvalue AS item_lownormalvalue,
      di.highnormalvalue AS item_highnormalvalue
    FROM procedureevents p
    JOIN cohort c
      ON c.subject_id = p.subject_id
     AND c.stay_id = p.stay_id
    JOIN stay_window sw
      ON sw.stay_id = p.stay_id
    LEFT JOIN d_items di
      ON di.itemid = p.itemid
    WHERE p.storetime IS NOT NULL
      AND p.storetime >= sw.icu_intime
      AND p.storetime <= sw.icu_outtime
  )
  SELECT
    g.subject_id,
    g.stay_id,
    g.hour_ts AS charttime_hour,
    e.charttime,
    e.caregiver_id,
    e.itemid,
    e.item_label,
    e.item_unitname,
    e.item_lownormalvalue,
    e.item_highnormalvalue,
    e.value,
    e.valueuom,
    e.location,
    e.locationcategory,
    e.orderid,
    e.linkorderid,
    e.ordercategoryname,
    e.ordercategorydescription,
    e.patientweight,
    e.isopenbag,
    e.continueinnextdept,
    e.statusdescription,
    e.originalamount,
    e.originalrate
  FROM hour_grid g
  LEFT JOIN events e
    ON e.stay_id = g.stay_id
   AND e.charttime_hour = g.hour_ts
  ORDER BY g.stay_id, g.hour_ts, e.charttime, e.itemid, e.orderid
);

-- 8) Chemistry + coagulation hourly (you shared this as a query; consider materializing it)
-- Suggested name:
--   CREATE MATERIALIZED VIEW fisi9t_chemcoag_hourly AS ( ... )
-- If you do, the API can auto-detect and include it in the wide table.
