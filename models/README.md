# Local prediction model

The runtime fallback model lives here. When the external prediction service at
`MODEL_SERVICE_URL` is unset or fails, the app loads this file via
`joblib.load()` and uses it to score patients.

## Default location

`models/sepsis_model.joblib` (override with the `LOCAL_MODEL_PATH` env var).

## Security

`joblib.load()` deserializes arbitrary Python objects and can execute arbitrary
code on load. Only place files here that were produced by us or another trusted
source. Never point `LOCAL_MODEL_PATH` at user-controlled input.
