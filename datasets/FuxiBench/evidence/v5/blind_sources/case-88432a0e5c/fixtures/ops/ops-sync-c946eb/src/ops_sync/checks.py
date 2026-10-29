"""Record checks used by the release pipeline."""


def normalise_record(record):
    """Return a copy of the record with stripped, lower-cased keys."""
    return {str(key).strip().lower(): value for key, value in record.items()}


def is_complete(record):
    """Return True only for a record that carries an id and an approval."""
    return bool(record.get("id")) and record.get("status") == "approved"
