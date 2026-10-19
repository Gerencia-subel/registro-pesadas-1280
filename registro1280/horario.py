"""Horas del envío por correo (sin dependencias del resto, lo usan el servicio y los comandos)."""

from datetime import datetime, timedelta

FORMATO = "%Y-%m-%d %H:%M:%S"
MINUTOS_DIA = 24 * 60


def texto_fecha(momento):
    return momento.strftime(FORMATO) if momento else ""


def leer_fecha(texto):
    try:
        return datetime.strptime((texto or "").strip(), FORMATO)
    except ValueError:
        return None


def minutos_config(cfg):
    try:
        return max(1, int(cfg.get("correo_minutos") or 5))
    except ValueError:
        return 5


def siguiente_envio(despues_de, minutos, base=None):
    """Hora del próximo envío, siempre después de "despues_de".

    Si los minutos dividen el día (1, 2, 5, 10, 15, 30, 60...) los envíos caen en horas
    redondas contadas desde las 00:00 (cada 5: 09:10, 09:15, 09:20). Si no, se cuentan
    desde "base" (el envío anterior o la activación).
    """
    paso = timedelta(minutes=minutos)
    if MINUTOS_DIA % minutos == 0:
        medianoche = despues_de.replace(hour=0, minute=0, second=0, microsecond=0)
        return medianoche + ((despues_de - medianoche) // paso + 1) * paso
    inicio = (base or despues_de).replace(second=0, microsecond=0)
    if despues_de < inicio:
        return inicio
    return inicio + ((despues_de - inicio) // paso + 1) * paso
