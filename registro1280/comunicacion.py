"""Estado de comunicación con el controlador: un ping cada N segundos (por defecto 5).

Corre como servicio aparte (registro1280-comunicacion). Solo hace ping a la IP del
controlador: no usa el puerto 10001, así que no molesta al controlador ni a Captura 1280.
El resultado ("Activo" o "Falla de comunicación") no se guarda en la base: queda en
/run/registro1280/comunicacion.json (en memoria; se borra al apagar la Raspberry) y lo leen
el correo, para poner el último valor en cada envío, y la web.
En la base solo está la configuración: comunicacion_activo y comunicacion_segundos.

Alerta: cuando el estado pasa de "Activo" a "Falla de comunicación" se envía UN correo
"Alerta de falla de comunicación" a los destinatarios del correo (Para y Copia). Mientras
siga caído no se repite; si vuelve y se cae otra vez, se envía otro. Si no se puede enviar
(sin internet), se reintenta cada minuto mientras siga caído.
"""

import json
import logging
import os
import socket
import subprocess
import threading
import time
import traceback
from datetime import datetime

import MySQLdb

from . import basedatos, correo
from .basedatos import ErrorBase
from .reloj import ahora

registro = logging.getLogger("registro1280")

RUTA = os.environ.get("REGISTRO1280_ESTADO", "/run/registro1280/comunicacion.json")
ACTIVO = "Activo"
FALLA = "Falla de comunicación"
DESACTIVADO = "Desactivado"
SIN_DATO = "Sin dato"
FORMATO = "%Y-%m-%d %H:%M:%S"
REINTENTO_ALERTA = 60   # segundos entre intentos de enviar la alerta si falla el envío


def segundos_config(cfg):
    try:
        return max(1, int(cfg.get("comunicacion_segundos") or 5))
    except ValueError:
        return 5


