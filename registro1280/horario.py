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

    Al activar (sin "base"): si los minutos dividen el día (1, 2, 5, 10, 15, 30, 60...) el
    primer envío cae en hora redonda (cada 5, activado a las 09:12: 09:15). Si no, a los N
    minutos.
    Después de cada envío ("base" = la hora de ese envío) se cuentan los N minutos desde ahí.
    Así, si se cambia el intervalo, el correo que ya estaba programado sale igual y el nuevo
    intervalo se cuenta desde ese envío (cada 5 hasta las 15:25 y cambio a 10: 15:35, 15:45...).
    """
    paso = timedelta(minutes=minutos)
    if base is None:
        if MINUTOS_DIA % minutos == 0:
            medianoche = despues_de.replace(hour=0, minute=0, second=0, microsecond=0)
            return medianoche + ((despues_de - medianoche) // paso + 1) * paso
        base = despues_de
    inicio = base.replace(second=0, microsecond=0)
    if despues_de < inicio:
        return inicio
    return inicio + ((despues_de - inicio) // paso + 1) * paso
