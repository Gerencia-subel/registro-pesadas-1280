"""Pruebas del estado de comunicación (ping al controlador cada N segundos)."""

import os
import unittest
from datetime import datetime, timedelta

import ayuda
from registro1280 import basedatos, comunicacion


class Estado(unittest.TestCase):
    def tearDown(self):
        if os.path.exists(comunicacion.RUTA):
            os.remove(comunicacion.RUTA)

    def test_estados(self):
        ahora = datetime(2026, 10, 19, 16, 20, 0)
        cfg = {"comunicacion_activo": "1", "comunicacion_segundos": "5"}
        self.assertEqual(comunicacion.leer_estado(cfg, lambda: ahora)["estado"], "Sin dato")
        comunicacion.escribir_estado(comunicacion.ACTIVO, ahora - timedelta(seconds=4))
        e = comunicacion.leer_estado(cfg, lambda: ahora)
        self.assertEqual(comunicacion.texto_estado(e), "Activo (16:19:56)")
        self.assertEqual(comunicacion.leer_estado(cfg, lambda: ahora + timedelta(minutes=5))["estado"], "Sin dato")
        self.assertEqual(comunicacion.leer_estado({"comunicacion_activo": "0"})["estado"], "Desactivado")
        self.assertEqual(oct(os.stat(comunicacion.RUTA).st_mode & 0o777), "0o644")   # la web lo puede leer

    def test_ping_a_una_ip_que_no_existe(self):
        self.assertFalse(comunicacion.hacer_ping("192.0.2.1", espera=1))


class Servicio(unittest.TestCase):
    def setUp(self):
        ayuda.preparar_base()
        self.con = basedatos.conectar()
        basedatos.guardar_config(self.con, ip="10.30.211.141")
        self.pings = []

    def tearDown(self):
        self.con.close()
        if os.path.exists(comunicacion.RUTA):
            os.remove(comunicacion.RUTA)

    def test_ping_cada_n_segundos(self):
        respuestas = [True, False]
        s = comunicacion.ServicioComunicacion(ping=lambda ip: self.pings.append(ip) or respuestas.pop(0))
        self.assertEqual(s.vuelta(), 5)
        cfg = basedatos.config(self.con)
        self.assertEqual(comunicacion.leer_estado(cfg)["estado"], "Activo")
        s.vuelta()
        self.assertEqual(comunicacion.leer_estado(cfg)["estado"], "Falla de comunicación")
        self.assertEqual(self.pings, ["10.30.211.141", "10.30.211.141"])
        basedatos.guardar_config(self.con, comunicacion_activo="0")
        self.assertEqual(s.vuelta(), 2)
        self.assertEqual(len(self.pings), 2)                      # desactivado: no hace ping
        s._con.close()


if __name__ == "__main__":
    unittest.main()
