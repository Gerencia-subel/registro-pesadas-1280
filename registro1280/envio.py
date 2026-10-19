"""Servicio del correo: cada N minutos envía por Gmail un CSV solo con las pesadas nuevas.

Corre siempre (systemd). El comando "correo" solo cambia la configuración en la base:
  correo_activo   1 = enviar, 0 = detenido
  correo_minutos  cada cuántos minutos (los envíos caen en horas redondas: 09:10, 09:15...)
  correo_desde    inicio del tramo que falta enviar (fin del último correo que salió bien)
  correo_proximo  hora del próximo envío
En cada envío: primero descarga el controlador (para que entre lo último), toma las
pesadas que todavía no salieron en ningún correo, arma el CSV y lo envía. Solo si Gmail
lo acepta, esas pesadas quedan marcadas con el número del correo: así ninguna se repite,
y si un envío falla, sus pesadas salen en el siguiente correo junto con las nuevas.
"""

import logging
import socket
import threading
import time
import traceback
from datetime import datetime, timedelta

import MySQLdb

from . import basedatos, correo
from .basedatos import ErrorBase
from .controlador import ErrorControlador, leer_pesadas
from .guardado import guardar
from .reloj import ahora

registro = logging.getLogger("registro1280")

LATIDO_CADA = 30
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


def cuerpo_tramo(desde, hasta, n, archivo, aviso=""):
    lineas = []
    if n:
        lineas.append("Pesadas del %s: %s." % (correo.rango(desde, hasta), correo.cantidad_texto(n)))
        lineas.append("Archivo adjunto: %s (columnas %s)." % (archivo, ", ".join(correo.COLUMNAS_CSV)))
    else:
        lineas.append("Sin pesadas del %s." % correo.rango(desde, hasta))
    if aviso:
        lineas += ["", aviso]
    lineas += ["", "Correo automático del registro de pesadas del Rice Lake 1280 (Raspberry %s)."
               % socket.gethostname()]
    return "\n".join(lineas) + "\n"


