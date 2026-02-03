"""
Patient views - handles patient list and detail pages.
"""

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q
from .models import UniquePatientProfile
from .cohort import get_cohort_filter


def patient_list(request):
    """
    Display a paginated list of patients (Patient Index view).
    
    Filters patients based on the cohort defined in cohort.py.
    URL: /patients/ or /
    """
    # Start with all patients
    patients = UniquePatientProfile.objects.all()
    
    # Apply cohort filter if defined
    cohort = get_cohort_filter()
    if cohort:
        if cohort['type'] == 'subject_ids':
            # Filter by subject_id list
            patients = patients.filter(subject_id__in=cohort['values'])
        elif cohort['type'] == 'tuples':
            # Filter by exact (subject_id, stay_id, hadm_id) tuples
            conditions = Q()
            for subject_id, stay_id, hadm_id in cohort['values']:
                conditions |= Q(subject_id=subject_id, stay_id=stay_id, hadm_id=hadm_id)
            patients = patients.filter(conditions)
    
    # Order by subject_id
    patients = patients.order_by('subject_id')
    
    # Pagination - 25 patients per page
    paginator = Paginator(patients, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'total_patients': patients.count(),
        'cohort_active': cohort is not None,
    }
    return render(request, 'patients/index.html', context)


def patient_detail(request, subject_id, stay_id, hadm_id):
    """
    Display details for a specific patient stay.
    
    URL: /patients/<subject_id>/<stay_id>/<hadm_id>/
    """
    patient = get_object_or_404(
        UniquePatientProfile,
        subject_id=subject_id,
        stay_id=stay_id,
        hadm_id=hadm_id
    )
    
    context = {
        'patient': patient,
    }
    return render(request, 'patients/show.html', context)
