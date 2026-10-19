"""Servicio del bucle: guarda automáticamente cada N minutos.

Corre siempre (systemd lo arranca al encender la Raspberry). El comando "bucle"
solo cambia la configuración en la base; este servicio la lee cada 2 segundos:
  bucle_activo   1 = guardar automáticamente, 0 = detenido
  bucle_minutos  cada cuántos minutos
  bucle_cambiado se actualiza con cada "bucle N" para empezar de inmediato
Además anota un latido cada 30 s (para saber que está funcionando), hace un
respaldo diario de la base a partir de las 03:00 (se guardan 7) y borra del
historial de descargas lo que tenga más de 90 días (las pesadas nunca se borran).
"""

import glob
import gzip
import logging
import os
import shutil
import subprocess
import threading
import time
import traceback
from datetime import timedelta

import MySQLdb

from . import basedatos
from .basedatos import ErrorBase
from .controlador import ErrorControlador, leer_pesadas
from .guardado import guardar
from .reloj import ahora

registro = logging.getLogger("registro1280")

LATIDO_CADA = 30
HORA_MANTENIMIENTO = 3
RESPALDOS_A_GUARDAR = 7
DIAS_HISTORIAL = 90


class Bucle:
    def __init__(self, conectar=basedatos.conectar, leer=leer_pesadas, reloj=ahora,
                 carpeta_respaldos="/var/lib/registro1280/respaldos", respaldar=True):
        self.conectar = conectar
        self.leer = leer
        self.reloj = reloj
        self.carpeta_respaldos = carpeta_respaldos
        self.respaldar = respaldar
        self.detener = threading.Event()
        self._con = None
        self._firma = None
        self._proximo = None
        self._ultimo_latido = 0.0

    # ------------------------------------------------------------------
    def correr(self):
        registro.info("Bucle iniciado")
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
        registro.info("Bucle detenido")

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
            basedatos.guardar_config(con, latido=self.reloj().strftime("%Y-%m-%d %H:%M:%S"))
            self._ultimo_latido = time.monotonic()
            self._mantenimiento(con)
        cfg = basedatos.config(con)
        activo = cfg.get("bucle_activo") == "1"
        try:
            minutos = max(1, int(cfg.get("bucle_minutos") or 5))
        except ValueError:
            minutos = 5
        firma = (activo, minutos, cfg.get("bucle_cambiado", ""))
        ahora_ = self.reloj()
        if firma != self._firma:
            self._firma = firma
            self._proximo = ahora_ if activo else None
            if activo:
                registro.info("Bucle activo: cada %d min", minutos)
            else:
                registro.info("Bucle detenido por configuración")
        if not activo:
            return 2
        if ahora_ < self._proximo:
            return (self._proximo - ahora_).total_seconds()
        inicio = ahora_
        try:
            r = guardar(con, "bucle", reintentos=1, leer=self.leer, reloj=self.reloj,
                        dormir=lambda s: self.detener.wait(s))
            registro.info("Guardado: %d leídas, %d nuevas, %d actualizadas, total %d (%.1f s)",
                          r["leidas"], r["nuevas"], r["actualizadas"], r["total"], r["segundos"])
        except ErrorControlador as e:
            registro.warning("No se pudo leer el controlador: %s", e)
        except ErrorBase as e:
            registro.error("%s", e)
        siguiente = inicio + timedelta(minutes=minutos)
        ahora_ = self.reloj()
        while siguiente <= ahora_:   # si la descarga tardó más que el intervalo, no se acumulan
            siguiente += timedelta(minutes=minutos)
        self._proximo = siguiente
        return 0.5

    # ------------------------------------------------------------------
    def _mantenimiento(self, con):
        momento = self.reloj()
        if momento.hour < HORA_MANTENIMIENTO:
            return
        hoy = momento.strftime("%Y-%m-%d")
        if basedatos.config(con).get("ultimo_respaldo") == hoy:
            return
        if self.respaldar:
            try:
                self._respaldo(momento)
            except Exception as e:
                registro.error("No se pudo hacer el respaldo diario: %s", e)
        try:
            borradas = basedatos.borrar_descargas_antiguas(con, momento - timedelta(days=DIAS_HISTORIAL))
            if borradas:
                registro.info("Historial: %d descargas de más de %d días borradas", borradas, DIAS_HISTORIAL)
        except MySQLdb.Error as e:
            registro.error("No se pudo limpiar el historial de descargas: %s", e)
        basedatos.guardar_config(con, ultimo_respaldo=hoy)

    def _respaldo(self, momento):
        os.makedirs(self.carpeta_respaldos, exist_ok=True)
        destino = os.path.join(self.carpeta_respaldos, "registro1280_%s.sql.gz" % momento.strftime("%Y%m%d"))
        temporal = destino + ".tmp"
        programa = shutil.which("mariadb-dump") or shutil.which("mysqldump")
        if not programa:
            raise RuntimeError("no está instalado mariadb-dump")
        with gzip.open(temporal, "wb") as salida:
            proceso = subprocess.Popen(
                [programa, "--single-transaction", "--no-tablespaces", "--skip-lock-tables",
                 "--socket=" + basedatos.SOCKET, basedatos.BASE],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for trozo in iter(lambda: proceso.stdout.read(65536), b""):
                salida.write(trozo)
            _, error = proceso.communicate()
        if proceso.returncode != 0:
            os.remove(temporal)
            raise RuntimeError(error.decode("utf-8", "replace").strip()[:300])
        os.replace(temporal, destino)
        respaldos = sorted(glob.glob(os.path.join(self.carpeta_respaldos, "registro1280_*.sql.gz")))
        for viejo in respaldos[:-RESPALDOS_A_GUARDAR]:
            os.remove(viejo)
        registro.info("Respaldo diario: %s", destino)
