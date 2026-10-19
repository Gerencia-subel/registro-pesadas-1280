"""Pruebas del bucle automático con reloj simulado (necesitan MariaDB)."""

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

import ayuda
from registro1280 import basedatos
from registro1280.bucle import Bucle
from simulador_1280 import Simulador


class BucleAutomatico(unittest.TestCase):
    def setUp(self):
        ayuda.preparar_base()
        self.sim = Simulador(iniciales=100)
        self.con = basedatos.conectar()
        ayuda.con_simulador(self.con, self.sim)
        self.reloj = ayuda.RelojFalso(datetime(2026, 10, 12, 9, 0, 0))
        self.carpeta = tempfile.mkdtemp(prefix="respaldos_")
        self.bucle = Bucle(reloj=self.reloj, carpeta_respaldos=self.carpeta, respaldar=False)

    def tearDown(self):
        self.bucle._cerrar()
        self.sim.cerrar()
        self.con.close()
        shutil.rmtree(self.carpeta, ignore_errors=True)

    def descargas(self):
        return len(basedatos.descargas(self.con, 1000))

    def activar(self, minutos):
        basedatos.guardar_config(self.con, bucle_activo="1", bucle_minutos=str(minutos),
                                 bucle_cambiado=self.reloj().strftime("%Y-%m-%d %H:%M:%S"))

    def test_detenido_no_descarga(self):
        self.bucle.vuelta()
        self.reloj.avanzar(minutes=30)
        self.bucle.vuelta()
        self.assertEqual(self.descargas(), 0)
        self.assertNotIn("DB.DATA#2", self.sim.comandos)
        self.assertTrue(basedatos.config(self.con)["latido"])

    def test_guarda_al_activar_y_cada_intervalo(self):
        self.activar(5)
        self.bucle.vuelta()
        self.assertEqual(self.descargas(), 1)
        self.assertEqual(basedatos.total(self.con), 100)
        self.reloj.avanzar(minutes=4, seconds=59)
        self.bucle.vuelta()
        self.assertEqual(self.descargas(), 1)
        for _ in range(3):
            self.sim.agregar()
        self.reloj.avanzar(seconds=1)
        self.bucle.vuelta()
        self.assertEqual(self.descargas(), 2)
        self.assertEqual(basedatos.total(self.con), 103)

    def test_cambio_de_intervalo_y_detener(self):
        self.activar(5)
        self.bucle.vuelta()
        self.reloj.avanzar(minutes=1)
        self.activar(1)             # "bucle 1": descarga de inmediato
        self.bucle.vuelta()
        self.assertEqual(self.descargas(), 2)
        self.reloj.avanzar(minutes=1)
        self.bucle.vuelta()
        self.assertEqual(self.descargas(), 3)
        basedatos.guardar_config(self.con, bucle_activo="0")
        self.reloj.avanzar(minutes=10)
        self.bucle.vuelta()
        self.assertEqual(self.descargas(), 3)

    def test_falla_del_controlador_no_detiene_el_bucle(self):
        self.activar(1)
        self.sim.modo = "mudo"
        self.bucle.vuelta()
        d = basedatos.descargas(self.con, 1)[0]
        self.assertEqual(d["resultado"], "ERROR")
        self.sim.modo = "normal"
        self.reloj.avanzar(minutes=1)
        self.bucle.vuelta()
        self.assertEqual(basedatos.descargas(self.con, 1)[0]["resultado"], "OK")
        self.assertEqual(basedatos.total(self.con), 100)

    def test_mantenimiento_diario(self):
        basedatos.registrar_descarga(self.con, inicio=datetime(2026, 6, 1), origen="bucle", resultado="OK")
        self.bucle.vuelta()
        self.assertEqual(basedatos.config(self.con)["ultimo_respaldo"], "2026-10-12")
        self.assertEqual(self.descargas(), 0)   # la descarga de junio (más de 90 días) se borró

    def test_respaldo_con_mariadb_dump(self):
        self.bucle.respaldar = True
        self.bucle.vuelta()
        archivos = os.listdir(self.carpeta)
        self.assertEqual(archivos, ["registro1280_20261012.sql.gz"])


if __name__ == "__main__":
    unittest.main()
