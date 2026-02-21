"""
Patient models.

Unmanaged models (managed=False) map to existing MIMIC-IV materialized views
and are kept as a read-only fallback for the services.py dynamic table resolution.

Managed Sim* models (managed=True) map to Django-owned simulation.* tables
populated by the advance_time pipeline at runtime. These are the primary data
source for the patient list, detail views, and prediction API after migration.
"""

from django.db import models


# =============================================================================
# Unmanaged models — read-only fallback to fisi9t_* materialized views
# =============================================================================

class UniquePatientProfile(models.Model):
    """
    Maps to fisi9t_unique_patient_profile MATERIALIZED VIEW in mimiciv_derived schema.
    Read-only fallback — sim_patient is the primary source after migration.
    """
    subject_id = models.IntegerField(primary_key=True)
    stay_id = models.IntegerField()
    hadm_id = models.IntegerField()
    anchor_age = models.SmallIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=1, null=True, blank=True)
    race = models.CharField(max_length=80, null=True, blank=True)
    first_careunit = models.CharField(max_length=255, null=True, blank=True)
    last_careunit = models.CharField(max_length=255, null=True, blank=True)
    intime = models.DateTimeField(null=True, blank=True)
    outtime = models.DateTimeField(null=True, blank=True)
    los = models.FloatField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'fisi9t_unique_patient_profile'

    def __str__(self):
        return f"Patient {self.subject_id} - Stay {self.stay_id}"

    @property
    def composite_key(self):
        return (self.subject_id, self.stay_id, self.hadm_id)


class VitalsignHourly(models.Model):
    """
    Maps to fisi9t_vitalsign_hourly MATERIALIZED VIEW. Read-only fallback.
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField()
    charttime_hour = models.DateTimeField()
    heart_rate = models.FloatField(null=True, blank=True)
    sbp = models.FloatField(null=True, blank=True)
    dbp = models.FloatField(null=True, blank=True)
    mbp = models.FloatField(null=True, blank=True)
    sbp_ni = models.FloatField(null=True, blank=True)
    dbp_ni = models.FloatField(null=True, blank=True)
    mbp_ni = models.FloatField(null=True, blank=True)
    resp_rate = models.FloatField(null=True, blank=True)
    temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    temperature_site = models.TextField(null=True, blank=True)
    spo2 = models.FloatField(null=True, blank=True)
    glucose = models.FloatField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'fisi9t_vitalsign_hourly'

    def __str__(self):
        return f"Vitals for {self.subject_id} at {self.charttime_hour}"


class ProcedureeventsHourly(models.Model):
    """
    Maps to fisi9t_procedureevents_hourly MATERIALIZED VIEW. Read-only fallback.
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField()
    charttime_hour = models.DateTimeField(null=True, blank=True)
    charttime = models.DateTimeField(null=True, blank=True)
    caregiver_id = models.IntegerField(null=True, blank=True)
    itemid = models.IntegerField(null=True, blank=True)
    item_label = models.CharField(max_length=100, null=True, blank=True)
    item_unitname = models.CharField(max_length=50, null=True, blank=True)
    item_lownormalvalue = models.FloatField(null=True, blank=True)
    item_highnormalvalue = models.FloatField(null=True, blank=True)
    value = models.FloatField(null=True, blank=True)
    valueuom = models.CharField(max_length=20, null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True)
    locationcategory = models.CharField(max_length=50, null=True, blank=True)
    orderid = models.IntegerField(null=True, blank=True)
    linkorderid = models.IntegerField(null=True, blank=True)
    ordercategoryname = models.CharField(max_length=50, null=True, blank=True)
    ordercategorydescription = models.CharField(max_length=30, null=True, blank=True)
    patientweight = models.FloatField(null=True, blank=True)
    isopenbag = models.SmallIntegerField(null=True, blank=True)
    continueinnextdept = models.SmallIntegerField(null=True, blank=True)
    statusdescription = models.CharField(max_length=20, null=True, blank=True)
    originalamount = models.FloatField(null=True, blank=True)
    originalrate = models.FloatField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'fisi9t_procedureevents_hourly'

    def __str__(self):
        return f"Procedure for {self.subject_id} - {self.item_label}"


# =============================================================================
# Managed Sim* models — live simulation state (simulation.* schema on RDS)
# Created by: python manage.py migrate
# Schema trick: 'simulation\".\"table_name' makes Django use schema-qualified name
# =============================================================================