def hacer_ping(ip, espera=2):
    """True si el controlador responde al ping."""
    try:
        # Hasta 2 paquetes: con que responda uno está vivo (evita falsas alertas por un paquete perdido).
        r = subprocess.run(["ping", "-c", "2", "-i", "0.5", "-W", str(espera), str(ip)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=espera + 3)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def escribir_estado(estado, momento, ruta=None):
    ruta = ruta or RUTA
    temporal = ruta + ".tmp"
    with open(temporal, "w", encoding="utf-8") as f:
        json.dump({"estado": estado, "momento": momento.strftime(FORMATO)}, f, ensure_ascii=False)
    os.chmod(temporal, 0o644)
    os.replace(temporal, ruta)


def leer_estado(cfg, reloj=ahora, ruta=None):
    """Último estado: dict con estado y momento (datetime o None)."""
    if cfg.get("comunicacion_activo") != "1":
        return {"estado": DESACTIVADO, "momento": None}
    try:
        with open(ruta or RUTA, encoding="utf-8") as f:
            datos = json.load(f)
        momento = datetime.strptime(datos["momento"], FORMATO)
        estado = datos["estado"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"estado": SIN_DATO, "momento": None}
    # Si el servicio dejó de revisar, el dato ya no vale.
    if (reloj() - momento).total_seconds() > max(30, 3 * segundos_config(cfg) + 10):
        return {"estado": SIN_DATO, "momento": momento}
    return {"estado": estado, "momento": momento}


def estado_guardado(ruta=None):
    """El último estado escrito (para no perder un cambio si el servicio se reinicia)."""
    try:
        with open(ruta or RUTA, encoding="utf-8") as f:
            estado = json.load(f).get("estado")
    except (OSError, ValueError, AttributeError):
        return None
    return estado if estado in (ACTIVO, FALLA) else None


def mensaje_alerta(cuenta, para, copia, ip, momento):
    asunto = "Alerta de falla de comunicación - %s" % correo.EQUIPO
    cuerpo = ("Alerta de falla de comunicación\n\n"
              "Estado: %s\n"
              "Detalle: el controlador (%s) dejó de responder a la Raspberry %s el %s a las %s.\n\n"
              "No se enviará otra alerta mientras siga caído; si la comunicación vuelve y se cae otra vez, "
              "llegará una nueva.\n\n"
              "Correo automático del registro de %s del Rice Lake 1280.\n"
              % (FALLA, ip, socket.gethostname(), momento.strftime("%d/%m/%Y"), momento.strftime("%H:%M:%S"),
                 correo.EQUIPO))
    return correo.armar_mensaje(cuenta, para, copia, asunto, cuerpo)


def texto_estado(e):
    """'Activo (16:19:58)' para el correo."""
    if e.get("momento") and e["estado"] in (ACTIVO, FALLA):
        return "%s (%s)" % (e["estado"], e["momento"].strftime("%H:%M:%S"))
    return e["estado"]


class ServicioComunicacion:
    def __init__(self, conectar=basedatos.conectar, ping=hacer_ping, reloj=ahora, ruta=None, enviar=None,
                 leer_cuenta=None):
        self.conectar = conectar
        self.ping = ping
        self.reloj = reloj
        self.ruta = ruta
        self.enviar = enviar or correo.enviar
        self.leer_cuenta = leer_cuenta or correo.leer_cuenta
        self.detener = threading.Event()
        self._con = None
        self._cfg = {}
        self._ultimo = estado_guardado(ruta)   # si el servicio se reinicia, sigue desde el último estado
        self._alerta = None                   # momento de la caída cuya alerta falta enviar
        self._intento_alerta = None
        self._alerta_fallida_anotada = False

    def correr(self):
        registro.info("Servicio de estado de comunicación iniciado")
        while not self.detener.is_set():
            inicio = time.monotonic()
            try:
                espera = self.vuelta()
            except Exception:
                registro.error("Error inesperado: %s", traceback.format_exc())
                espera = 5
            self.detener.wait(max(0.2, espera - (time.monotonic() - inicio)))
        registro.info("Servicio de estado de comunicación detenido")

    def _config(self):
        """Lee la configuración; si la base no responde, sigue con la última conocida."""
        try:
            if self._con is None:
                self._con = self.conectar()
            self._cfg = basedatos.config(self._con)
        except (ErrorBase, MySQLdb.Error) as e:
            registro.warning("Base de datos: %s", e)
            try:
                if self._con is not None:
                    self._con.close()
            except Exception:
                pass
            self._con = None
        return self._cfg

    def vuelta(self):
        """Un ping. Devuelve cuántos segundos esperar hasta el siguiente."""
        cfg = self._config()
        if cfg.get("comunicacion_activo") != "1":
            self._ultimo, self._alerta = None, None
            return 2
        vivo = self.ping(cfg.get("ip", ""))
        estado = ACTIVO if vivo else FALLA
        momento = self.reloj()
        if estado != self._ultimo:
            registro.info("Estado de comunicación: %s", estado)
            if estado == FALLA and self._ultimo == ACTIVO:
                self._alerta, self._intento_alerta, self._alerta_fallida_anotada = momento, None, False
            if estado == ACTIVO:
                self._alerta = None
            self._ultimo = estado
        try:
            escribir_estado(estado, momento, self.ruta)
        except OSError as e:
            registro.error("No se pudo escribir el estado: %s", e)
        if self._alerta is not None and (self._intento_alerta is None or
                                         (momento - self._intento_alerta).total_seconds() >= REINTENTO_ALERTA):
            self._enviar_alerta(cfg, momento)
        return segundos_config(cfg)

    def _enviar_alerta(self, cfg, momento):
        self._intento_alerta = momento
        caida = self._alerta
        try:
            para = correo.direcciones(cfg.get("correo_para"))
            copia = correo.direcciones(cfg.get("correo_copia"))
            if not para:
                raise correo.ErrorCorreo("No hay destinatarios para la alerta.")
            cuenta = self.leer_cuenta()
            self.enviar(cuenta, mensaje_alerta(cuenta, para, copia, cfg.get("ip", ""), caida))
        except (correo.ErrorCorreo, ValueError) as e:
            registro.warning("No se pudo enviar la alerta de falla de comunicación: %s", e)
            if not self._alerta_fallida_anotada:
                self._anotar(cfg, caida, "ERROR", "Alerta de falla de comunicación (%s): %s"
                             % (caida.strftime("%H:%M:%S"), e))
                self._alerta_fallida_anotada = True
            return
        self._alerta = None
        registro.info("Alerta de falla de comunicación enviada")
        self._anotar(cfg, caida, "OK", "Alerta de falla de comunicación: el controlador dejó de responder a las %s. "
                     "Enviado a %s%s." % (caida.strftime("%H:%M:%S"), ", ".join(para),
                                         "; copia %s" % ", ".join(copia) if copia else ""))

    def _anotar(self, cfg, caida, resultado, detalle):
        try:
            if self._con is None:
                self._con = self.conectar()
            basedatos.registrar_correo(self._con, enviado_en=self.reloj(), tipo="alerta", pesadas=0,
                                       resultado=resultado, para=cfg.get("correo_para", ""),
                                       copia=cfg.get("correo_copia", ""), detalle=detalle)
        except (ErrorBase, MySQLdb.Error) as e:
            registro.error("No se pudo anotar la alerta: %s", e)
