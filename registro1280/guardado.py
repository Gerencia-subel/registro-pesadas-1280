"""Una descarga del controlador y su guardado en la base (lo usan "guardar" y el bucle)."""

import logging
import time

import MySQLdb

from . import basedatos
from .basedatos import ErrorBase
from .controlador import ErrorControlador, leer_pesadas
from .reloj import ahora

registro = logging.getLogger("registro1280")


def guardar(con, origen, reintentos=0, espera=5.0, leer=leer_pesadas, reloj=ahora, dormir=time.sleep):
    """Descarga la tabla de pesadas y guarda lo nuevo.

    Devuelve un dict con: ip, puerto, leidas, esperadas, segundos, nuevas, actualizadas,
    sin_cambio, rechazadas, total. Si falla, lo anota en "descargas" y lanza
    ErrorControlador o ErrorBase (no se guarda nada a medias).
    """
    cfg = basedatos.config(con)
    ip, puerto = cfg.get("ip", ""), cfg.get("puerto", "10001")
    inicio = reloj()
    intento = 0
    while True:
        try:
            lectura = leer(ip, int(puerto))
            break
        except (ErrorControlador, ValueError, OSError) as e:
            if intento < reintentos:
                intento += 1
                registro.warning("Lectura fallida (%s). Reintentando en %d s", e, espera)
                dormir(espera)
                continue
            detalle = "%s:%s - %s" % (ip, puerto, e)
            _anotar(con, inicio=inicio, origen=origen, resultado="ERROR", detalle=detalle)
            raise ErrorControlador(str(e))
    try:
        resultado = basedatos.guardar_pesadas(con, lectura.pesadas, lectura.rechazadas, reloj())
    except (ErrorBase, MySQLdb.Error) as e:
        texto = e.args[1] if isinstance(e, MySQLdb.Error) and len(e.args) > 1 else str(e)
        _anotar(con, inicio=inicio, origen=origen, resultado="ERROR", segundos=round(lectura.segundos, 2),
                leidas=len(lectura.pesadas) + len(lectura.rechazadas), detalle="Al guardar: %s" % texto)
        raise ErrorBase("No se pudo guardar en la base: %s" % texto)
    leidas = len(lectura.pesadas) + len(lectura.rechazadas)
    detalle = ""
    if lectura.rechazadas:
        detalle = "%d filas con datos inválidos (ver tabla rechazadas)" % len(lectura.rechazadas)
    _anotar(con, inicio=inicio, origen=origen, resultado="OK", segundos=round(lectura.segundos, 2),
            leidas=leidas, detalle=detalle, **resultado)
    resultado.update(ip=ip, puerto=puerto, leidas=leidas, esperadas=lectura.esperadas,
                     segundos=lectura.segundos)
    return resultado


def _anotar(con, **campos):
    try:
        basedatos.registrar_descarga(con, **campos)
    except (ErrorBase, MySQLdb.Error) as e:
        registro.error("No se pudo anotar la descarga: %s", e)
