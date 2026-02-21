-- 09_fisi9t_coagulation_hourly.sql
-- Materialized view: hourly coagulation features per stay.
-- Index on (stay_id, charttime_hour) for fast advance-time queries.

DROP VIEW IF EXISTS mimiciv_derived.fisi9t_coagulation_hourly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fisi9t_coagulation_hourly CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fisi9t_coagulation_hourly AS
WITH stay_window AS (
  SELECT
    c.subject_id, c.stay_id,
    date_trunc('hour', id.icu_intime) AS start_hour,
    date_trunc('hour', id.icu_outtime) AS end_hour,
    id.icu_intime, id.icu_outtime
  FROM mimiciv_derived.fisi9t_unique_patient_profile c
  JOIN mimiciv_derived.icustay_detail id ON id.stay_id = c.stay_id
),
hour_grid AS (
  SELECT sw.subject_id, sw.stay_id, gs.gs AS hour_ts
  FROM stay_window sw
  CROSS JOIN LATERAL generate_series(sw.start_hour, sw.end_hour + interval '1 hour', interval '1 hour') gs(gs)
),
coag_in_icu AS (
  SELECT
    co.subject_id, c.stay_id, co.charttime,
    date_trunc('hour', co.charttime + interval '30 minutes') AS charttime_hour,
    co.d_dimer, co.fibrinogen, co.thrombin, co.inr, co.pt, co.ptt
  FROM mimiciv_derived.coagulation co
  JOIN mimiciv_derived.fisi9t_unique_patient_profile c ON c.subject_id = co.subject_id
  JOIN stay_window sw ON sw.subject_id = c.subject_id AND sw.stay_id = c.stay_id
  WHERE co.charttime >= sw.icu_intime AND co.charttime <= sw.icu_outtime
),
coag_hourly AS (
  SELECT
    coag_in_icu.subject_id, coag_in_icu.stay_id, coag_in_icu.charttime_hour,
    (array_agg(coag_in_icu.inr ORDER BY coag_in_icu.charttime DESC) FILTER (WHERE coag_in_icu.inr IS NOT NULL))[1] AS inr,
    (array_agg(coag_in_icu.pt ORDER BY coag_in_icu.charttime DESC) FILTER (WHERE coag_in_icu.pt IS NOT NULL))[1] AS pt,
    (array_agg(coag_in_icu.ptt ORDER BY coag_in_icu.charttime DESC) FILTER (WHERE coag_in_icu.ptt IS NOT NULL))[1] AS ptt,
    (array_agg(coag_in_icu.thrombin ORDER BY coag_in_icu.charttime DESC) FILTER (WHERE coag_in_icu.thrombin IS NOT NULL))[1] AS thrombin,
    max(coag_in_icu.d_dimer) FILTER (WHERE coag_in_icu.d_dimer IS NOT NULL) AS d_dimer,
    min(coag_in_icu.fibrinogen) FILTER (WHERE coag_in_icu.fibrinogen IS NOT NULL) AS fibrinogen
  FROM coag_in_icu
  GROUP BY coag_in_icu.subject_id, coag_in_icu.stay_id, coag_in_icu.charttime_hour
)
SELECT
  g.subject_id, g.stay_id, g.hour_ts AS charttime_hour,
  co.d_dimer, co.fibrinogen, co.thrombin, co.inr, co.pt, co.ptt
FROM hour_grid g
LEFT JOIN coag_hourly co ON co.stay_id = g.stay_id AND co.charttime_hour = g.hour_ts;

CREATE INDEX ON mimiciv_derived.fisi9t_coagulation_hourly (stay_id, charttime_hour);
CREATE INDEX ON mimiciv_derived.fisi9t_coagulation_hourly (subject_id);
