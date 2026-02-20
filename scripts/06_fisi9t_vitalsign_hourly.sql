-- 06_fisi9t_vitalsign_hourly.sql
-- Materialized view: hourly vital signs per stay.

DROP MATERIALIZED VIEW IF EXISTS fisi9t_vitalsign_hourly CASCADE;

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

CREATE INDEX idx_fisi9t_vitals_stay_id_time ON fisi9t_vitalsign_hourly (stay_id, charttime_hour);
CREATE INDEX idx_fisi9t_vitals_subject_id ON fisi9t_vitalsign_hourly (subject_id);
