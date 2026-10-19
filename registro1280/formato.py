"""Formatos para mostrar datos (terminal y web)."""

from decimal import Decimal


def kg(valor):
    """22559.85 -> '22,559.85 kg' (coma de miles, punto decimal, 2 decimales)."""
    if valor is None:
        return ""
    numero = Decimal(valor).quantize(Decimal("0.01"))
    if numero == 0:
        numero = abs(numero)
    return "{:,.2f} kg".format(numero)


def fecha(momento):
    return momento.strftime("%d/%m/%Y %H:%M") if momento else ""


def fecha_segundos(momento):
    return momento.strftime("%d/%m/%Y %H:%M:%S") if momento else ""


def numero(n):
    return "{:,}".format(int(n or 0))


def hace(segundos):
    segundos = int(max(0, segundos))
    if segundos < 60:
        return "%d s" % segundos
    if segundos < 3600:
        return "%d min" % (segundos // 60)
    if segundos < 86400:
        return "%d h %d min" % (segundos // 3600, segundos % 3600 // 60)
    return "%d días" % (segundos // 86400)
