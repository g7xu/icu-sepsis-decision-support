"""
Shared simulation constants and helpers used by views.py and pipeline.py.

To change the simulation date, update SIM_YEAR/SIM_MONTH/SIM_DAY here —
all derived labels and ISO strings update automatically.
"""

from faker import Faker

SIM_YEAR  = 2025
SIM_MONTH = 3
SIM_DAY   = 13

SIM_DATE_LABEL      = "March 13, 2025"
SIM_DATE_NEXT_LABEL = "March 14, 2025"
SIM_DATE_ISO        = f"{SIM_YEAR:04d}-{SIM_MONTH:02d}-{SIM_DAY:02d}"       # "2025-03-13"
SIM_DATE_NEXT_ISO   = f"{SIM_YEAR:04d}-{SIM_MONTH:02d}-{SIM_DAY + 1:02d}"  # "2025-03-14"


def display_time(current_hour: int) -> str:
    """Return human-readable clock string for a given internal hour index.

    current_hour == -1  →  "March 13, 2025 00:00"  (not started)
    current_hour ==  0  →  "March 13, 2025 01:00"
    ...
    current_hour == 22  →  "March 13, 2025 23:00"
    current_hour == 23  →  "March 14, 2025 00:00"
    """
    display_hour = current_hour + 1
    if display_hour <= 0:
        return f"{SIM_DATE_LABEL} 00:00"
    elif display_hour >= 24:
        return f"{SIM_DATE_NEXT_LABEL} 00:00"
    return f"{SIM_DATE_LABEL} {display_hour:02d}:00"


def get_display_name(subject_id, stay_id, hadm_id):
    """Deterministic pseudonymized name for a patient triple."""
    fake = Faker()
    fake.seed_instance(hash((subject_id, stay_id, hadm_id)))
    return fake.name()


def format_procedure_value(value, uom):
    """Format a procedure value for human-readable display.

    MIMIC stores durations as fractional days (e.g. 0.170 day = ~4h 5m).
    Convert to hours:minutes for display.
    """
    if value is None:
        return None, None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value), uom

    uom_lower = (uom or '').lower().strip()

    # Binary flag (value=1, no unit) — just means "procedure occurred"; hide it
    if val == 1 and uom_lower in ('', 'none'):
        return None, None

    if uom_lower in ('day', 'days'):
        total_minutes = round(val * 24 * 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours}h {minutes:02d}m", None
    if uom_lower in ('hour', 'hours'):
        total_minutes = round(val * 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours}h {minutes:02d}m", None
    if uom_lower in ('min', 'minute', 'minutes'):
        total_minutes = round(val)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours > 0:
            return f"{hours}h {minutes:02d}m", None
        return f"{minutes}m", None

    # For other units, round to 2 decimal places if it's a float
    if val == int(val):
        return str(int(val)), uom
    return f"{val:.2f}", uom


def prediction_as_of_iso(current_hour: int) -> str | None:
    """Return the ISO timestamp used as `as_of` for the prediction API call.

    Returns None if the simulation has not started (current_hour < 0).
    """
    if current_hour < 0:
        return None
    if current_hour >= 23:
        return f"{SIM_DATE_NEXT_ISO}T00:00:00"
    return f"{SIM_DATE_ISO}T{current_hour + 1:02d}:00:00"
