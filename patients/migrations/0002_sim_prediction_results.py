"""
Migration 0002: Create sim_prediction_results table.

Stores per-patient per-hour prediction results computed by the pipeline.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('patients', '0001_sim_tables'),
    ]

    operations = [
        migrations.CreateModel(
            name='SimPredictionResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('subject_id', models.IntegerField()),
                ('stay_id', models.IntegerField()),
                ('hadm_id', models.IntegerField()),
                ('prediction_hour', models.IntegerField()),
                ('risk_score', models.FloatField(null=True)),
                ('latent_class', models.IntegerField(null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'simulation\".\"sim_prediction_results',
                'managed': True,
                'indexes': [
                    models.Index(fields=['stay_id', 'prediction_hour'], name='sim_pred_stay_hour_idx'),
                ],
                'unique_together': {('subject_id', 'stay_id', 'hadm_id', 'prediction_hour')},
            },
        ),
    ]
