from django.contrib import admin
from .models import UniquePatientProfile


@admin.register(UniquePatientProfile)
class UniquePatientProfileAdmin(admin.ModelAdmin):
    list_display = ('subject_id', 'stay_id', 'hadm_id', 'anchor_age', 'gender', 'first_careunit')
    list_filter = ('gender', 'first_careunit')
    search_fields = ('subject_id', 'stay_id', 'hadm_id')
