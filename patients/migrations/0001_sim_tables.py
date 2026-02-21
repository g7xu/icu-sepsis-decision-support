"""
Migration 0001: Create simulation schema and all sim_* tables.

Run with: python manage.py migrate
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        # Create the simulation schema first
        migrations.RunSQL(
            sql="CREATE SCHEMA IF NOT EXISTS simulation",
            reverse_sql="DROP SCHEMA IF EXISTS simulation CASCADE",
        ),

        migrations.CreateModel(
            name='SimPatient',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_id', models.IntegerField()),
                ('stay_id', models.IntegerField(unique=True)),
                ('hadm_id', models.IntegerField()),
                ('anchor_age', models.SmallIntegerField(null=True)),
                ('gender', models.CharField(max_length=1, null=True)),
                ('race', models.CharField(max_length=80, null=True)),
                ('first_careunit', models.CharField(max_length=255, null=True)),
                ('last_careunit', models.CharField(max_length=255, null=True)),
                ('intime', models.DateTimeField(null=True)),
                ('outtime', models.DateTimeField(null=True)),
                ('los', models.FloatField(null=True)),
            ],
            options={
                'db_table': 'simulation\".\"sim_patient',
                'managed': True,
                'indexes': [
                    models.Index(fields=['subject_id'], name='sim_patient_subject_idx'),
                    models.Index(fields=['stay_id'], name='sim_patient_stay_idx'),
                ],
            },
        ),

        migrations.CreateModel(
            name='SimVitalsignHourly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_id', models.IntegerField()),
                ('stay_id', models.IntegerField()),
                ('charttime_hour', models.DateTimeField()),
                ('heart_rate', models.FloatField(null=True)),
                ('sbp', models.FloatField(null=True)),
                ('dbp', models.FloatField(null=True)),
                ('mbp', models.FloatField(null=True)),
                ('sbp_ni', models.FloatField(null=True)),
                ('dbp_ni', models.FloatField(null=True)),
                ('mbp_ni', models.FloatField(null=True)),
                ('resp_rate', models.FloatField(null=True)),
                ('temperature', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('temperature_site', models.TextField(null=True)),
                ('spo2', models.FloatField(null=True)),
                ('glucose', models.FloatField(null=True)),
            ],
            options={
                'db_table': 'simulation\".\"sim_vitalsign_hourly',
                'managed': True,
                'indexes': [
                    models.Index(fields=['stay_id', 'charttime_hour'], name='sim_vitals_stay_hour_idx'),
                ],
            },
        ),

        migrations.CreateModel(
            name='SimProcedureeventsHourly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_id', models.IntegerField()),
                ('stay_id', models.IntegerField()),
                ('charttime_hour', models.DateTimeField(null=True)),
                ('charttime', models.DateTimeField(null=True)),
                ('caregiver_id', models.IntegerField(null=True)),
                ('itemid', models.IntegerField(null=True)),
                ('item_label', models.CharField(max_length=100, null=True)),
                ('item_unitname', models.CharField(max_length=50, null=True)),
                ('item_lownormalvalue', models.FloatField(null=True)),
                ('item_highnormalvalue', models.FloatField(null=True)),
                ('value', models.FloatField(null=True)),
                ('valueuom', models.CharField(max_length=20, null=True)),
                ('location', models.CharField(max_length=100, null=True)),
                ('locationcategory', models.CharField(max_length=50, null=True)),
                ('orderid', models.IntegerField(null=True)),
                ('linkorderid', models.IntegerField(null=True)),
                ('ordercategoryname', models.CharField(max_length=50, null=True)),
                ('ordercategorydescription', models.CharField(max_length=30, null=True)),
                ('patientweight', models.FloatField(null=True)),
                ('isopenbag', models.SmallIntegerField(null=True)),
                ('continueinnextdept', models.SmallIntegerField(null=True)),
                ('statusdescription', models.CharField(max_length=20, null=True)),
                ('originalamount', models.FloatField(null=True)),
                ('originalrate', models.FloatField(null=True)),
            ],
            options={
                'db_table': 'simulation\".\"sim_procedureevents_hourly',
                'managed': True,
                'indexes': [
                    models.Index(fields=['stay_id', 'charttime_hour'], name='sim_proc_stay_hour_idx'),
                ],
            },
        ),

        migrations.CreateModel(
            name='SimChemistryHourly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_id', models.IntegerField()),
                ('stay_id', models.IntegerField()),
                ('charttime_hour', models.DateTimeField()),
                ('bicarbonate', models.FloatField(null=True)),
                ('calcium', models.FloatField(null=True)),
                ('sodium', models.FloatField(null=True)),
                ('potassium', models.FloatField(null=True)),
            ],
            options={
                'db_table': 'simulation\".\"sim_chemistry_hourly',
                'managed': True,
                'indexes': [
                    models.Index(fields=['stay_id', 'charttime_hour'], name='sim_chem_stay_hour_idx'),
                ],
            },
        ),

        migrations.CreateModel(
            name='SimCoagulationHourly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_id', models.IntegerField()),
                ('stay_id', models.IntegerField()),
                ('charttime_hour', models.DateTimeField()),
                ('d_dimer', models.FloatField(null=True)),
                ('fibrinogen', models.FloatField(null=True)),
                ('thrombin', models.FloatField(null=True)),
                ('inr', models.FloatField(null=True)),
                ('pt', models.FloatField(null=True)),
                ('ptt', models.FloatField(null=True)),
            ],
            options={
                'db_table': 'simulation\".\"sim_coagulation_hourly',
                'managed': True,
                'indexes': [
                    models.Index(fields=['stay_id', 'charttime_hour'], name='sim_coag_stay_hour_idx'),
                ],
            },
        ),

        migrations.CreateModel(
            name='SimSofaHourly',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_id', models.IntegerField()),
                ('stay_id', models.IntegerField()),
                ('charttime_hour', models.DateTimeField()),
                ('sofa_24hours', models.IntegerField(null=True)),
                ('respiration', models.IntegerField(null=True)),
                ('coagulation', models.IntegerField(null=True)),
                ('liver', models.IntegerField(null=True)),
                ('cardiovascular', models.IntegerField(null=True)),
                ('cns', models.IntegerField(null=True)),
                ('renal', models.IntegerField(null=True)),
            ],
            options={
                'db_table': 'simulation\".\"sim_sofa_hourly',
                'managed': True,
                'indexes': [
                    models.Index(fields=['stay_id', 'charttime_hour'], name='sim_sofa_stay_hour_idx'),
                ],
            },
        ),
    ]
