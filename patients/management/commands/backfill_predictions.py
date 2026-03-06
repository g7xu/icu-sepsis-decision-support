"""
Management command: backfill_predictions

Computes and persists predictions for all patients in sim_patient, only for
hours where vitals data actually exists in sim_vitalsign_hourly.

Usage:
    python manage.py backfill_predictions

Safe to re-run: skips hours that already have predictions (use --force to
truncate and recompute all).
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from patients.models import SimPatient, SimVitalsignHourly, SimPredictionResult
from patients.services import get_prediction
from patients.utils import prediction_as_of_iso


class Command(BaseCommand):
    help = (
        "Backfill sim_prediction_results for all patients using existing sim_* data. "
        "Only computes predictions for hours where vitals data exists."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Truncate sim_prediction_results before backfilling.',
        )

    def handle(self, *args, **options):
        if options['force']:
            count = SimPredictionResult.objects.count()
            SimPredictionResult.objects.all().delete()
            self.stdout.write(f"Truncated {count} existing prediction rows.")

        patients = list(SimPatient.objects.all().values_list(
            'subject_id', 'stay_id', 'hadm_id', named=False,
        ))
        self.stdout.write(f"Found {len(patients)} patients in sim_patient.")

        # Build map: stay_id → set of hours that have vitals data
        stay_hours = defaultdict(set)
        for stay_id, ct in SimVitalsignHourly.objects.values_list('stay_id', 'charttime_hour'):
            stay_hours[stay_id].add(ct.hour)

        created = 0
        skipped = 0
        no_data = 0

        for subject_id, stay_id, hadm_id in patients:
            hours = sorted(stay_hours.get(stay_id, set()))
            if not hours:
                no_data += 1
                continue

            for hour in hours:
                # Skip if already exists
                if SimPredictionResult.objects.filter(
                    subject_id=subject_id,
                    stay_id=stay_id,
                    hadm_id=hadm_id,
                    prediction_hour=hour,
                ).exists():
                    skipped += 1
                    continue

                as_of_iso = prediction_as_of_iso(hour)
                if not as_of_iso:
                    continue
                as_of = parse_datetime(as_of_iso)

                result = get_prediction(
                    subject_id=subject_id,
                    stay_id=stay_id,
                    hadm_id=hadm_id,
                    as_of=as_of,
                    window_hours=24,
                )

                risk_score = result.get('risk_score') if result.get('ok') else None
                latent_class = result.get('latent_class') if result.get('ok') else None

                SimPredictionResult.objects.create(
                    subject_id=subject_id,
                    stay_id=stay_id,
                    hadm_id=hadm_id,
                    prediction_hour=hour,
                    risk_score=risk_score,
                    latent_class=latent_class,
                )
                created += 1

            self.stdout.write(f"  stay {stay_id}: {len(hours)} hours with data")

        self.stdout.write(self.style.SUCCESS(
            f"\nBackfill complete: {created} created, {skipped} skipped, {no_data} patients with no vitals data."
        ))
