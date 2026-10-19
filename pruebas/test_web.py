"""Pruebas de la web de consulta (necesitan MariaDB)."""

import json
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
                                     "Turno", "Estado", "guardado_en", "actualizado_en", "correo"})
        r = self.cliente.get("/api/pesadas?id=%d" % fila["ID"]).get_json()
        self.assertEqual(r["total"], 1)
        self.assertEqual(self.cliente.get("/api/pesadas?id=x").status_code, 400)
        self.assertEqual(self.cliente.get("/api/pesadas?desde=2026-02-30").status_code, 400)
        r = self.cliente.get("/api/pesadas?producto=%27%20OR%201%3D1%20--").get_json()
        self.assertEqual(r["total"], 0)
        self.assertEqual(len(self.cliente.get("/api/descargas").get_json()["descargas"]), 1)

    def test_correos(self):
        from datetime import datetime
        con = basedatos.conectar()
        basedatos.registrar_correo(con, enviado_en=datetime(2026, 10, 19, 9, 15, 2), tipo="tramo",
                                   desde=datetime(2026, 10, 19, 9, 10), hasta=datetime(2026, 10, 19, 9, 15),
                                   pesadas=0, resultado="OK", para="ktomasl@alicorp.com.pe",
                                   detalle="Gmail no aceptó: ktomasl@alicorp.com.pe (550)")
        basedatos.guardar_config(con, correo_activo="1", correo_minutos="5", correo_proximo="2026-10-19 09:20:00")
        try:
            self.ingresar()
            r = self.cliente.get("/api/resumen").get_json()
            self.assertEqual((r["correo"]["activo"], r["correo"]["minutos"]), (True, "5"))
            self.assertEqual(r["correo"]["proximo"], "19/10/2026 09:20")
            self.assertEqual(r["correo"]["ultimo"]["tramo"], "19/10/2026 09:10 - 09:15")
            c = self.cliente.get("/api/correos").get_json()["correos"]
            self.assertEqual((len(c), c[0]["resultado"], c[0]["tipo"]), (1, "OK", "tramo"))
            self.assertEqual(c[0]["detalle"], "Gmail no aceptó: ktomasl@alicorp.com.pe (550)")
            self.assertEqual(self.cliente.post("/api/correos", headers={"X-CSRF-Token": self.csrf()}).status_code, 405)
        finally:
            basedatos.guardar_config(con, correo_activo="0", correo_proximo="")
            con.cursor().execute("TRUNCATE correos")
            con.close()

    def csrf(self):
        with self.cliente.session_transaction() as sesion:
            return sesion["csrf"]

    def test_no_se_puede_modificar(self):
        self.ingresar()
        self.assertEqual(self.cliente.post("/api/pesadas").status_code, 403)       # sin la ficha de la página
        cabecera = {"X-CSRF-Token": self.csrf()}
        for metodo in ("post", "put", "delete", "patch"):
            r = getattr(self.cliente, metodo)("/api/pesadas", headers=cabecera)
            self.assertEqual(r.status_code, 405, metodo)

    def test_configuracion_y_pedidos(self):
        con = basedatos.conectar()
        basedatos.guardar_config(con, correo_cuenta="industria@gmail.com", correo_para="ktomasl@alicorp.com.pe")
        try:
            self.ingresar()
            pagina = self.cliente.get("/configuracion").get_data(as_text=True)
            self.assertIn("Envío por correo", pagina)
            self.assertIn(self.csrf(), pagina)
            r = self.cliente.get("/api/configuracion").get_json()
            self.assertEqual((r["correo"]["cuenta"], r["correo"]["para"]), ("industria@gmail.com", ["ktomasl@alicorp.com.pe"]))
            self.assertNotIn("clave", json.dumps(r))
            cabecera = {"X-CSRF-Token": self.csrf()}
            sin_ficha = self.cliente.post("/api/pedidos", json={"tipo": "bucle", "datos": "5"})
            self.assertEqual(sin_ficha.status_code, 403)
            self.assertEqual(self.cliente.post("/api/pedidos", json={"tipo": "borrar_todo"},
                                               headers=cabecera).status_code, 400)
            r = self.cliente.post("/api/pedidos", json={"tipo": "correo_cuenta", "usuario": "otra@gmail.com",
                                                        "clave": "abcd efgh ijkl mnop"}, headers=cabecera).get_json()
            self.assertTrue(r["ok"])
            p = self.cliente.get("/api/pedidos/%d" % r["nro"]).get_json()
            self.assertEqual(p["estado"], "PENDIENTE")
            fila = basedatos.pedidos_pendientes(con)[0]
            self.assertEqual((fila["tipo"], json.loads(fila["datos"])["usuario"]), ("correo_cuenta", "otra@gmail.com"))
            self.assertEqual(self.cliente.get("/api/pedidos/999999").status_code, 404)
            r = self.cliente.get("/api/comunicacion").get_json()
            self.assertEqual((r["activo"], r["segundos"], r["estado"]), (True, "5", "Sin dato"))
            from registro1280 import comunicacion
            from registro1280.reloj import ahora
            comunicacion.escribir_estado(comunicacion.FALLA, ahora())
            self.assertEqual(self.cliente.get("/api/comunicacion").get_json()["estado"], "Falla de comunicación")
            basedatos.guardar_config(con, correo_clave="abcdefghijklmnop")
            self.assertEqual(self.cliente.get("/api/configuracion/clave").get_json()["clave"], "abcdefghijklmnop")
            r = self.cliente.post("/api/pedidos", json={"tipo": "correo_destinatarios", "para": ["a@x.com", " "],
                                                        "copia": ["b@y.pe"]}, headers=cabecera).get_json()
            fila = [p for p in basedatos.pedidos_pendientes(con) if p["nro"] == r["nro"]][0]
            self.assertEqual(json.loads(fila["datos"]), {"para": ["a@x.com"], "copia": ["b@y.pe"]})
            self.assertEqual(self.cliente.get("/api/configuracion").get_json()["correo"]["para"],
                             ["ktomasl@alicorp.com.pe"])
        finally:
            con.cursor().execute("TRUNCATE pedidos")
            basedatos.guardar_config(con, correo_cuenta="", correo_para="", correo_clave="")
            con.close()

    def test_salir(self):
        self.ingresar()
        self.cliente.get("/salir")
        self.assertEqual(self.cliente.get("/api/resumen").status_code, 401)


if __name__ == "__main__":
    unittest.main()
