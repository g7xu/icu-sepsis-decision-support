-- 10_fisi9t_sofa_hourly.sql
-- Materialized view: hourly SOFA entries per stay

DROP MATERIALIZED VIEW IF EXISTS mimiciv_derived.fisi9t_sofa_hourly CASCADE;

CREATE MATERIALIZED VIEW mimiciv_derived.fisi9t_sofa_hourly AS (
    SELECT f.subject_id,
    s.stay_id,
    s.hr,
    s.starttime AS charttime_hour,
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
    FROM sofa s JOIN fisi9t_unique_patient_profile f ON s.stay_id = f.stay_id
);

CREATE INDEX idx_fisi9t_sofa_hourly_subject_id ON mimiciv_derived.fisi9t_sofa_hourly (subject_id);
CREATE INDEX idx_fisi9t_sofa_hourly_stay_id ON mimiciv_derived.fisi9t_sofa_hourly (stay_id);
CREATE INDEX idx_fisi9t_sofa_hourly_charttime_hour ON mimiciv_derived.fisi9t_sofa_hourly (charttime_hour);