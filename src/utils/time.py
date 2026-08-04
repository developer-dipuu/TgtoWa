from datetime import datetime, timezone

def utcnow() -> datetime:
    """Returns the current UTC time."""
    return datetime.now(timezone.utc).replace(microsecond=0)