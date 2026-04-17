"""S3 client and audit-trail helpers for prediction input/output storage."""

import json
from datetime import timezone


def as_of_key(as_of):
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    return as_of.astimezone(timezone.utc).strftime("%Y%m%dT%H0000Z")


def build_patient_prefix(prefix, subject_id, stay_id, hadm_id):
    return f"{prefix.rstrip('/')}/patients/{subject_id}_{stay_id}_{hadm_id}"


def get_s3_client(settings):
    try:
        import boto3
    except ImportError:
        return None, "boto3 not installed. Run: pip install boto3"

    region = getattr(settings, "MODEL_S3_REGION", "") or None
    access_key = getattr(settings, "AWS_ACCESS_KEY_ID", "") or None
    secret_key = getattr(settings, "AWS_SECRET_ACCESS_KEY", "") or None
    session_token = getattr(settings, "AWS_SESSION_TOKEN", "") or None

    if not access_key or not secret_key:
        return None, "Missing AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY in .env"

    client_kwargs = {
        "region_name": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }
    if session_token:
        client_kwargs["aws_session_token"] = session_token

    return boto3.client("s3", **client_kwargs), None


def key_exists(s3, bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def write_json(s3, bucket, key, payload, skip_if_exists=False):
    if skip_if_exists and key_exists(s3, bucket, key):
        return
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )


def read_json(s3, bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def list_keys(s3, bucket, prefix):
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            keys.append(item["Key"])
    return sorted(keys)


def load_history_vectors(s3, bucket, patient_prefix, history_limit, current_key):
    feature_prefix = f"{patient_prefix}/features/"
    keys = list_keys(s3, bucket, feature_prefix)
    history_keys = [k for k in keys if k != current_key][-history_limit:]
    rows = []
    for key in history_keys:
        try:
            payload = read_json(s3, bucket, key)
            if payload.get("feature_vector"):
                rows.append(payload["feature_vector"])
        except Exception:
            continue
    return rows


def load_first_comorbidity_group(s3, bucket, patient_prefix):
    pred_prefix = f"{patient_prefix}/predictions/"
    keys = list_keys(s3, bucket, pred_prefix)
    for key in keys:
        try:
            payload = read_json(s3, bucket, key)
            group = payload.get("comorbidity_group")
            if group:
                return str(group)
        except Exception:
            continue
    return None
