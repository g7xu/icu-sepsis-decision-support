"""
Django management command to wipe model IO data from S3.

Use before a fresh simulation run to avoid stale feature vectors from previous runs.
Also useful when the bucket gets clogged.

Usage:
  python manage.py clear_model_s3
  python manage.py clear_model_s3 --prefix model-io/patients/13129329_32482524_23992308  # one patient only
"""
import os
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Delete model IO objects (features, predictions, io) from S3 to start fresh."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefix",
            type=str,
            default=None,
            help="S3 prefix to delete (default: full MODEL_S3_PREFIX, e.g. model-io/)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List objects that would be deleted without deleting them",
        )

    def handle(self, *args, **options):
        try:
            import boto3
        except ImportError:
            self.stderr.write("boto3 not installed. Run: pip install boto3")
            return

        bucket = getattr(settings, "MODEL_S3_BUCKET", "") or ""
        prefix = options.get("prefix") or (getattr(settings, "MODEL_S3_PREFIX", "model-io") or "model-io")
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        dry_run = options.get("dry_run", False)

        if not bucket:
            self.stderr.write("MODEL_S3_BUCKET not set in settings.")
            return

        region = getattr(settings, "MODEL_S3_REGION", "") or None
        access_key = getattr(settings, "AWS_ACCESS_KEY_ID", "") or None
        secret_key = getattr(settings, "AWS_SECRET_ACCESS_KEY", "") or None

        if not access_key or not secret_key:
            self.stderr.write("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set.")
            return

        client_kwargs = {
            "region_name": region,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }
        session_token = getattr(settings, "AWS_SESSION_TOKEN", "") or None
        if session_token:
            client_kwargs["aws_session_token"] = session_token

        s3 = boto3.client("s3", **client_kwargs)
        paginator = s3.get_paginator("list_objects_v2")
        count = 0

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                count += 1
                if dry_run:
                    self.stdout.write(f"Would delete: s3://{bucket}/{key}")
                else:
                    s3.delete_object(Bucket=bucket, Key=key)
                    self.stdout.write(f"Deleted: s3://{bucket}/{key}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Dry run: {count} object(s) would be deleted under {prefix}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Deleted {count} object(s) under {prefix}"))
