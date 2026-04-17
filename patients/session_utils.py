"""Session-cache helpers and simulation-clock helpers shared across views/api."""


def _prediction_cache_key(subject_id, stay_id, hadm_id, hour):
    return f"{subject_id}_{stay_id}_{hadm_id}_{hour}"


def get_prediction_cached(session, subject_id, stay_id, hadm_id, hour):
    """Return cached {risk_score, comorbidity_group} for this hour or None."""
    preds = session.get("predictions") or {}
    return preds.get(_prediction_cache_key(subject_id, stay_id, hadm_id, hour))


def store_prediction(session, subject_id, stay_id, hadm_id, hour, risk_score, comorbidity_group):
    preds = session.get("predictions") or {}
    preds[_prediction_cache_key(subject_id, stay_id, hadm_id, hour)] = {
        "risk_score": risk_score,
        "comorbidity_group": comorbidity_group,
    }
    session["predictions"] = preds
    session.modified = True


def get_similar_patients_cached(session, subject_id, stay_id, hadm_id, hour):
    cache = session.get("similar_patients") or {}
    return cache.get(_prediction_cache_key(subject_id, stay_id, hadm_id, hour))


def store_similar_patients(session, subject_id, stay_id, hadm_id, hour, similar_list):
    cache = session.get("similar_patients") or {}
    cache[_prediction_cache_key(subject_id, stay_id, hadm_id, hour)] = similar_list
    session["similar_patients"] = cache
    session.modified = True


def simulation_hour_from_as_of(as_of):
    """Invert the display ``as_of`` (2025-03-13/14) back to a simulation hour 0-23."""
    if as_of.month == 3 and as_of.day == 14 and as_of.hour == 0:
        return 23
    if as_of.month == 3 and as_of.day == 13:
        return (as_of.hour - 1) % 24
    return as_of.hour
