"""Hora de Lima, sin depender de la zona horaria configurada en la Raspberry."""

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("America/Lima")
except Exception:  # sin base de zonas horarias: Perú no usa horario de verano
    ZONA = timezone(timedelta(hours=-5), "Lima")


def ahora():
    """Fecha y hora actual de Lima, sin zona (así se guarda en la base)."""
    return datetime.now(ZONA).replace(tzinfo=None, microsecond=0)
