import logging
from datetime import datetime, timedelta
from django.http import JsonResponse
from django.utils import timezone as django_tz
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from .models import UniquePatientProfile
from .services import (
    get_static_feature_sources,
    get_hourly_feature_sources,
    assemble_hourly_wide_table,
    get_prediction,
    get_similar_patients,
)
from .views import _store_similar_patients

logger = logging.getLogger(__name__)

def _resolve_time_window(request, window_hours_default=6):
    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    as_of_str = request.GET.get('as_of')
    window_hours = int(request.GET.get('window_hours', window_hours_default))

    if start_str and end_str:
        return parse_datetime(start_str), parse_datetime(end_str)
    
    if as_of_str:
        as_of = parse_datetime(as_of_str)
        if as_of:
            return as_of - timedelta(hours=window_hours), as_of
            
    raise ValueError("Provide either start+end params, or as_of (+ optional window_hours).")

@require_GET
def get_static_features(request, subject_id, stay_id, hadm_id):
    """
    GET /patients/<id>/features/static
    """
    sources = get_static_feature_sources(
        subject_id=subject_id, 
        stay_id=stay_id, 
        hadm_id=hadm_id
    )
    
    return JsonResponse({
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "sources": sources
    })

@require_GET
def get_hourly_features(request, subject_id, stay_id, hadm_id):
    """
    GET /patients/<id>/features/hourly
    """
    try:
        start_dt, end_dt = _resolve_time_window(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)

    include_procedures = request.GET.get('include_procedures', 'true').lower() == 'true'
    include_sofa = request.GET.get('include_sofa', 'true').lower() == 'true'

    sources = get_hourly_feature_sources(
        subject_id=subject_id,
        stay_id=stay_id,
        start=start_dt,
        end=end_dt,
        include_procedures=include_procedures,
        include_sofa=include_sofa
    )

    return JsonResponse({
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "window": {"start": start_dt, "end": end_dt},
        "sources": sources
    })

@require_GET
def get_hourly_wide_features(request, subject_id, stay_id, hadm_id):
    """
    GET /patients/<id>/features/hourly-wide
    """
    try:
        start_dt, end_dt = _resolve_time_window(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)

    include_sofa = request.GET.get('include_sofa', 'true').lower() == 'true'
    include_labs = request.GET.get('include_labs', 'true').lower() == 'true'

    wide_table = assemble_hourly_wide_table(
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id,
        start=start_dt,
        end=end_dt,
        include_sofa=include_sofa,
        include_labs=include_labs
    )

    return JsonResponse({
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "window": {"start": start_dt, "end": end_dt},
        "sources": {
            "hourly_wide": wide_table
        }
    })

@require_GET
def get_feature_bundle(request, subject_id, stay_id, hadm_id):
    """
    GET /patients/<id>/feature-bundle
    """
    try:
        start_dt, end_dt = _resolve_time_window(request)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)

    # Fetch both static and hourly
    static_sources = get_static_feature_sources(subject_id, stay_id, hadm_id)
    hourly_sources = get_hourly_feature_sources(
        subject_id, stay_id, start_dt, end_dt,
        include_procedures=True, include_sofa=True
    )

    # Merge dictionaries
    merged_sources = {**static_sources, **hourly_sources}

    return JsonResponse({
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "window": {"start": start_dt, "end": end_dt},
        "sources": merged_sources
    })


@require_GET
def get_prediction_view(request, subject_id, stay_id, hadm_id):
    """
    GET /patients/<ids>/prediction
    Returns risk_score and comorbidity_group for routing to the patient view.
    Query params: as_of (required), window_hours (default 24).
    """
    as_of_str = request.GET.get('as_of')
    window_hours = int(request.GET.get('window_hours', 24))
    if not as_of_str:
        return JsonResponse({"error": "Provide as_of (ISO datetime) query param."}, status=400)
    as_of = parse_datetime(as_of_str)
    if not as_of:
        return JsonResponse({"error": "Invalid as_of format."}, status=400)

    result = get_prediction(
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id,
        as_of=as_of,
        window_hours=window_hours,
    )
    if not result.get("ok"):
        error_msg = result.get("error", "Prediction failed")
        logger.error(f"Prediction failed for patient {subject_id}/{stay_id}/{hadm_id}: {error_msg}")
        return JsonResponse({"error": error_msg}, status=500)

    return JsonResponse({
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "as_of": as_of_str,
        "risk_score": result["risk_score"],
        "comorbidity_group": result["comorbidity_group"],
    })


def _enrich_similar_patients(similar):
    """Add profile data (anchor_age, gender, race, hours_since_admission) to similar patients."""
    from datetime import timezone as dt_tz

    enriched = []
    for s in similar:
        charttime_dt = parse_datetime(s.get('charttime_hour_str', '')) if s.get('charttime_hour_str') else None
        hours_since_admission = None
        profile = None

        try:
            profile = UniquePatientProfile.objects.get(
                subject_id=s['subject_id'],
                stay_id=s['stay_id'],
                hadm_id=s['hadm_id'],
            )
            if profile.intime and charttime_dt:
                ct = charttime_dt
                if django_tz.is_naive(ct):
                    ct = django_tz.make_aware(ct, dt_tz.utc)
                delta = ct - profile.intime
                hours_since_admission = round(delta.total_seconds() / 3600, 1)
        except UniquePatientProfile.DoesNotExist:
            pass

        enriched.append({
            'subject_id': s['subject_id'],
            'stay_id': s['stay_id'],
            'hadm_id': s['hadm_id'],
            'similarity_score': s['similarity_score'],
            'had_sepsis': s['had_sepsis'],
            'anchor_age': profile.anchor_age if profile else None,
            'gender': profile.gender if profile else None,
            'race': profile.race if profile else None,
            'hours_since_admission': hours_since_admission,
            'features': s.get('features') or {},
        })
    return enriched


@require_GET
def get_similar_patients_view(request, subject_id, stay_id, hadm_id):
    """
    GET /patients/<id>/similar-patients
    Returns top 3 most similar patients (by feature vector cosine similarity) from
    non-cohort pool. Query params: as_of (required).
    Enriched with profile data; stores in session for cache.
    """
    as_of_str = request.GET.get('as_of')
    if not as_of_str:
        return JsonResponse({"error": "Provide as_of (ISO datetime) query param."}, status=400)
    as_of = parse_datetime(as_of_str)
    if not as_of:
        return JsonResponse({"error": "Invalid as_of format."}, status=400)

    similar = get_similar_patients(
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id,
        as_of=as_of,
        top_k=3,
    )

    enriched = _enrich_similar_patients(similar)

    # Store in session for cache (current_hour from as_of: 2025-03-13T09:00 -> hour 8)
    current_hour = (as_of.hour - 1) % 24 if as_of.month == 3 and as_of.day == 13 else as_of.hour
    if as_of.day == 14 and as_of.hour == 0:
        current_hour = 23
    _store_similar_patients(
        request.session, subject_id, stay_id, hadm_id, current_hour, enriched
    )

    return JsonResponse({
        "patient": {"subject_id": subject_id, "stay_id": stay_id, "hadm_id": hadm_id},
        "as_of": as_of_str,
        "similar_patients": enriched,
    })
