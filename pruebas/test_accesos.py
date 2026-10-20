"""Permisos reales, migración, recuperación y revocación de sesiones de la web."""

import secrets
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import MySQLdb
from werkzeug.security import check_password_hash, generate_password_hash

import ayuda
from registro1280 import accesos, basedatos, comandos, config_web
from registro1280.web import crear_app

AJUSTES = {"usuario": "admin.anterior", "clave_hash": generate_password_hash("112233", method="pbkdf2:sha256:1000"),
           "secreto": "accesos-prueba" * 4, "puerto": 8081}


class Accesos(unittest.TestCase):
    def setUp(self):
        ayuda.preparar_base()
        self.con = basedatos.conectar()
        self.addCleanup(self.con.close)
        accesos.preparar(self.con, AJUSTES)
        self.admin = accesos.autenticar(self.con, "admin.anterior", "112233")
        self.app = crear_app(AJUSTES)
        self.app.testing = True
        self.cliente = self.login("admin.anterior", "112233")

    def login(self, usuario, clave, esperado=302):
        cliente = self.app.test_client()
        self.assertEqual(cliente.post("/ingresar", data={"usuario": usuario, "clave": clave}).status_code, esperado)
        return cliente

    def post(self, ruta, cuerpo, cliente=None):
        cliente = cliente or self.cliente
        with cliente.session_transaction() as sesion:
            csrf = sesion.get("csrf", "")
        return cliente.post(ruta, json=cuerpo, headers={"X-CSRF-Token": csrf})

    def crear(self, usuario="operador", rol="operador", pin="123456"):
        r = self.post("/api/accesos", {"usuario": usuario, "rol": rol, "pin": pin})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()["nro"]

    def cambiar(self, nro, accion, **datos):
        return self.post("/api/accesos/%s" % nro, dict(accion=accion, **datos))

    def test_migracion_conserva_clave_y_no_la_restaura_al_actualizar(self):
        self.assertEqual(self.admin["rol"], "administrador")
        self.assertIsNone(accesos.autenticar(self.con, "ADMIN.ANTERIOR", "112233"))
        self.cambiar(self.admin["nro"], "pin", pin="654321")
        accesos.preparar(self.con, AJUSTES)
        self.assertIsNone(accesos.autenticar(self.con, "admin.anterior", "112233"))
        self.assertIsNotNone(accesos.autenticar(self.con, "admin.anterior", "654321"))
        self.assertEqual(len(accesos.listar(self.con)), 1)

    def test_no_recrear_cuenta_legacy_eliminada_al_actualizar(self):
        self.crear("nuevo.admin", "administrador")
        cliente = self.login("nuevo.admin", "123456")
        self.assertEqual(self.post("/api/accesos/%d" % self.admin["nro"], {"accion": "eliminar"}, cliente).status_code, 200)
        accesos.preparar(self.con, AJUSTES)
        self.assertIsNone(accesos.autenticar(self.con, "admin.anterior", "112233"))
        self.assertEqual(self.cliente.get("/api/resumen").status_code, 401)

    def test_schema_repetido_no_cambia_usuarios(self):
        nro = self.crear()
        antes = accesos.vigente(self.con, self.admin["nro"], self.admin["revision"])
        with open(ayuda.RAIZ + "/registro1280/esquema.sql", encoding="utf-8") as archivo:
            sql = archivo.read().replace("registro1280", ayuda.BASE)
        resultado = ayuda._mariadb(sql)
        self.assertEqual(resultado.returncode, 0, resultado.stderr.decode())
        accesos.preparar(self.con, AJUSTES)
        self.assertEqual(accesos.vigente(self.con, antes["nro"], antes["revision"]), antes)
        self.assertEqual(accesos.autenticar(self.con, "operador", "123456")["nro"], nro)

    def test_operador_ve_pesadas_excel_correos_pero_nada_de_configuracion(self):
        self.crear()
        cliente = self.login("operador", "123456")
        html = cliente.get("/").get_data(as_text=True)
        self.assertNotIn("vista-configuracion", html)
        self.assertNotIn("seccion-accesos", html)
        self.assertNotIn("config.js", html)
        self.assertNotIn('href="/configuracion"', html)
        self.assertIn("exportar-excel", html)
        self.assertIn("correos-filas", html)
        for ruta in ("/api/resumen", "/api/pesadas", "/api/correos"):
            self.assertEqual(cliente.get(ruta).status_code, 200, ruta)
        r = cliente.get("/api/pesadas/excel?confirmar_todo=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn(".xlsx", r.headers["Content-Disposition"])
        r.close()
        for ruta in ("/configuracion", "/api/configuracion", "/api/configuracion/clave", "/api/comunicacion",
                     "/api/descargas", "/api/pedidos/1", "/api/accesos"):
            self.assertEqual(cliente.get(ruta).status_code, 403, ruta)
        for ruta, cuerpo in (("/api/pedidos", {"tipo": "guardar"}),
                             ("/api/accesos", {"usuario": "hack", "rol": "administrador", "pin": "111111"}),
                             ("/api/accesos/1", {"accion": "pin", "pin": "111111"})):
            self.assertEqual(self.post(ruta, cuerpo, cliente).status_code, 403, ruta)

    def test_admin_puede_ambas_secciones_y_listado_no_expone_hash(self):
        self.crear()
        html = self.cliente.get("/configuracion").get_data(as_text=True)
        self.assertIn("seccion-accesos", html)
        self.assertIn("vista-pesadas", html)
        self.assertEqual(self.cliente.get("/api/configuracion").status_code, 200)
        datos = self.cliente.get("/api/accesos").get_json()
        self.assertEqual(datos["actual"], self.admin["nro"])
        self.assertEqual(len(datos["usuarios"]), 2)
        for fila in datos["usuarios"]:
            self.assertEqual(set(fila), {"nro", "usuario", "rol"})

    def test_crear_valida_usuario_rol_pin_y_duplicados(self):
        self.crear()
        self.assertEqual(self.post("/api/accesos", {"usuario": "operador", "rol": "operador", "pin": "123456"}).status_code, 409)
        for usuario, rol, pin in (("x", "operador", "123456"), ("<script>", "operador", "123456"),
                                  ("normal", "dueño", "123456"), ("normal", "administrador", "123"),
                                  ("normal", "operador", "abcdef"), ("normal", "operador", 123456),
                                  ([], "operador", "123456"), ("normal", [], "123456")):
            with self.subTest(usuario=usuario, rol=rol, pin=pin):
                self.assertEqual(self.post("/api/accesos", {"usuario": usuario, "rol": rol, "pin": pin}).status_code, 400)
        self.assertEqual(self.post("/api/accesos", []).status_code, 400)
        self.assertEqual(len(accesos.listar(self.con)), 2)

    def test_pin_solo_hash_y_acepta_ceros_iniciales(self):
        nro = self.crear(pin="001234")
        cur = self.con.cursor()
        cur.execute("SELECT clave_hash FROM usuarios WHERE nro=%s", (nro,))
        hash_pin = cur.fetchone()[0]
        self.con.commit()
        self.assertNotEqual(hash_pin, "001234")
        self.assertTrue(check_password_hash(hash_pin, "001234"))
        self.login("operador", "001234")
        self.login("operador", "1234", esperado=401)

    def test_exactamente_seis_numeros_al_crear_resetear_recuperar_e_ingresar(self):
        nro = self.crear()
        for pin in ("", "1", "12345", "1234567", "123456789012", "12a456", "１２３４５６", "12345 ", "12345\n", 123456):
            with self.subTest(pin=pin):
                r = self.post("/api/accesos", {"usuario": "OTRO", "rol": "operador", "pin": pin})
                self.assertEqual(r.status_code, 400)
                self.assertIn("exactamente 6", r.get_json()["error"])
                self.assertEqual(self.cambiar(nro, "pin", pin=pin).status_code, 400)
                with self.assertRaises(accesos.ErrorAcceso):
                    accesos.recuperar(self.con, "RECUPERAR", pin)
                self.assertIsNone(accesos.autenticar(self.con, "operador", pin))
        self.login("operador", "123456")

    def test_renombrar_mayusculas_conserva_rol_pin_y_cierra_sesiones(self):
        nro = self.crear()
        anterior = self.login("operador", "123456")
        self.assertEqual(self.cambiar(nro, "usuario", usuario="OPERADOR").status_code, 200)
        self.assertEqual(anterior.get("/api/resumen").status_code, 401)
        self.login("operador", "123456", esperado=401)
        cliente = self.login("OPERADOR", "123456")
        self.assertEqual(cliente.get("/api/pesadas").status_code, 200)
        self.assertEqual(cliente.get("/api/configuracion").status_code, 403)
        self.assertEqual(accesos.autenticar(self.con, "OPERADOR", "123456")["nro"], nro)

    def test_renombrar_administrador_propio_permite_reingresar(self):
        r = self.cambiar(self.admin["nro"], "usuario", usuario="ADMINISTRADOR")
        self.assertTrue(r.get_json()["volver_ingresar"])
        self.assertEqual(self.cliente.get("/api/accesos").status_code, 401)
        self.login("admin.anterior", "112233", esperado=401)
        cliente = self.login("ADMINISTRADOR", "112233")
        self.assertEqual(cliente.get("/api/accesos").status_code, 200)

    def test_nombre_duplicado_o_invalido_no_cambia_la_cuenta(self):
        nro = self.crear()
        self.assertEqual(self.cambiar(nro, "usuario", usuario="admin.anterior").status_code, 409)
        for nombre in ("", "x", "con espacios", "<script>", "A" * 41, None, []):
            self.assertEqual(self.cambiar(nro, "usuario", usuario=nombre).status_code, 400)
        self.assertEqual(self.cambiar(nro, "usuario", usuario="operador").status_code, 200)
        self.assertIsNotNone(accesos.autenticar(self.con, "operador", "123456"))

    def test_actualizar_esquema_antiguo_preserva_credenciales_e_invalida_sesiones(self):
        self.con.cursor().execute("ALTER TABLE usuarios DROP COLUMN pin_seis")
        with open(ayuda.RAIZ + "/registro1280/esquema.sql", encoding="utf-8") as archivo:
            sql = archivo.read().replace("registro1280", ayuda.BASE)
        resultado = ayuda._mariadb(sql)
        self.assertEqual(resultado.returncode, 0, resultado.stderr.decode())
        self.assertEqual(self.cliente.get("/api/accesos").status_code, 401)
        cliente = self.login("admin.anterior", "112233")
        self.assertEqual(cliente.get("/api/accesos").status_code, 200)
        fila = accesos.autenticar(self.con, "admin.anterior", "112233")
        self.assertEqual(fila["pin_seis"], 1)
        self.assertNotEqual(fila["revision"], self.admin["revision"])

    def test_instalacion_crea_exactamente_las_dos_cuentas_solicitadas(self):
        self.con.cursor().execute("DELETE FROM usuarios")
        self.con.commit()
        from registro1280.__main__ import main
        with patch.object(config_web, "preparar", return_value=False):
            self.assertEqual(main(["preparar-web", "8081"]), 0)
        self.assertEqual([(u["usuario"], u["rol"]) for u in accesos.listar(self.con)],
                         [("ADMINISTRADOR", "administrador"), ("OPERADOR", "operador")])
        admin = self.login("ADMINISTRADOR", "102030")
        operador = self.login("OPERADOR", "123456")
        self.assertEqual(admin.get("/api/configuracion").status_code, 200)
        self.assertEqual(operador.get("/api/pesadas").status_code, 200)
        self.assertEqual(operador.get("/api/configuracion").status_code, 403)

    def test_inicializar_corrige_pin_y_rol_de_cuentas_existentes_una_sola_vez(self):
        self.crear("ADMINISTRADOR", "operador", "654321")
        sesion_anterior = self.login("ADMINISTRADOR", "654321")
        accesos.preparar_iniciales(self.con)
        self.assertEqual(sesion_anterior.get("/api/pesadas").status_code, 401)
        cliente = self.login("ADMINISTRADOR", "102030")
        self.assertEqual(cliente.get("/api/configuracion").status_code, 200)
        self.login("ADMINISTRADOR", "654321", esperado=401)
        # Las demás cuentas se conservan.
        self.assertIsNotNone(accesos.autenticar(self.con, "admin.anterior", "112233"))

    def test_actualizaciones_no_restauran_pin_nombre_ni_usuario_eliminado(self):
        accesos.preparar_iniciales(self.con)
        admin = accesos.autenticar(self.con, "ADMINISTRADOR", "102030")
        operador = accesos.autenticar(self.con, "OPERADOR", "123456")
        accesos.modificar(self.con, self.admin, admin["nro"], "pin", pin="998877")
        accesos.modificar(self.con, self.admin, admin["nro"], "usuario", usuario="ADMIN.NUEVO")
        accesos.modificar(self.con, self.admin, operador["nro"], "eliminar")
        accesos.preparar_iniciales(self.con)
        self.assertIsNotNone(accesos.autenticar(self.con, "ADMIN.NUEVO", "998877"))
        self.assertIsNone(accesos.autenticar(self.con, "ADMINISTRADOR", "102030"))
        self.assertIsNone(accesos.autenticar(self.con, "OPERADOR", "123456"))

    def test_ya_no_hay_enlace_ni_conversion_de_credenciales(self):
        cliente = self.app.test_client()
        html = cliente.get("/ingresar").get_data(as_text=True)
        self.assertNotIn("Actualizar acceso anterior", html)
        self.assertNotIn("/actualizar-pin", html)
        for metodo in (cliente.get, cliente.post):
            r = metodo("/actualizar-pin")
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r.headers["Location"], "/ingresar")

    def test_csrf_requerido_para_cada_cambio(self):
        nro = self.crear()
        for ruta, datos in (("/api/accesos", {"usuario": "intruso", "rol": "administrador", "pin": "123456"}),
                            ("/api/accesos/%d" % nro, {"accion": "eliminar"})):
            self.assertEqual(self.cliente.post(ruta, json=datos).status_code, 403)
        self.assertEqual(len(accesos.listar(self.con)), 2)

    def test_reset_pin_revoca_todas_las_sesiones_y_clave_anterior(self):
        nro = self.crear()
        clientes = [self.login("operador", "123456") for _ in range(2)]
        self.assertEqual(self.cambiar(nro, "pin", pin="654321").status_code, 200)
        for cliente in clientes:
            self.assertEqual(cliente.get("/api/resumen").status_code, 401)
        self.login("operador", "123456", esperado=401)
        self.login("operador", "654321")

    def test_cambio_rol_revoca_sesion_anterior_y_aplica_permisos(self):
        nro = self.crear()
        cliente = self.login("operador", "123456")
        self.assertEqual(self.cambiar(nro, "rol", rol="administrador").status_code, 200)
        self.assertEqual(cliente.get("/api/configuracion").status_code, 401)
        cliente = self.login("operador", "123456")
        self.assertEqual(cliente.get("/api/configuracion").status_code, 200)
        self.assertEqual(self.cambiar(nro, "rol", rol="operador").status_code, 200)
        self.assertEqual(cliente.get("/api/configuracion").status_code, 401)
        cliente = self.login("operador", "123456")
        self.assertEqual(cliente.get("/api/configuracion").status_code, 403)

    def test_eliminar_cierra_sesion_no_afecta_pesadas(self):
        nro = self.crear()
        cliente = self.login("operador", "123456")
        self.assertEqual(self.cambiar(nro, "eliminar").status_code, 200)
        self.assertEqual(cliente.get("/api/resumen").status_code, 401)
        self.login("operador", "123456", esperado=401)
        self.assertEqual(self.cliente.get("/api/pesadas").status_code, 200)

    def test_no_eliminar_propio_ni_quitar_ultimo_admin(self):
        self.assertEqual(self.cambiar(self.admin["nro"], "eliminar").status_code, 409)
        self.assertEqual(self.cambiar(self.admin["nro"], "rol", rol="operador").status_code, 409)
        self.assertEqual(self.cliente.get("/api/accesos").status_code, 200)

    def test_cambiar_pin_propio_pide_reingreso(self):
        otro_cliente = self.login("admin.anterior", "112233")
        r = self.cambiar(self.admin["nro"], "pin", pin="123456")
        self.assertTrue(r.get_json()["volver_ingresar"])
        self.assertEqual(self.cliente.get("/api/accesos").status_code, 401)
        self.assertEqual(otro_cliente.get("/api/accesos").status_code, 401)
        self.login("admin.anterior", "123456")

    def test_mutacion_revalida_al_admin_despues_del_candado(self):
        nro = self.crear("segundo.admin", "administrador")
        anterior = accesos.autenticar(self.con, "segundo.admin", "123456")
        self.cambiar(nro, "rol", rol="operador")
        with self.assertRaises(accesos.ErrorAcceso) as error:
            accesos.crear(self.con, anterior, "prohibido", "administrador", "123456")
        self.assertEqual(error.exception.estado, 403)

    def test_dos_cambios_simultaneos_conservan_un_admin(self):
        self.crear("segundo.admin", "administrador")
        segundo = accesos.autenticar(self.con, "segundo.admin", "123456")
        barrera = threading.Barrier(2)

        def degradar(actor):
            con = basedatos.conectar()
            try:
                barrera.wait(timeout=10)
                accesos.modificar(con, actor, actor["nro"], "rol", rol="operador")
                return 200
            except accesos.ErrorAcceso as error:
                return error.estado
            finally:
                con.close()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            resultados = list(ejecutor.map(degradar, [self.admin, segundo]))
        self.assertEqual(sorted(resultados), [200, 409])
        self.assertEqual(sum(u["rol"] == "administrador" for u in accesos.listar(self.con)), 1)

    def test_sesion_legacy_no_es_un_acceso_alternativo(self):
        cliente = self.app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["usuario"] = AJUSTES["usuario"]
            sesion["rol"] = "administrador"
        self.assertEqual(cliente.get("/api/configuracion").status_code, 401)

    def test_error_base_no_autoriza_ni_consume_intento(self):
        def sin_base():
            raise basedatos.ErrorBase("Prueba")
        app = crear_app(AJUSTES, conectar=sin_base)
        cliente = app.test_client()
        self.assertEqual(cliente.get("/ingresar").status_code, 200)
        self.assertEqual(cliente.post("/ingresar", data={"usuario": "x", "clave": "112233"}).status_code, 503)
        with cliente.session_transaction() as sesion:
            sesion["cuenta"], sesion["revision"] = self.admin["nro"], self.admin["revision"]
        self.assertEqual(cliente.get("/api/pesadas").status_code, 503)

    def test_recuperacion_local_restaurar_admin_y_revocar_sesion(self):
        self.crear("segundo.admin", "administrador")
        accesos.recuperar(self.con, "operador.recuperado", "445566")
        usuario_bd = basedatos.usuario_actual()
        with patch.object(comandos.os, "geteuid", return_value=0), \
                patch("builtins.input", return_value="admin.anterior"), \
                patch.object(comandos.getpass, "getpass", return_value="998877"), \
                patch.object(config_web, "leer", return_value=AJUSTES), \
                patch.object(config_web, "cambiar_clave") as guardar_ini, \
                patch.object(comandos, "_con", side_effect=lambda: basedatos.conectar(usuario=usuario_bd)), \
                patch.object(comandos.subprocess, "run"):
            self.assertEqual(comandos.cambiar_clave([]), 0)
        guardar_ini.assert_called_once_with("admin.anterior", "998877")
        self.assertEqual(self.cliente.get("/api/accesos").status_code, 401)
        cliente = self.login("admin.anterior", "998877")
        self.assertEqual(cliente.get("/api/configuracion").status_code, 200)
        self.login("admin.anterior", "112233", esperado=401)

    def test_permisos_sql_web_gestionan_usuarios_sin_editar_pesadas(self):
        usuario = "prueba_web_" + secrets.token_hex(6)
        clave = secrets.token_hex(24)
        cur = self.con.cursor()
        cur.execute("CREATE USER %s@'localhost' IDENTIFIED BY %s", (usuario, clave))
        try:
            cur.execute("GRANT SELECT ON " + ayuda.BASE + ".* TO %s@'localhost'", (usuario,))
            cur.execute("GRANT INSERT, UPDATE, DELETE ON " + ayuda.BASE + ".usuarios TO %s@'localhost'", (usuario,))
            con_web = MySQLdb.connect(unix_socket=basedatos.SOCKET, user=usuario, passwd=clave,
                                     database=ayuda.BASE, charset="utf8mb4", autocommit=False)
            try:
                nro = accesos.crear(con_web, self.admin, "operador", "operador", "123456")
                accesos.modificar(con_web, self.admin, nro, "pin", pin="654321")
                self.assertIsNotNone(accesos.autenticar(con_web, "operador", "654321"))
                accesos.modificar(con_web, self.admin, nro, "eliminar")
                with self.assertRaises(MySQLdb.OperationalError):
                    con_web.cursor().execute("UPDATE pesadas SET Producto='prohibido'")
                with self.assertRaises(MySQLdb.OperationalError):
                    con_web.cursor().execute("DELETE FROM pesadas")
            finally:
                con_web.close()
        finally:
            cur.execute("DROP USER %s@'localhost'", (usuario,))
