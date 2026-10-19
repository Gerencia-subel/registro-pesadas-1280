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


class Alerta(unittest.TestCase):
    def setUp(self):
        ayuda.preparar_base()
        self.con = basedatos.conectar()
        basedatos.guardar_config(self.con, ip="10.30.211.141", correo_para="ktomasl@alicorp.com.pe",
                                 correo_copia="subelrobotin@gmail.com")
        self.reloj = ayuda.RelojFalso(datetime(2026, 10, 19, 16, 40, 0))
        self.enviados = []
        self.falla_envio = []

    def tearDown(self):
        self.con.close()
        if os.path.exists(comunicacion.RUTA):
            os.remove(comunicacion.RUTA)

    def enviar(self, cuenta, mensaje):
        if self.falla_envio:
            self.falla_envio.pop()
            raise comunicacion.correo.ErrorCorreo("Sin internet")
        self.enviados.append(mensaje)

    def servicio(self, respuestas):
        s = comunicacion.ServicioComunicacion(ping=lambda ip: respuestas.pop(0), reloj=self.reloj, enviar=self.enviar,
                                              leer_cuenta=lambda: {"usuario": "industria@gmail.com"})
        return s

    def correr(self, s, veces):
        for _ in range(veces):
            s.vuelta()
            self.reloj.avanzar(seconds=5)

    def test_una_alerta_por_cada_caida(self):
        s = self.servicio([True, True, False, False, False, False, True, True, False, False])
        self.correr(s, 10)
        self.assertEqual(len(self.enviados), 2)
        m = self.enviados[0]
        self.assertEqual(m["Subject"], "Alerta de falla de comunicación - Balanza de Coches - PDL")
        self.assertEqual(m["To"], "ktomasl@alicorp.com.pe")
        self.assertEqual(m["Cc"], "subelrobotin@gmail.com")
        cuerpo = m.get_body().get_content()
        self.assertIn("Estado: Falla de comunicación", cuerpo)
        self.assertIn("19/10/2026 a las 16:40:10", cuerpo)             # el tercer ping (16:40:10) falló
        self.assertIn("16:40:40", self.enviados[1].get_body().get_content())
        self.assertEqual([c["tipo"] for c in basedatos.correos(self.con, 5)], ["alerta", "alerta"])
        s._con.close()

    def test_si_arranca_caido_no_avisa(self):
        s = self.servicio([False, False, True, False])
        self.correr(s, 4)
        self.assertEqual(len(self.enviados), 1)                         # solo la caída después de estar activo

    def test_reinicio_del_servicio_no_pierde_la_caida(self):
        self.correr(self.servicio([True]), 1)
        self.correr(self.servicio([False]), 1)                           # otro proceso (reinicio)
        self.assertEqual(len(self.enviados), 1)

    def test_sin_internet_reintenta_cada_minuto(self):
        self.falla_envio = [1, 1]
        s = self.servicio([True] + [False] * 30)
        self.correr(s, 31)
        self.assertEqual(len(self.enviados), 1)
        self.assertEqual([c["resultado"] for c in basedatos.correos(self.con, 5)], ["OK", "ERROR"])
        s._con.close()


if __name__ == "__main__":
    unittest.main()
