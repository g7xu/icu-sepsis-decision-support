"""
Display names for patient pseudonymization.
Maps (subject_id, stay_id, hadm_id) to human-readable names for clinician-facing UI.
IDs remain in URLs and backend - only the display is anonymized.
"""

# Unique names for cohort - deterministic assignment (no duplicates per cohort)
DISPLAY_NAMES = [
    "Varun Pabreja", "Jason Xu", "Hikaru Isayama", "Ethan Vo",
    "Yash Patel", "Gloria Ye", "Utkarsh Lohia", "Susan Walker",
    "Optimus Prime", "Barbara White", "William Harris", "Kate Zhou",
    "Richard Lewis", "Samuel Mahjouri", "Joseph Hall", "Jessica Allen",
    "Thomas Young", "Sarah King", "Charles Wright", "Karen Scott",
    "Christopher Green", "Nancy Adams", "Daniel Baker", "Betty Nelson",
    "Matthew Carter", "Margaret Mitchell", "Anthony Perez", "Sandra Roberts",
    "Mark Turner", "Ashley Phillips", "Donald Campbell", "Kimberly Parker",
    "Steven Evans", "Emily Edwards", "Paul Collins", "Donna Stewart",
    "Andrew Sanchez", "Michelle Morris", "Joshua Rogers", "Carol Reed",
    "Kenneth Cook", "Amanda Morgan", "Kevin Bell", "Melissa Murphy",
    "Brian Bailey", "Deborah Rivera", "George Cooper", "Stephanie Richardson",
    "Edward Cox", "Rebecca Howard", "Ronald Ward", "Sharon Torres",
    "Timothy Peterson", "Laura Gray", "Jason Ramirez", "Cynthia James",
    "Jeffrey Watson", "Kathleen Brooks", "Ryan Kelly", "Amy Sanders",
    "Jacob Price", "Angela Bennett", "Gary Wood", "Brenda Barnes",
    "Nicholas Ross", "Marie Henderson", "Eric Coleman", "Diane Jenkins",
    "Jonathan Perry", "Joyce Powell", "Frank Long", "Virginia Patterson",
    "Scott Hughes", "Rachel Hughes", "Gregory Flores", "Carolyn Green",
    "Samuel Washington", "Janet Butler", "Raymond Simmons", "Maria Foster",
    "Patrick Gonzales", "Heather Bryant", "Alexander Alexander", "Doris Russell",
    "Jack Griffin", "Evelyn Hayes", "Dennis Myers", "Jean Ford",
    "Jerry Hamilton", "Alice Graham", "Tyler Sullivan", "Judith Wallace",
]


def get_display_name_mapping():
    """
    Returns dict mapping (subject_id, stay_id, hadm_id) -> display_name.
    Deterministic: same patient always gets same name. No duplicates within cohort.
    """
    from .cohort import get_cohort_filter
    from .models import UniquePatientProfile

    cohort = get_cohort_filter()
    if not cohort:
        return {}

    if cohort['type'] == 'tuples':
        tuples = sorted(cohort['values'])
    elif cohort['type'] == 'subject_ids':
        qs = UniquePatientProfile.objects.filter(
            subject_id__in=cohort['values']
        ).values_list('subject_id', 'stay_id', 'hadm_id')
        tuples = sorted(set(qs))
    else:
        return {}

    return {
        t: DISPLAY_NAMES[i] if i < len(DISPLAY_NAMES) else f"Patient {i + 1}"
        for i, t in enumerate(tuples)
    }
