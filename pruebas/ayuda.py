"""Ayuda para las pruebas: base de datos de prueba y simulador del controlador.

Las pruebas usan una base aparte (registro1280_prueba), nunca la real.
Necesitan MariaDB y permisos de administrador:  sudo python3 -m unittest discover -s pruebas
"""

import atexit
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("REGISTRO1280_BASE", "registro1280_prueba")
# La cuenta de correo de las pruebas va en un archivo temporal: nunca se toca /etc/registro1280/correo.ini.
_CARPETA_TEMPORAL = tempfile.mkdtemp(prefix="registro1280_pruebas_")
os.environ["REGISTRO1280_CORREO_INI"] = os.path.join(_CARPETA_TEMPORAL, "correo.ini")
atexit.register(shutil.rmtree, _CARPETA_TEMPORAL, True)
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if not os.environ.get("REGISTRO1280_VER_REGISTRO"):
    logging.getLogger("registro1280").setLevel(logging.CRITICAL)

from registro1280 import basedatos  # noqa: E402
from simulador_1280 import Simulador  # noqa: E402,F401

BASE = basedatos.BASE
assert BASE != "registro1280", "Las pruebas no deben usar la base real"
from registro1280 import correo as _correo  # noqa: E402
assert _correo.RUTA.startswith(_CARPETA_TEMPORAL), "Las pruebas no deben usar la cuenta de correo real"

_preparada = False


def _mariadb(sql):
    return subprocess.run(["mariadb", "--socket=" + basedatos.SOCKET], input=sql.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def preparar_base():
    """Crea la base de prueba (una vez) y la deja vacía. Si no hay MariaDB o permisos, salta la prueba."""
    global _preparada
    if not _preparada:
        with open(os.path.join(RAIZ, "registro1280", "esquema.sql"), encoding="utf-8") as f:
            esquema = f.read().replace("registro1280", BASE)
        try:
            r = _mariadb("DROP DATABASE IF EXISTS %s;\n%s" % (BASE, esquema))
        except FileNotFoundError:
            raise unittest.SkipTest("MariaDB no está instalado")
        if r.returncode != 0:
            raise unittest.SkipTest("Sin acceso a MariaDB (usar sudo): %s" % r.stderr.decode()[:200])
        _preparada = True
        atexit.register(lambda: _mariadb("DROP DATABASE IF EXISTS %s;" % BASE))
    r = _mariadb("USE %s; TRUNCATE pesadas; TRUNCATE descargas; TRUNCATE rechazadas; TRUNCATE correos; TRUNCATE pedidos; "
                 "UPDATE configuracion SET valor='0' WHERE clave IN ('bucle_activo','correo_activo'); "
                 "UPDATE configuracion SET valor='5' WHERE clave IN ('bucle_minutos','correo_minutos'); "
                 "UPDATE configuracion SET valor='' WHERE clave IN ('bucle_cambiado','latido','ultimo_respaldo',"
                 "'correo_para','correo_copia','correo_cuenta','correo_clave','correo_desde','correo_proximo','correo_detenido',"
                 "'correo_latido');"
                 % BASE)
    if r.returncode != 0:
        raise unittest.SkipTest("No se pudo vaciar la base de prueba: %s" % r.stderr.decode()[:200])


def con_simulador(con, sim):
    basedatos.guardar_config(con, ip="127.0.0.1", puerto=str(sim.puerto))


class RelojFalso:
    def __init__(self, inicio):
        self.t = inicio

    def __call__(self):
        return self.t

    def avanzar(self, **kw):
        from datetime import timedelta
        self.t += timedelta(**kw)
