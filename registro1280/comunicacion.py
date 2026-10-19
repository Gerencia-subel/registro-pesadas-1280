"""Estado de comunicación con el controlador: un ping cada N segundos (por defecto 5).

Corre como servicio aparte (registro1280-comunicacion). Solo hace ping a la IP del
controlador: no usa el puerto 10001, así que no molesta al controlador ni a Captura 1280.
El resultado ("Activo" o "Falla de comunicación") no se guarda en la base: queda en
/run/registro1280/comunicacion.json (en memoria; se borra al apagar la Raspberry) y lo leen
el correo, para poner el último valor en cada envío, y la web.
En la base solo está la configuración: comunicacion_activo y comunicacion_segundos.
"""

import json
import logging
import os
import subprocess
import threading
import time
import traceback
from datetime import datetime

import MySQLdb

from . import basedatos
from .basedatos import ErrorBase
from .reloj import ahora

registro = logging.getLogger("registro1280")

RUTA = os.environ.get("REGISTRO1280_ESTADO", "/run/registro1280/comunicacion.json")
ACTIVO = "Activo"
FALLA = "Falla de comunicación"
DESACTIVADO = "Desactivado"
SIN_DATO = "Sin dato"
FORMATO = "%Y-%m-%d %H:%M:%S"


def segundos_config(cfg):
    try:
        return max(1, int(cfg.get("comunicacion_segundos") or 5))
    except ValueError:
        return 5


def hacer_ping(ip, espera=2):
    """True si el controlador responde al ping."""
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(espera), str(ip)], stdout=subprocess.DEVNULL,
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


def texto_estado(e):
    """'Activo (16:19:58)' para el correo."""
    if e.get("momento") and e["estado"] in (ACTIVO, FALLA):
        return "%s (%s)" % (e["estado"], e["momento"].strftime("%H:%M:%S"))
    return e["estado"]


class ServicioComunicacion:
    def __init__(self, conectar=basedatos.conectar, ping=hacer_ping, reloj=ahora, ruta=None):
        self.conectar = conectar
        self.ping = ping
        self.reloj = reloj
        self.ruta = ruta
        self.detener = threading.Event()
        self._con = None
        self._cfg = {}
        self._ultimo = None

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
            return 2
        vivo = self.ping(cfg.get("ip", ""))
        estado = ACTIVO if vivo else FALLA
        if estado != self._ultimo:
            registro.info("Estado de comunicación: %s", estado)
            self._ultimo = estado
        try:
            escribir_estado(estado, self.reloj(), self.ruta)
        except OSError as e:
            registro.error("No se pudo escribir el estado: %s", e)
        return segundos_config(cfg)
