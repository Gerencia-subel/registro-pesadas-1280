"""Pruebas de la web de consulta (necesitan MariaDB)."""

import json
import io
import unittest
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from werkzeug.security import generate_password_hash

import ayuda
from registro1280 import accesos, basedatos
from registro1280.guardado import guardar
from registro1280.controlador import Pesada
from registro1280.web import crear_app
from simulador_1280 import Simulador

AJUSTES = {"usuario": "intradevco", "clave_hash": generate_password_hash("112233", method="pbkdf2:sha256:1000"),
           "secreto": "prueba" * 8, "puerto": 8081}


class Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ayuda.preparar_base()
        cls.sim = Simulador(iniciales=250)
        con = basedatos.conectar()
        accesos.preparar(con, AJUSTES)
        ayuda.con_simulador(con, cls.sim)
        guardar(con, "manual")
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls.sim.cerrar()

    def setUp(self):
        self.app = crear_app(AJUSTES)
        self.cliente = self.app.test_client()

    def ingresar(self, clave="112233", ip="10.0.0.5", **kw):
        return self.cliente.post("/ingresar", data={"usuario": "intradevco", "clave": clave},
                                 environ_base={"REMOTE_ADDR": ip}, **kw)

    def test_sin_sesion(self):
        r = self.cliente.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/ingresar", r.headers["Location"])
        self.assertEqual(self.cliente.get("/api/pesadas").status_code, 401)
        self.assertEqual(self.cliente.get("/api/correos").status_code, 401)
        self.assertEqual(self.cliente.get("/configuracion").status_code, 302)
        self.assertEqual(self.cliente.get("/ingresar").status_code, 200)
        r = self.cliente.get("/static/estilo.css")
        self.assertEqual(r.status_code, 200)
        r.close()

    def test_clave_incorrecta_y_bloqueo(self):
        self.assertEqual(self.ingresar("mala").status_code, 401)
        for _ in range(4):
            self.ingresar("mala")
        r = self.ingresar("112233")          # bloqueado aunque ahora la clave sea correcta
        self.assertIn("Demasiados intentos", r.get_data(as_text=True))
        self.assertEqual(self.ingresar("112233", ip="10.0.0.9").status_code, 302)  # otra PC sí entra

    def test_bloqueo_por_ip_real_detras_de_funnel(self):
        for _ in range(5):
            self.cliente.post("/ingresar", data={"usuario": "intradevco", "clave": "x"},
                              environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"X-Forwarded-For": "200.1.1.1"})
        r = self.cliente.post("/ingresar", data={"usuario": "intradevco", "clave": "112233"},
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

    def test_secciones_en_un_solo_documento_y_enlaces_directos(self):
        class Pagina(HTMLParser):
            def __init__(self):
                super().__init__()
                self.ids = []
                self.vistas = {}
                self.cuerpos = []

            def handle_starttag(self, tag, attrs):
                datos = dict(attrs)
                if "id" in datos:
                    self.ids.append(datos["id"])
                    if datos["id"].startswith("vista-"):
                        self.vistas[datos["id"]] = datos
                if tag == "body":
                    self.cuerpos.append(datos)

        self.ingresar()
        for ruta, activa, oculta in (("/", "pesadas", "configuracion"),
                                     ("/configuracion", "configuracion", "pesadas")):
            r = self.cliente.get(ruta)
            self.assertEqual(r.status_code, 200)
            html = r.get_data(as_text=True)
            pagina = Pagina()
            pagina.feed(html)
            self.assertEqual(len(pagina.ids), len(set(pagina.ids)))
            self.assertEqual(pagina.cuerpos, [{"data-vista": activa}])
            self.assertNotIn("hidden", pagina.vistas["vista-" + activa])
            self.assertIn("hidden", pagina.vistas["vista-" + oculta])
            self.assertIn(self.csrf(), html)
            self.assertIn("navegacion.js", html)
            self.assertEqual(html.count('<header class="barra">'), 1)

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
            self.assertEqual(c[0]["detalle"], "Enviado a ktomasl@alicorp.com.pe.")
            self.assertEqual(self.cliente.post("/api/correos", headers={"X-CSRF-Token": self.csrf()}).status_code, 405)
        finally:
            basedatos.guardar_config(con, correo_activo="0", correo_proximo="")
            con.cursor().execute("TRUNCATE correos")
            con.close()

    def test_detalle_breve_incluso_en_correos_anteriores(self):
        con = basedatos.conectar()
        viejo = ("Enviado a anterior@empresa.com; copia copia@empresa.com. "
                 "Aviso: no se pudo leer el controlador (Sin respuesta del controlador en "
                 "10.30.211.141:10001). Las pesadas que falten se enviarán en el siguiente correo.")
        alerta = "Alerta de falla de comunicación: el controlador dejó de responder a las 16:39:41. Enviado a anterior@empresa.com."
        error = "Gmail rechazó la cuenta o la contraseña de aplicación."
        try:
            def registrar(tipo, resultado, detalle, copia=""):
                return basedatos.registrar_correo(
                    con, enviado_en=datetime(2026, 10, 19, 16, 40), tipo=tipo, resultado=resultado,
                    desde=None if tipo == "alerta" else datetime(2026, 10, 19, 16, 35),
                    hasta=None if tipo == "alerta" else datetime(2026, 10, 19, 16, 40),
                    pesadas=0, para="anterior@empresa.com", copia=copia, detalle=detalle)

            n_tramo = registrar("tramo", "OK", viejo, "copia@empresa.com")
            n_alerta = registrar("alerta", "OK", alerta)
            n_error = registrar("tramo", "ERROR", error)
            n_prueba = registrar("prueba", "OK", "Prueba enviada; sin archivo: no se pudo leer el controlador")
            self.ingresar()
            filas = {c["nro"]: c for c in self.cliente.get("/api/correos").get_json()["correos"]}
            self.assertEqual(filas[n_tramo]["detalle"], "Enviado a anterior@empresa.com; copia copia@empresa.com.")
            self.assertEqual(filas[n_prueba]["detalle"], "Enviado a anterior@empresa.com.")
            self.assertEqual((filas[n_alerta]["tramo"], filas[n_alerta]["detalle"]), ("", alerta))
            self.assertEqual(filas[n_error]["detalle"], error)
            self.assertEqual(filas[n_tramo]["tramo"], "19/10/2026 16:35 - 16:40")
            self.assertEqual(self.cliente.get("/api/resumen").get_json()["correo"]["ultimo"]["detalle"],
                             "Enviado a anterior@empresa.com.")
            originales = {c["nro"]: c for c in basedatos.correos(con)}
            self.assertEqual(originales[n_tramo]["detalle"], viejo)
        finally:
            con.cursor().execute("TRUNCATE correos")
            con.commit()
            con.close()

    def test_correos_paginados_sin_limite_de_50(self):
        con = basedatos.conectar()
        try:
            for i in range(57):
                basedatos.registrar_correo(con, enviado_en=datetime(2026, 10, 19, 9, i),
                                           tipo="alerta" if i % 2 else "tramo", pesadas=i,
                                           resultado="ERROR" if i == 0 else "OK")
            self.ingresar()
            primera = self.cliente.get("/api/correos").get_json()
            self.assertEqual((primera["total"], primera["pagina"], primera["paginas"],
                              primera["por_pagina"], len(primera["correos"])), (57, 1, 12, 5, 5))
            ids = []
            for pagina in range(1, 13):
                r = self.cliente.get("/api/correos?pagina=%d" % pagina).get_json()
                ids += [c["nro"] for c in r["correos"]]
            self.assertEqual(len(ids), 57)
            self.assertEqual(ids, sorted(set(ids), reverse=True))
            ultima = self.cliente.get("/api/correos?pagina=9999").get_json()
            self.assertEqual((ultima["pagina"], len(ultima["correos"])), (12, 2))
            self.assertEqual(ultima["correos"][-1]["resultado"], "ERROR")
            for tamano in (5, 25, 50, 100, 500, 1000):
                r = self.cliente.get("/api/correos?por_pagina=%d" % tamano).get_json()
                self.assertEqual((r["por_pagina"], len(r["correos"])), (tamano, min(57, tamano)))
            for consulta in ("pagina=x", "por_pagina=x", "pagina=1.5"):
                self.assertEqual(self.cliente.get("/api/correos?" + consulta).status_code, 400)
            r = self.cliente.get("/api/correos?pagina=-1&por_pagina=999").get_json()
            self.assertEqual((r["pagina"], r["por_pagina"]), (1, 5))
        finally:
            con.cursor().execute("TRUNCATE correos")
            con.commit()
            con.close()

    def test_correos_sin_registros(self):
        self.ingresar()
        r = self.cliente.get("/api/correos?pagina=12").get_json()
        self.assertEqual((r["ok"], r["total"], r["pagina"], r["paginas"], r["correos"]),
                         (True, 0, 1, 1, []))

    def test_filtros_por_fecha_y_hora(self):
        con = basedatos.conectar()
        fechas = [datetime(2020, 1, 2, 10, 14, 59), datetime(2020, 1, 2, 10, 15),
                  datetime(2020, 1, 2, 10, 15, 59), datetime(2020, 1, 2, 10, 16),
                  datetime(2020, 1, 2, 23, 59), datetime(2020, 1, 3, 0, 0)]
        pesadas = [Pesada(900000 + i, i, "Filtro horario", Decimal("300"), Decimal("300"),
                          fecha, fecha, 1, 0) for i, fecha in enumerate(fechas)]
        try:
            basedatos.guardar_pesadas(con, pesadas, [], datetime(2020, 1, 3))
            self.ingresar()

            def consultar(consulta):
                return self.cliente.get("/api/pesadas?producto=Filtro+horario&" + consulta).get_json()

            r = consultar("desde=2020-01-02T10:15&hasta=2020-01-02T10:15")
            self.assertEqual(r["total"], 2)  # incluye los segundos del último minuto
            self.assertEqual({f["ID"] for f in r["filas"]}, {900001, 900002})
            self.assertEqual(consultar("desde=2020-01-02T10:15&hasta=2020-01-02T10:15&id=900001")["total"], 1)
            self.assertEqual(consultar("desde=2020-01-02T23:59&hasta=2020-01-03T00:00")["total"], 2)
            self.assertEqual(consultar("desde=2020-01-02&hasta=2020-01-02")["total"], 5)
            self.assertEqual(consultar("hasta=2020-01-02T10:15")["total"], 3)
            self.assertEqual(consultar("desde=2020-01-03T00:00")["total"], 1)
            self.assertEqual(consultar("desde=2021-01-01T00:00")["total"], 0)
            for consulta in ("desde=2020-01-02T10:15&hasta=2020-01-02T10:15",
                             "desde=2020-01-02T23:59&hasta=2020-01-03T00:00",
                             "desde=2020-01-02&hasta=2020-01-02", "id=900001", "desde=2021-01-01T00:00"):
                esperados = [f["ID"] for f in self.cliente.get(
                    "/api/pesadas?producto=Filtro+horario&por_pagina=1000&" + consulta).get_json()["filas"]]
                exportados = self.excel_ids("producto=Filtro+horario&" + consulta)
                self.assertEqual(exportados, esperados)
            for consulta in ("desde=2020-01-02T25:00", "hasta=2020-01-02T10:99",
                             "desde=2020-01-03T00:00&hasta=2020-01-02T10:15"):
                r = self.cliente.get("/api/pesadas?" + consulta)
                self.assertEqual(r.status_code, 400)
                self.assertIn("Fecha", r.get_json()["error"])
        finally:
            con.cursor().execute("DELETE FROM pesadas WHERE Producto = 'Filtro horario'")
            con.commit()
            con.close()

    def excel_ids(self, consulta):
        r = self.cliente.get("/api/pesadas/excel?" + consulta)
        try:
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.mimetype, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.assertIn(".xlsx", r.headers["Content-Disposition"])
            self.assertEqual(r.headers["Cache-Control"], "no-store")
            with ZipFile(io.BytesIO(r.get_data())) as libro:
                hoja = ET.fromstring(libro.read("xl/worksheets/sheet1.xml"))
                ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                filas = hoja.findall("s:sheetData/s:row", ns)[1:]
                tabla = ET.fromstring(libro.read("xl/tables/table1.xml"))
                self.assertEqual(tabla.get("displayName"), "TablaBalanzacoches")
                self.assertEqual(tabla.get("ref"), "A1:M%d" % max(2, len(filas) + 1))
                return [int(f.find("s:c[@r='B%s']/s:v" % f.get("r"), ns).text) for f in filas]
        finally:
            r.close()

    def test_excel_toda_la_base_requiere_confirmacion(self):
        self.assertEqual(self.cliente.get("/api/pesadas/excel?confirmar_todo=1").status_code, 401)
        self.ingresar()
        for consulta in ("", "por_pagina=5", "producto=+++", "confirmar_todo=0"):
            r = self.cliente.get("/api/pesadas/excel?" + consulta)
            self.assertEqual(r.status_code, 400)
            self.assertIn("Confirma", r.get_json()["error"])
        ids = self.excel_ids("confirmar_todo=1&por_pagina=5&pagina=2")
        self.assertEqual(len(ids), 250)  # exporta todo, no solo la página visible
        self.assertEqual(len(set(ids)), 250)

    def test_excel_filtros_y_errores(self):
        self.ingresar()
        r = self.cliente.get("/api/pesadas?por_pagina=1000").get_json()
        fila = r["filas"][0]
        self.assertEqual(self.excel_ids("id=%d" % fila["ID"]), [fila["ID"]])
        self.assertEqual(self.excel_ids("producto=NO-EXISTE-ESTE-PRODUCTO"), [])
        self.assertEqual(self.excel_ids("producto=%27%20OR%201%3D1%20--"), [])
        for consulta in ("id=x", "desde=2026-02-30T09:00", "hasta=2026-10-19T25:00",
                         "desde=2026-10-20T10:00&hasta=2026-10-19T10:00"):
            self.assertEqual(self.cliente.get("/api/pesadas/excel?" + consulta).status_code, 400)

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
