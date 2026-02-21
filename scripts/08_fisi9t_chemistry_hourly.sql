-- 08_fisi9t_chemistry_hourly.sql
-- Materialized view: hourly chemistry features per stay.

DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fisi9t_chemistry_hourly CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fisi9t_chemistry_hourly AS (
  WITH stay_window AS (
    SELECT
      c.subject_id,
      c.stay_id,
      date_trunc('hour', id.icu_intime) AS start_hour,
      date_trunc('hour', id.icu_outtime) AS end_hour,
      id.icu_intime,
      id.icu_outtime
    FROM mimiciv_derived.fisi9t_unique_patient_profile c
    JOIN mimiciv_derived.icustay_detail id
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
  chem_in_icu AS (
    SELECT
      ch.subject_id,
      c.stay_id,
      ch.charttime,
      date_trunc('hour', ch.charttime + interval '30 minutes') AS charttime_hour,
      ch.bicarbonate,
      ch.calcium,
      ch.sodium,
      ch.potassium
    FROM mimiciv_derived.chemistry ch
    JOIN mimiciv_derived.fisi9t_unique_patient_profile c
      ON c.subject_id = ch.subject_id
    JOIN stay_window sw
      ON sw.subject_id = c.subject_id
     AND sw.stay_id = c.stay_id
    WHERE ch.charttime >= sw.icu_intime
      AND ch.charttime <= sw.icu_outtime
  ),
  chem_hourly AS (
    SELECT
      chem_in_icu.subject_id,
      chem_in_icu.stay_id,
      chem_in_icu.charttime_hour,
      min(chem_in_icu.bicarbonate) FILTER (WHERE chem_in_icu.bicarbonate IS NOT NULL) AS bicarbonate,
      avg(chem_in_icu.calcium) FILTER (WHERE chem_in_icu.calcium IS NOT NULL) AS calcium,
      avg(chem_in_icu.sodium) FILTER (WHERE chem_in_icu.sodium IS NOT NULL) AS sodium,
      max(chem_in_icu.potassium) FILTER (WHERE chem_in_icu.potassium IS NOT NULL) AS potassium
    FROM chem_in_icu
    GROUP BY chem_in_icu.subject_id, chem_in_icu.stay_id, chem_in_icu.charttime_hour
  )
  SELECT
    g.subject_id,
    g.stay_id,
    g.hour_ts AS charttime_hour,
    ch.bicarbonate,
    ch.calcium,
    ch.sodium,
    ch.potassium
  FROM hour_grid g
  LEFT JOIN chem_hourly ch
    ON ch.stay_id = g.stay_id
   AND ch.charttime_hour = g.hour_ts
  ORDER BY g.stay_id, g.hour_ts
);

CREATE INDEX idx_fisi9t_chem_stay_id_time ON mimiciv_derived.fisi9t_chemistry_hourly (stay_id, charttime_hour);
CREATE INDEX idx_fisi9t_chem_subject_id ON mimiciv_derived.fisi9t_chemistry_hourly (subject_id);