class ServicioCorreo:
    def __init__(self, conectar=basedatos.conectar, leer=leer_pesadas, reloj=ahora, enviar=correo.enviar,
                 leer_cuenta=correo.leer_cuenta, espera_reintento=30, dormir=None):
        self.conectar = conectar
        self.leer = leer
        self.reloj = reloj
        self.enviar = enviar
        self.leer_cuenta = leer_cuenta
        self.espera_reintento = espera_reintento
        self.detener = threading.Event()
        self.dormir = dormir or self.detener.wait
        self._con = None
        self._ultimo_latido = 0.0
        self._activo = None

    # ------------------------------------------------------------------
    def correr(self):
        registro.info("Servicio de correo iniciado")
        while not self.detener.is_set():
            try:
                espera = self.vuelta()
            except (ErrorBase, MySQLdb.Error) as e:
                registro.error("Base de datos: %s", e)
                self._cerrar()
                espera = 10
            except Exception:
                registro.error("Error inesperado: %s", traceback.format_exc())
                self._cerrar()
                espera = 10
            self.detener.wait(max(0.2, min(espera, 2.0)))
        self._cerrar()
        registro.info("Servicio de correo detenido")

    def _cerrar(self):
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
        self._con = None

    def vuelta(self):
        """Una revisión. Devuelve cuántos segundos esperar hasta la siguiente."""
        if self._con is None:
            self._con = self.conectar()
        con = self._con
        if time.monotonic() - self._ultimo_latido >= LATIDO_CADA:
            basedatos.guardar_config(con, correo_latido=texto_fecha(self.reloj()))
            self._ultimo_latido = time.monotonic()
        cfg = basedatos.config(con)
        activo = cfg.get("correo_activo") == "1"
        minutos = minutos_config(cfg)
        if activo != self._activo:
            self._activo = activo
            registro.info("Correo %s", "activo: cada %d min" % minutos if activo else "detenido")
        if not activo:
            return 2
        momento = self.reloj()
        proximo = leer_fecha(cfg.get("correo_proximo"))
        if proximo is None:
            basedatos.guardar_config(con, correo_proximo=texto_fecha(siguiente_envio(momento, minutos)))
            return 0.5
        if momento < proximo:
            return (proximo - momento).total_seconds()
        if not basedatos.tomar_candado_correo(con):
            return 5
        try:
            hasta, salio = self.enviar_tramo(con, cfg, proximo, momento)
        finally:
            basedatos.soltar_candado_correo(con)
        # Si mientras se enviaba alguien detuvo o volvió a activar el correo, se respeta lo nuevo.
        actual = basedatos.config(con)
        if actual.get("correo_activo") == "1" and actual.get("correo_desde") == cfg.get("correo_desde") \
                and actual.get("correo_proximo") == cfg.get("correo_proximo"):
            cambios = {"correo_proximo": texto_fecha(siguiente_envio(self.reloj(), minutos_config(actual),
                                                                     base=proximo))}
            if salio:
                cambios["correo_desde"] = texto_fecha(hasta)
            basedatos.guardar_config(con, **cambios)
        return 0.5

    # ------------------------------------------------------------------
    def enviar_tramo(self, con, cfg, proximo, momento):
        """Descarga, arma el CSV de lo pendiente y lo envía. Devuelve (fin del tramo, salió bien)."""
        minutos = minutos_config(cfg)
        # Normalmente el tramo termina en la hora programada. Si la Pi estuvo apagada o sin
        # servicio, termina ahora (cubre todo lo que faltaba).
        hasta = proximo if (momento - proximo).total_seconds() < 60 else momento.replace(second=0)
        desde = leer_fecha(cfg.get("correo_desde")) or hasta - timedelta(minutes=minutos)
        desde = min(desde, hasta)

        aviso, sin_controlador = "", False
        try:
            guardar(con, "correo", reintentos=1, leer=self.leer, reloj=self.reloj, dormir=self.dormir)
        except ErrorControlador as e:
            sin_controlador = True
            aviso = ("Aviso: a las %s no se pudo leer el controlador (%s). Las pesadas que falten se "
                     "enviarán en el siguiente correo." % (self.reloj().strftime("%H:%M"), e))
        except ErrorBase as e:
            aviso = ("Aviso: no se pudo guardar lo último del controlador (%s). Las pesadas que falten se "
                     "enviarán en el siguiente correo." % e)

        filas = basedatos.pendientes_correo(con)
        archivo = correo.nombre_archivo(desde, hasta) if filas else None
        datos = dict(tipo="tramo", desde=desde, hasta=hasta, pesadas=len(filas), archivo=archivo,
                     para=cfg.get("correo_para", ""), copia=cfg.get("correo_copia", ""))
        try:
            para = correo.direcciones(cfg.get("correo_para"))
            copia = correo.direcciones(cfg.get("correo_copia"))
            if not para:
                raise correo.ErrorCorreo("No hay destinatarios. Indicarlos con:  correo para nombre@empresa.com")
            cuenta = self.leer_cuenta()
        except (ValueError, correo.ErrorCorreo) as e:
            return self._resultado(con, datos, hasta, None, str(e))

        asunto = correo.asunto_tramo(desde, hasta, len(filas), sin_controlador)
        cuerpo = cuerpo_tramo(desde, hasta, len(filas), archivo, aviso)
        mensaje = correo.armar_mensaje(cuenta, para, copia, asunto, cuerpo,
                                       correo.armar_csv(filas) if filas else None, archivo)
        error, no_aceptados = None, ""
        for intento in range(2):
            try:
                no_aceptados = self.enviar(cuenta, mensaje) or ""
                error = None
                break
            except correo.ErrorCorreo as e:
                error = str(e)
                registro.warning("No se pudo enviar el correo (%s): %s", asunto, e)
                if intento == 0 and e.reintentar and not self.detener.is_set():
                    self.dormir(self.espera_reintento)
                else:
                    break
        if error is None:
            registro.info("Correo enviado: %s", asunto)
            detalle = " ".join(t for t in (no_aceptados, aviso) if t)
            return self._resultado(con, datos, hasta, [f["nro"] for f in filas], detalle)
        return self._resultado(con, datos, hasta, None, error)

    def _resultado(self, con, datos, hasta, enviadas, detalle):
        ok = enviadas is not None
        basedatos.registrar_correo(con, nros_enviados=enviadas or (), enviado_en=self.reloj(),
                                   resultado="OK" if ok else "ERROR", detalle=detalle or "", **datos)
        return hasta, ok
