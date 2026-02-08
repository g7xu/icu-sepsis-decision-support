#!/usr/bin/env python
"""
Test script to verify database connection to MIMIC-IV PostgreSQL.
Run this before starting Django to ensure the connection works.
"""

import os
import sys

# Add the project to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.db import connection
from patients.models import UniquePatientProfile


def test_connection():
    """Test the database connection and query the patient table."""
    print("=" * 60)
    print("ICU Sepsis Decision Support - Database Connection Test")
    print("=" * 60)
    
    # Test 1: Basic connection
    print("\n[1] Testing database connection...")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            version = cursor.fetchone()[0]
            print(f"    ✓ Connected to PostgreSQL")
            print(f"    Version: {version[:50]}...")
    except Exception as e:
        print(f"    ✗ Connection failed: {e}")
        return False
    
    # Test 2: Check schema access
    print("\n[2] Testing schema access (mimiciv_derived)...")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_schema();")
            schema = cursor.fetchone()[0]
            print(f"    ✓ Current schema: {schema}")
    except Exception as e:
        print(f"    ✗ Schema check failed: {e}")
        return False
    
    # Test 3: Check if materialized view exists
    print("\n[3] Checking for fisi9t_unique_patient_profile materialized view...")
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM pg_matviews 
                    WHERE schemaname = 'mimiciv_derived' 
                    AND matviewname = 'fisi9t_unique_patient_profile'
                );
            """)
            exists = cursor.fetchone()[0]
            if exists:
                print("    ✓ Materialized view exists in mimiciv_derived schema")
            else:
                print("    ✗ Materialized view NOT found in mimiciv_derived schema")
                print("    Please verify the view name and schema")
                return False
    except Exception as e:
        print(f"    ✗ Materialized view check failed: {e}")
        return False
    
    # Test 4: Query patients using Django ORM
    print("\n[4] Testing Django ORM query...")
    try:
        count = UniquePatientProfile.objects.count()
        print(f"    ✓ Found {count} patient records")
        
        if count > 0:
            # Show first 5 patients
            print("\n    Sample patients:")
            patients = UniquePatientProfile.objects.all()[:5]
            for p in patients:
                print(f"      - Subject: {p.subject_id}, Stay: {p.stay_id}, "
                      f"Age: {p.anchor_age}, Gender: {p.gender}")
    except Exception as e:
        print(f"    ✗ ORM query failed: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("All tests passed! Your database connection is working.")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. python manage.py migrate  (for Django's internal tables)")
    print("  2. python manage.py runserver")
    print("  3. Open http://127.0.0.1:8000/patients/")
    print()
    
    return True


if __name__ == '__main__':
    success = test_connection()
    sys.exit(0 if success else 1)