class SimPatient(models.Model):
    """
    Live admitted patients in the current simulation.
    Populated by pipeline.advance_hour() when a patient's intime hour is reached.
    Deleted by pipeline.rewind_hour() when rewinding past their admission hour.
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField(unique=True)
    hadm_id = models.IntegerField()
    anchor_age = models.SmallIntegerField(null=True)
    gender = models.CharField(max_length=1, null=True)
    race = models.CharField(max_length=80, null=True)
    first_careunit = models.CharField(max_length=255, null=True)
    last_careunit = models.CharField(max_length=255, null=True)
    intime = models.DateTimeField(null=True)
    outtime = models.DateTimeField(null=True)
    los = models.FloatField(null=True)

    class Meta:
        managed = True
        db_table = 'simulation\".\"sim_patient'
        indexes = [
            models.Index(fields=['subject_id']),
            models.Index(fields=['stay_id']),
        ]

    def __str__(self):
        return f"SimPatient {self.subject_id} - Stay {self.stay_id}"

    @property
    def composite_key(self):
        return (self.subject_id, self.stay_id, self.hadm_id)


class SimVitalsignHourly(models.Model):
    """
    Hourly vitals inserted one hour at a time by the pipeline.
    Source: mimiciv_derived.vitalsign (hourly AVG per stay).
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField()
    charttime_hour = models.DateTimeField()
    heart_rate = models.FloatField(null=True)
    sbp = models.FloatField(null=True)
    dbp = models.FloatField(null=True)
    mbp = models.FloatField(null=True)
    sbp_ni = models.FloatField(null=True)
    dbp_ni = models.FloatField(null=True)
    mbp_ni = models.FloatField(null=True)
    resp_rate = models.FloatField(null=True)
    temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    temperature_site = models.TextField(null=True)
    spo2 = models.FloatField(null=True)
    glucose = models.FloatField(null=True)

    class Meta:
        managed = True
        db_table = 'simulation\".\"sim_vitalsign_hourly'
        indexes = [
            models.Index(fields=['stay_id', 'charttime_hour']),
        ]

    def __str__(self):
        return f"SimVitals stay={self.stay_id} at {self.charttime_hour}"


class SimProcedureeventsHourly(models.Model):
    """
    Hourly procedure events inserted by the pipeline.
    Source: mimiciv_icu.procedureevents + mimiciv_icu.d_items.
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField()
    charttime_hour = models.DateTimeField(null=True)
    charttime = models.DateTimeField(null=True)
    caregiver_id = models.IntegerField(null=True)
    itemid = models.IntegerField(null=True)
    item_label = models.CharField(max_length=100, null=True)
    item_unitname = models.CharField(max_length=50, null=True)
    item_lownormalvalue = models.FloatField(null=True)
    item_highnormalvalue = models.FloatField(null=True)
    value = models.FloatField(null=True)
    valueuom = models.CharField(max_length=20, null=True)
    location = models.CharField(max_length=100, null=True)
    locationcategory = models.CharField(max_length=50, null=True)
    orderid = models.IntegerField(null=True)
    linkorderid = models.IntegerField(null=True)
    ordercategoryname = models.CharField(max_length=50, null=True)
    ordercategorydescription = models.CharField(max_length=30, null=True)
    patientweight = models.FloatField(null=True)
    isopenbag = models.SmallIntegerField(null=True)
    continueinnextdept = models.SmallIntegerField(null=True)
    statusdescription = models.CharField(max_length=20, null=True)
    originalamount = models.FloatField(null=True)
    originalrate = models.FloatField(null=True)

    class Meta:
        managed = True
        db_table = 'simulation\".\"sim_procedureevents_hourly'
        indexes = [
            models.Index(fields=['stay_id', 'charttime_hour']),
        ]

    def __str__(self):
        return f"SimProc stay={self.stay_id} {self.item_label} at {self.charttime_hour}"


class SimChemistryHourly(models.Model):
    """
    Hourly chemistry labs inserted by the pipeline.
    Source: mimiciv_derived.chemistry (hourly MIN/AVG/MAX per stay).
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField()
    charttime_hour = models.DateTimeField()
    bicarbonate = models.FloatField(null=True)
    calcium = models.FloatField(null=True)
    sodium = models.FloatField(null=True)
    potassium = models.FloatField(null=True)

    class Meta:
        managed = True
        db_table = 'simulation\".\"sim_chemistry_hourly'
        indexes = [
            models.Index(fields=['stay_id', 'charttime_hour']),
        ]

    def __str__(self):
        return f"SimChem stay={self.stay_id} at {self.charttime_hour}"


class SimCoagulationHourly(models.Model):
    """
    Hourly coagulation labs inserted by the pipeline.
    Source: mimiciv_derived.coagulation.
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField()
    charttime_hour = models.DateTimeField()
    d_dimer = models.FloatField(null=True)
    fibrinogen = models.FloatField(null=True)
    thrombin = models.FloatField(null=True)
    inr = models.FloatField(null=True)
    pt = models.FloatField(null=True)
    ptt = models.FloatField(null=True)

    class Meta:
        managed = True
        db_table = 'simulation\".\"sim_coagulation_hourly'
        indexes = [
            models.Index(fields=['stay_id', 'charttime_hour']),
        ]

    def __str__(self):
        return f"SimCoag stay={self.stay_id} at {self.charttime_hour}"


class SimSofaHourly(models.Model):
    """
    Hourly SOFA scores inserted by the pipeline.
    Source: mimiciv_derived.sofa_hourly (skipped gracefully if not present).
    """
    subject_id = models.IntegerField()
    stay_id = models.IntegerField()
    charttime_hour = models.DateTimeField()
    sofa_24hours = models.IntegerField(null=True)
    respiration = models.IntegerField(null=True)
    coagulation = models.IntegerField(null=True)
    liver = models.IntegerField(null=True)
    cardiovascular = models.IntegerField(null=True)
    cns = models.IntegerField(null=True)
    renal = models.IntegerField(null=True)

    class Meta:
        managed = True
        db_table = 'simulation\".\"sim_sofa_hourly'
        indexes = [
            models.Index(fields=['stay_id', 'charttime_hour']),
        ]

    def __str__(self):
        return f"SimSOFA stay={self.stay_id} score={self.sofa_24hours} at {self.charttime_hour}"
