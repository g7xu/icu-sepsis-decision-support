"""
Patient models - mapped to existing MIMIC-IV tables in PostgreSQL.

These models use managed=False since the tables already exist in the database.
"""

from django.db import models


class UniquePatientProfile(models.Model):
    """
    Maps to fisi9t_unique_patient_profile MATERIALIZED VIEW in mimiciv_derived schema.
    
    This is the main patient index containing unique patient stays.
    Django treats materialized views the same as tables for read operations.
    
    Note: This is READ-ONLY. INSERT/UPDATE/DELETE will not work on materialized views.
    """
    # === Primary identifiers ===
    subject_id = models.IntegerField(primary_key=True)  # integer
    stay_id = models.IntegerField()                      # integer
    hadm_id = models.IntegerField()                      # integer
    
    # === Demographics ===
    anchor_age = models.SmallIntegerField(null=True, blank=True)  # smallint
    gender = models.CharField(max_length=1, null=True, blank=True)  # character(1)
    race = models.CharField(max_length=80, null=True, blank=True)   # character varying(80)
    
    # === Care unit info ===
    first_careunit = models.CharField(max_length=255, null=True, blank=True)  # character varying(255)
    last_careunit = models.CharField(max_length=255, null=True, blank=True)   # character varying(255)
    
    # === Timestamps ===
    intime = models.DateTimeField(null=True, blank=True)   # timestamp without time zone
    outtime = models.DateTimeField(null=True, blank=True)  # timestamp without time zone
    
    # === Derived metrics ===
    los = models.FloatField(null=True, blank=True)  # double precision (length of stay in days)

    class Meta:
        managed = False  # Django will NOT create/alter this materialized view
        db_table = 'fisi9t_unique_patient_profile'
        # Note: The actual PK is composite (subject_id, stay_id, hadm_id)
        # Django doesn't support composite PKs natively, so we use subject_id as primary
        # For lookups, always filter by all three fields

    def __str__(self):
        return f"Patient {self.subject_id} - Stay {self.stay_id}"

    @property
    def composite_key(self):
        """Returns the composite key tuple for this patient stay."""
        return (self.subject_id, self.stay_id, self.hadm_id)


class VitalsignHourly(models.Model):
    """
    Maps to fisi9t_vitalsign_hourly MATERIALIZED VIEW in mimiciv_derived schema.
    
    Contains hourly vital sign measurements for patients.
    Linked to UniquePatientProfile via (subject_id, stay_id).
    """
    # === Identifiers ===
    subject_id = models.IntegerField()   # integer
    stay_id = models.IntegerField()      # integer
    charttime_hour = models.DateTimeField()  # timestamp without time zone
    
    # === Cardiovascular vitals ===
    heart_rate = models.FloatField(null=True, blank=True)  # double precision
    sbp = models.FloatField(null=True, blank=True)         # systolic BP - double precision
    dbp = models.FloatField(null=True, blank=True)         # diastolic BP - double precision
    mbp = models.FloatField(null=True, blank=True)         # mean BP - double precision
    
    # === Non-invasive BP ===
    sbp_ni = models.FloatField(null=True, blank=True)      # double precision
    dbp_ni = models.FloatField(null=True, blank=True)      # double precision
    mbp_ni = models.FloatField(null=True, blank=True)      # double precision
    
    # === Respiratory & other vitals ===
    resp_rate = models.FloatField(null=True, blank=True)   # double precision
    temperature = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)  # numeric
    temperature_site = models.TextField(null=True, blank=True)  # text
    spo2 = models.FloatField(null=True, blank=True)        # double precision
    glucose = models.FloatField(null=True, blank=True)     # double precision

    class Meta:
        managed = False
        db_table = 'fisi9t_vitalsign_hourly'

    def __str__(self):
        return f"Vitals for {self.subject_id} at {self.charttime_hour}"


class ProcedureeventsHourly(models.Model):
    """
    Maps to fisi9t_procedureevents_hourly MATERIALIZED VIEW in mimiciv_derived schema.
    
    Contains hourly procedure events for patients.
    Linked to UniquePatientProfile via (subject_id, stay_id).
    """
    # === Identifiers ===
    subject_id = models.IntegerField()       # integer
    stay_id = models.IntegerField()          # integer
    charttime_hour = models.DateTimeField(null=True, blank=True)  # timestamp without time zone
    charttime = models.DateTimeField(null=True, blank=True)       # timestamp without time zone
    
    # === Caregiver & item info ===
    caregiver_id = models.IntegerField(null=True, blank=True)     # integer
    itemid = models.IntegerField(null=True, blank=True)           # integer
    item_label = models.CharField(max_length=100, null=True, blank=True)     # character varying(100)
    item_unitname = models.CharField(max_length=50, null=True, blank=True)   # character varying(50)
    item_lownormalvalue = models.FloatField(null=True, blank=True)   # double precision
    item_highnormalvalue = models.FloatField(null=True, blank=True)  # double precision
    
    # === Values ===
    value = models.FloatField(null=True, blank=True)              # double precision
    valueuom = models.CharField(max_length=20, null=True, blank=True)  # character varying(20)
    
    # === Location info ===
    location = models.CharField(max_length=100, null=True, blank=True)        # character varying(100)
    locationcategory = models.CharField(max_length=50, null=True, blank=True) # character varying(50)
    
    # === Order info ===
    orderid = models.IntegerField(null=True, blank=True)          # integer
    linkorderid = models.IntegerField(null=True, blank=True)      # integer
    ordercategoryname = models.CharField(max_length=50, null=True, blank=True)        # character varying(50)
    ordercategorydescription = models.CharField(max_length=30, null=True, blank=True) # character varying(30)
    
    # === Additional fields ===
    patientweight = models.FloatField(null=True, blank=True)      # double precision
    isopenbag = models.SmallIntegerField(null=True, blank=True)   # smallint
    continueinnextdept = models.SmallIntegerField(null=True, blank=True)  # smallint
    statusdescription = models.CharField(max_length=20, null=True, blank=True)  # character varying(20)
    originalamount = models.FloatField(null=True, blank=True)     # double precision
    originalrate = models.FloatField(null=True, blank=True)       # double precision

    class Meta:
        managed = False
        db_table = 'fisi9t_procedureevents_hourly'

    def __str__(self):
        return f"Procedure for {self.subject_id} - {self.item_label}"
