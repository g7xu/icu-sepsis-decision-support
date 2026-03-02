-- 11_fisi9t_feature_matrix_hourly.sql
-- Materialized view: hourly feature matrix for each patient

DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fisi9t_feature_matrix_hourly CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fisi9t_feature_matrix_hourly AS (

WITH stay_window AS (
         SELECT upp.subject_id,
            upp.stay_id,
            upp.hadm_id,
            upp.anchor_age,
            upp.gender,
            upp.race,
            upp.first_careunit,
            upp.intime,
            upp.outtime,
            date_trunc('hour'::text, upp.intime) AS start_hour,
            date_trunc('hour'::text, upp.outtime - '00:00:01'::interval) AS end_hour
           FROM mimiciv_derived.fisi9t_unique_patient_profile upp
          WHERE upp.intime IS NOT NULL AND upp.outtime IS NOT NULL AND upp.outtime > upp.intime
        ), hour_grid AS (
         SELECT sw.subject_id,
            sw.stay_id,
            sw.hadm_id,
            sw.anchor_age,
            sw.gender,
            sw.race,
            sw.first_careunit,
            sw.intime,
            sw.outtime,
            gs.gs AS charttime_hour
           FROM stay_window sw
             CROSS JOIN LATERAL generate_series(sw.start_hour, sw.end_hour, '01:00:00'::interval) gs(gs)
        )
 SELECT hg.subject_id,
    hg.stay_id,
    hg.hadm_id,
    hg.anchor_age,
    hg.gender,
    hg.race,
    hg.first_careunit,
    hg.intime,
    hg.outtime,
    hg.charttime_hour,
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
    v.glucose,
    ch.bicarbonate,
    ch.calcium,
    ch.sodium,
    ch.potassium,
    co.d_dimer,
    co.fibrinogen,
    co.thrombin,
    co.inr,
    co.pt,
    co.ptt,
    s.hr AS sofa_hr,
    s.pao2fio2ratio_novent,
    s.pao2fio2ratio_vent,
    s.rate_epinephrine,
    s.rate_norepinephrine,
    s.rate_dopamine,
    s.rate_dobutamine,
    s.meanbp_min,
    s.gcs_min,
    s.uo_24hr,
    s.bilirubin_max,
    s.creatinine_max,
    s.platelet_min,
    s.respiration,
    s.coagulation,
    s.liver,
    s.cardiovascular,
    s.cns,
    s.renal,
    s.respiration_24hours,
    s.coagulation_24hours,
    s.liver_24hours,
    s.cardiovascular_24hours,
    s.cns_24hours,
    s.renal_24hours,
    s.sofa_24hours
   FROM hour_grid hg
     LEFT JOIN fisi9t_vitalsign_hourly v ON v.subject_id = hg.subject_id AND v.stay_id = hg.stay_id AND v.charttime_hour = hg.charttime_hour
     LEFT JOIN mimiciv_derived.fisi9t_chemistry_hourly ch ON ch.subject_id = hg.subject_id AND ch.stay_id = hg.stay_id AND ch.charttime_hour = hg.charttime_hour
     LEFT JOIN mimiciv_derived.fisi9t_coagulation_hourly co ON co.subject_id = hg.subject_id AND co.stay_id = hg.stay_id AND co.charttime_hour = hg.charttime_hour
     LEFT JOIN mimiciv_derived.fisi9t_sofa_hourly s ON s.subject_id = hg.subject_id AND s.stay_id = hg.stay_id AND s.charttime_hour = hg.charttime_hour);

CREATE INDEX idx_fisi9t_feature_matrix_hourly_subject_id ON mimiciv_derived.fisi9t_feature_matrix_hourly (subject_id);
CREATE INDEX idx_fisi9t_feature_matrix_hourly_stay_id ON mimiciv_derived.fisi9t_feature_matrix_hourly (stay_id);
CREATE INDEX idx_fisi9t_feature_matrix_hourly_hadm_id ON mimiciv_derived.fisi9t_feature_matrix_hourly (hadm_id);
CREATE INDEX idx_fisi9t_feature_matrix_hourly_charttime_hour ON mimiciv_derived.fisi9t_feature_matrix_hourly (charttime_hour);