"""Pruebas de la web de consulta (necesitan MariaDB)."""

import unittest

from werkzeug.security import generate_password_hash

import ayuda
from registro1280 import basedatos
from registro1280.guardado import guardar
from registro1280.web import crear_app
from simulador_1280 import Simulador

AJUSTES = {"usuario": "intradevco", "clave_hash": generate_password_hash("intradevco", method="pbkdf2:sha256:1000"),
           "secreto": "prueba" * 8, "puerto": 8081}


class Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ayuda.preparar_base()
        cls.sim = Simulador(iniciales=250)
        con = basedatos.conectar()
        ayuda.con_simulador(con, cls.sim)
        guardar(con, "manual")
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls.sim.cerrar()

    def setUp(self):
        self.app = crear_app(AJUSTES)
        self.cliente = self.app.test_client()

    def ingresar(self, clave="intradevco", ip="10.0.0.5", **kw):
        return self.cliente.post("/ingresar", data={"usuario": "intradevco", "clave": clave},
                                 environ_base={"REMOTE_ADDR": ip}, **kw)

    def test_sin_sesion(self):
        r = self.cliente.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/ingresar", r.headers["Location"])
        self.assertEqual(self.cliente.get("/api/pesadas").status_code, 401)
        self.assertEqual(self.cliente.get("/ingresar").status_code, 200)
        r = self.cliente.get("/static/estilo.css")
        self.assertEqual(r.status_code, 200)
        r.close()

    def test_clave_incorrecta_y_bloqueo(self):
        self.assertEqual(self.ingresar("mala").status_code, 401)
        for _ in range(4):
            self.ingresar("mala")
        r = self.ingresar("intradevco")          # bloqueado aunque ahora la clave sea correcta
        self.assertIn("Demasiados intentos", r.get_data(as_text=True))
        self.assertEqual(self.ingresar("intradevco", ip="10.0.0.9").status_code, 302)  # otra PC sí entra

    def test_bloqueo_por_ip_real_detras_de_funnel(self):
        for _ in range(5):
            self.cliente.post("/ingresar", data={"usuario": "intradevco", "clave": "x"},
                              environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"X-Forwarded-For": "200.1.1.1"})
        r = self.cliente.post("/ingresar", data={"usuario": "intradevco", "clave": "intradevco"},
                              environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"X-Forwarded-For": "190.2.2.2"})
        self.assertEqual(r.status_code, 302)

    def test_consulta(self):
        self.assertEqual(self.ingresar().status_code, 302)
        self.assertIn("Registro de pesadas", self.cliente.get("/").get_data(as_text=True))
        r = self.cliente.get("/api/resumen").get_json()
        self.assertEqual((r["ok"], r["total"]), (True, 250))
        self.assertEqual(r["ultima_descarga"]["resultado"], "OK")
        r = self.cliente.get("/api/pesadas?por_pagina=50&pagina=2").get_json()
        self.assertEqual((r["total"], r["paginas"], r["pagina"], len(r["filas"])), (250, 5, 2, 50))
        fila = r["filas"][0]
        self.assertTrue(fila["Peso"].endswith(" kg"))
        self.assertEqual(set(fila), {"nro", "ID", "cor", "Producto", "Peso", "Acum", "FechaI", "FechaF",
                                     "Turno", "Estado", "guardado_en", "actualizado_en"})
        r = self.cliente.get("/api/pesadas?id=%d" % fila["ID"]).get_json()
        self.assertEqual(r["total"], 1)
        self.assertEqual(self.cliente.get("/api/pesadas?id=x").status_code, 400)
        self.assertEqual(self.cliente.get("/api/pesadas?desde=2026-02-30").status_code, 400)
        r = self.cliente.get("/api/pesadas?producto=%27%20OR%201%3D1%20--").get_json()
        self.assertEqual(r["total"], 0)
        self.assertEqual(len(self.cliente.get("/api/descargas").get_json()["descargas"]), 1)

    def test_no_se_puede_modificar(self):
        self.ingresar()
        for metodo in ("post", "put", "delete", "patch"):
            r = getattr(self.cliente, metodo)("/api/pesadas")
            self.assertEqual(r.status_code, 405, metodo)

    def test_salir(self):
        self.ingresar()
        self.cliente.get("/salir")
        self.assertEqual(self.cliente.get("/api/resumen").status_code, 401)


if __name__ == "__main__":
    unittest.main()
