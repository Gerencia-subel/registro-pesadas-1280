"""Pruebas del envío por correo: CSV, tramos sin repetir, fallas (las de la base necesitan MariaDB).

Se usa un servidor SMTP falso en la misma máquina: no se envía nada a internet.
"""

import contextlib
import csv
import io
import json
import os
import unittest
from datetime import datetime
from decimal import Decimal
from unittest import mock

import MySQLdb

import ayuda
from registro1280 import acciones, basedatos, comandos, correo
from registro1280.controlador import ErrorControlador, leer_pesadas
from registro1280.envio import ServicioCorreo, siguiente_envio
from simulador_1280 import Simulador
from smtp_falso import SMTPFalso


def correr(funcion, *argumentos):
    salida, errores = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
        codigo = funcion(list(argumentos))
    return codigo, salida.getvalue(), errores.getvalue()


def asunto(mensaje):
    # Python deja un espacio al inicio si el asunto largo viene doblado en dos líneas (los
    # programas de correo lo quitan al mostrarlo).
    return str(mensaje["Subject"]).strip()


def leer_csv(mensaje):
    adjuntos = list(mensaje.iter_attachments())
    if not adjuntos:
        return None, None
    texto = adjuntos[0].get_payload(decode=True).decode("utf-8-sig")
    return adjuntos[0].get_filename(), list(csv.reader(io.StringIO(texto)))


# ---------------------------------------------------------------------- sin base de datos
class Formatos(unittest.TestCase):
    def test_numeros_como_los_manda_el_controlador(self):
        self.assertEqual(correo.numero_csv(Decimal("302.9500")), "302.95")
        self.assertEqual(correo.numero_csv(Decimal("22559.8500")), "22559.85")
        self.assertEqual(correo.numero_csv(Decimal("-81.6000")), "-81.6")
        self.assertEqual(correo.numero_csv(Decimal("1000.0000")), "1000")
        self.assertEqual(correo.numero_csv(Decimal("0.0000")), "0")

    def test_csv(self):
        filas = [{"ID": 188755, "cor": 77, "Producto": "Sapolio Floral, Limón", "Peso": Decimal("302.9500"),
                  "Acum": Decimal("22559.8500"), "FechaI": datetime(2026, 1, 10, 1, 8),
                  "FechaF": datetime(2026, 1, 10, 1, 9), "Turno": 2, "Estado": 0}]
        datos = correo.armar_csv(filas)
        self.assertTrue(datos.startswith(b"\xef\xbb\xbf"))      # UTF-8 con BOM: Excel muestra bien las tildes
        lineas = list(csv.reader(io.StringIO(datos.decode("utf-8-sig"))))
        self.assertEqual(lineas[0], ["ID", "Producto", "cor", "Peso", "Acumulado", "Hora inicial", "Hora fin",
                                     "Fecha", "Turno"])
        self.assertEqual(lineas[1], ["188755", "Sapolio Floral, Limón", "77", "302.95", "22559.85",
                                     "01:08", "01:09", "10/01/2026", "2"])
        self.assertEqual(datos.decode("utf-8-sig").count("\r\n"), 2)

    def test_fecha_es_la_de_inicio(self):
        filas = [{"ID": 1, "cor": 1, "Producto": "Granel", "Peso": Decimal("300"), "Acum": Decimal("300"),
                  "FechaI": datetime(2026, 10, 19, 23, 59), "FechaF": datetime(2026, 10, 20, 0, 1), "Turno": 2,
                  "Estado": 0}]
        linea = list(csv.reader(io.StringIO(correo.armar_csv(filas).decode("utf-8-sig"))))[1]
        self.assertEqual(linea[5:8], ["23:59", "00:01", "19/10/2026"])

    def test_textos_del_tramo(self):
        a, b = datetime(2026, 10, 19, 9, 10), datetime(2026, 10, 19, 9, 15)
        self.assertEqual(correo.asunto_tramo(a, b, 12), "Balanza de Coches - PDL 19/10/2026 09:10 - 09:15 - 12 pesadas")
        self.assertEqual(correo.asunto_tramo(a, b, 1), "Balanza de Coches - PDL 19/10/2026 09:10 - 09:15 - 1 pesada")
        self.assertEqual(correo.asunto_tramo(a, b, 0), "Balanza de Coches - PDL 19/10/2026 09:10 - 09:15 - Sin pesadas")
        self.assertTrue(correo.asunto_tramo(a, b, 0, True).endswith("Sin pesadas - Controlador sin respuesta"))
        self.assertEqual(correo.nombre_archivo(a, b), "Balanza de Coches - PDL_20261019_0910-0915.csv")
        c, d = datetime(2026, 10, 19, 23, 55), datetime(2026, 10, 20, 0, 0)
        self.assertEqual(correo.rango(c, d), "19/10/2026 23:55 - 20/10/2026 00:00")
        self.assertEqual(correo.nombre_archivo(c, d), "Balanza de Coches - PDL_20261019_2355-20261020_0000.csv")

    def test_direcciones(self):
        self.assertEqual(correo.direcciones("ktomasl@alicorp.com.pe, subelrobotin@gmail.com"),
                         ["ktomasl@alicorp.com.pe", "subelrobotin@gmail.com"])
        self.assertEqual(correo.direcciones("a@x.com; a@x.com b@y.pe"), ["a@x.com", "b@y.pe"])
        self.assertEqual(correo.direcciones(""), [])
        for mala in ("sin-arroba", "a@b", "a@@b.com", "a@b.com, x", "<a@b.com>"):
            with self.assertRaises(ValueError, msg=mala):
                correo.direcciones(mala)

    def test_horas_de_envio(self):
        t = datetime(2026, 10, 19, 9, 12, 40)
        self.assertEqual(siguiente_envio(t, 5), datetime(2026, 10, 19, 9, 15))
        self.assertEqual(siguiente_envio(datetime(2026, 10, 19, 9, 15), 5), datetime(2026, 10, 19, 9, 20))
        self.assertEqual(siguiente_envio(t, 60), datetime(2026, 10, 19, 10, 0))
        self.assertEqual(siguiente_envio(datetime(2026, 10, 19, 23, 58), 5), datetime(2026, 10, 20, 0, 0))
        self.assertEqual(siguiente_envio(t, 1440), datetime(2026, 10, 20, 0, 0))
        self.assertEqual(siguiente_envio(t, 7), datetime(2026, 10, 19, 9, 19))           # 7 no divide el día
        self.assertEqual(siguiente_envio(datetime(2026, 10, 19, 9, 19, 1), 7, base=datetime(2026, 10, 19, 9, 19)),
                         datetime(2026, 10, 19, 9, 26))
        # después de un envío se cuentan los minutos desde ese envío
        self.assertEqual(siguiente_envio(datetime(2026, 10, 19, 15, 25, 1), 10, base=datetime(2026, 10, 19, 15, 25)),
                         datetime(2026, 10, 19, 15, 35))
        self.assertEqual(siguiente_envio(datetime(2026, 10, 19, 12, 0), 7, base=datetime(2026, 10, 19, 9, 19)),
                         datetime(2026, 10, 19, 12, 7))                                   # 9:19 + 24 x 7


class EnvioSMTP(unittest.TestCase):
    def setUp(self):
        self.smtp = SMTPFalso()

    def tearDown(self):
        self.smtp.cerrar()

    def mensaje(self, cuenta, para=("ktomasl@alicorp.com.pe",), copia=("subelrobotin@gmail.com",)):
        return correo.armar_mensaje(cuenta, list(para), list(copia), "Asunto ñandú", "Cuerpo\n.punto al inicio\n",
                                    correo.armar_csv([]), "pesadas.csv")

    def test_envia_con_para_copia_y_adjunto(self):
        cuenta = self.smtp.cuenta()
        self.assertEqual(correo.enviar(cuenta, self.mensaje(cuenta)), "")
        recibido = self.smtp.mensajes[0]
        self.assertEqual(recibido["para"], ["ktomasl@alicorp.com.pe", "subelrobotin@gmail.com"])
        m = recibido["mensaje"]
        self.assertEqual(m["To"], "ktomasl@alicorp.com.pe")
        self.assertEqual(m["Cc"], "subelrobotin@gmail.com")
        self.assertEqual(asunto(m), "Asunto ñandú")
        self.assertIn("prueba@gmail.com", m["From"])
        self.assertIn(".punto al inicio", m.get_body().get_content())
        nombre, filas = leer_csv(m)
        self.assertEqual((nombre, filas[0][0]), ("pesadas.csv", "ID"))

    def test_clave_incorrecta_no_se_reintenta(self):
        cuenta = self.smtp.cuenta(clave="otra")
        with self.assertRaises(correo.ErrorCorreo) as e:
            correo.enviar(cuenta, self.mensaje(cuenta))
        self.assertIn("contraseña de aplicación", str(e.exception))
        self.assertFalse(e.exception.reintentar)
        with self.assertRaises(correo.ErrorCorreo):
            correo.probar_cuenta(cuenta)
        correo.probar_cuenta(self.smtp.cuenta())

    def test_limite_diario_de_gmail(self):
        self.smtp.modo = "limite"
        cuenta = self.smtp.cuenta()
        with self.assertRaises(correo.ErrorCorreo) as e:
            correo.enviar(cuenta, self.mensaje(cuenta))
        self.assertIn("límite diario", str(e.exception))

    def test_destinatario_rechazado(self):
        self.smtp.rechazar = {"subelrobotin@gmail.com"}
        cuenta = self.smtp.cuenta()
        aviso = correo.enviar(cuenta, self.mensaje(cuenta))
        self.assertIn("subelrobotin@gmail.com", aviso)
        self.assertEqual(len(self.smtp.mensajes), 1)

    def test_corte_despues_del_contenido_no_se_reintenta(self):
        self.smtp.modo = "sin_respuesta"
        cuenta = self.smtp.cuenta()
        with self.assertRaises(correo.ErrorCorreo) as e:
            correo.enviar(cuenta, self.mensaje(cuenta), espera=3)
        self.assertFalse(e.exception.reintentar)     # pudo haber llegado: no se manda dos veces

    def test_sin_direcciones_para_la_web(self):
        self.assertEqual(correo.sin_direcciones("Gmail no aceptó: ktomasl@alicorp.com.pe (550 5.1.1)"),
                         "Gmail no aceptó: (dirección) (550 5.1.1)")

    def test_sin_conexion(self):
        cuenta = self.smtp.cuenta()
        self.smtp.cerrar()
        with self.assertRaises(correo.ErrorCorreo) as e:
            correo.enviar(cuenta, self.mensaje(cuenta), espera=3)
        self.assertTrue(e.exception.reintentar)
        self.smtp = SMTPFalso()

    def test_cuenta_en_archivo(self):
        ruta = correo.RUTA
        self.assertFalse(correo.hay_cuenta({"correo_cuenta": ""}))
        self.assertTrue(correo.hay_cuenta({"correo_cuenta": "industria@gmail.com"}))
        with self.assertRaises(correo.ErrorCorreo):
            correo.leer_cuenta()
        correo.guardar_cuenta("industria@gmail.com", "abcd efgh ijkl mnop")
        try:
            c = correo.leer_cuenta()
            self.assertEqual((c["usuario"], c["clave"], c["servidor"], c["puerto"], c["seguridad"]),
                             ("industria@gmail.com", "abcdefghijklmnop", "smtp.gmail.com", 587, "starttls"))
            self.assertEqual(os.stat(ruta).st_mode & 0o077, 0)   # nadie más puede leerla
        finally:
            os.remove(ruta)


# ---------------------------------------------------------------------- con base de datos
class Tramos(unittest.TestCase):
    """El servicio del correo con reloj simulado, el simulador del controlador y el SMTP falso."""

    def setUp(self):
        ayuda.preparar_base()
        self.reloj = ayuda.RelojFalso(datetime(2026, 10, 19, 9, 12, 40))
        self.sim = Simulador(iniciales=200, reloj=self.reloj)
        self.smtp = SMTPFalso()
        self.con = basedatos.conectar()
        ayuda.con_simulador(self.con, self.sim)
        self.controlador_caido = False
        self.servicio = ServicioCorreo(leer=self.leer, reloj=self.reloj, leer_cuenta=self.smtp.cuenta,
                                       espera_reintento=0, dormir=lambda s: None, reloj_listo=lambda: True)
        basedatos.guardar_config(self.con, correo_para="ktomasl@alicorp.com.pe", correo_copia="subelrobotin@gmail.com",
                                 correo_cuenta="prueba@gmail.com")

    def tearDown(self):
        self.servicio._cerrar()
        self.sim.cerrar()
        self.smtp.cerrar()
        self.con.close()

    def leer(self, ip, puerto):
        if self.controlador_caido:
            raise ErrorControlador("El controlador no respondió")
        return leer_pesadas(ip, puerto)

    def activar(self, minutos=5):
        with mock.patch.object(comandos, "ahora", self.reloj):
            codigo, salida, error = correr(comandos.correo, str(minutos))
        self.assertEqual(codigo, 0, error)
        return salida

    def detener(self):
        with mock.patch.object(comandos, "ahora", self.reloj):
            codigo, salida, error = correr(comandos.correo, "detener")
        self.assertEqual(codigo, 0, error)

    def pesar(self, n):
        """n pesadas que terminan en el minuto actual del reloj."""
        return [self.sim.agregar(fin=self.reloj().replace(second=0)) for _ in range(n)]

    def hasta(self, h, m, s=1):
        """Avanza el reloj hasta las h:m:s y hace una revisión del servicio."""
        self.reloj.t = self.reloj.t.replace(hour=h, minute=m, second=s)
        self.servicio.vuelta()

    def ids_enviados(self):
        ids = []
        for r in self.smtp.mensajes:
            _, filas = leer_csv(r["mensaje"])
            ids += [int(f[0]) for f in (filas or [[]])[1:] if f]
        return ids

    def test_cada_correo_lleva_solo_su_tramo(self):
        salida = self.activar(5)
        self.assertIn("Primer envío a las 09:15 con las pesadas desde las 09:12", salida)
        self.hasta(9, 13)
        primeras = self.pesar(3)
        self.hasta(9, 14, 59)
        self.assertEqual(self.smtp.mensajes, [])               # todavía no son las 09:15
        self.hasta(9, 15)
        self.assertEqual(len(self.smtp.mensajes), 1)
        m = self.smtp.mensajes[0]["mensaje"]
        self.assertEqual(asunto(m), "Balanza de Coches - PDL 19/10/2026 09:12 - 09:15 - 3 pesadas")
        self.assertEqual(self.smtp.mensajes[0]["para"], ["ktomasl@alicorp.com.pe", "subelrobotin@gmail.com"])
        nombre, filas = leer_csv(m)
        self.assertEqual(nombre, "Balanza de Coches - PDL_20261019_0912-0915.csv")
        self.assertEqual([f[0] for f in filas[1:]], [p.split("|")[0] for p in primeras])
        self.assertEqual(filas[1][3], primeras[0].split("|")[3])       # el peso tal cual (302.95)
        # tramo siguiente: 09:15 - 09:20, solo las nuevas
        self.hasta(9, 17)
        segundas = self.pesar(2)
        self.hasta(9, 20)
        m = self.smtp.mensajes[1]["mensaje"]
        self.assertEqual(asunto(m), "Balanza de Coches - PDL 19/10/2026 09:15 - 09:20 - 2 pesadas")
        _, filas = leer_csv(m)
        self.assertEqual([f[0] for f in filas[1:]], [p.split("|")[0] for p in segundas])
        # muchos tramos seguidos: ninguna pesada se repite ni se pierde
        nuevas = primeras + segundas
        for minuto in range(21, 60):
            self.hasta(9, minuto)
            nuevas += self.pesar(minuto % 3)
        self.hasta(10, 0)
        enviados = self.ids_enviados()
        self.assertEqual(len(enviados), len(set(enviados)))
        self.assertEqual(sorted(enviados), sorted(int(p.split("|")[0]) for p in nuevas))
        self.assertEqual(basedatos.contar_pendientes(self.con), 0)
        self.assertEqual(len(self.smtp.mensajes), 10)          # 09:15, 09:20 ... 10:00
        historial = basedatos.correos(self.con, 100)
        self.assertTrue(all(c["resultado"] == "OK" for c in historial))

    def test_tramo_sin_pesadas(self):
        self.activar(5)
        self.hasta(9, 15)
        m = self.smtp.mensajes[0]["mensaje"]
        self.assertEqual(asunto(m), "Balanza de Coches - PDL 19/10/2026 09:12 - 09:15 - Sin pesadas")
        self.assertEqual(list(m.iter_attachments()), [])
        cuerpo = m.get_body().get_content()
        self.assertIn("Balanza de Coches - PDL del 19/10/2026 09:12 - 09:15: sin pesadas.", cuerpo)
        self.assertIn("Estado de comunicación: Sin dato", cuerpo)

    def test_si_falla_el_envio_van_en_el_siguiente(self):
        self.activar(5)
        self.hasta(9, 14)
        a = self.pesar(2)
        self.smtp.modo = "caido"
        self.hasta(9, 15)
        self.assertEqual(self.smtp.mensajes, [])
        fallido = basedatos.correos(self.con, 1)[0]
        self.assertEqual((fallido["resultado"], fallido["pesadas"]), ("ERROR", 2))
        self.assertEqual(basedatos.contar_pendientes(self.con), 2)
        self.smtp.modo = "normal"
        self.hasta(9, 18)
        b = self.pesar(1)
        self.hasta(9, 20)
        m = self.smtp.mensajes[0]["mensaje"]
        self.assertEqual(asunto(m), "Balanza de Coches - PDL 19/10/2026 09:12 - 09:20 - 3 pesadas")
        self.assertEqual(sorted(self.ids_enviados()), sorted(int(p.split("|")[0]) for p in a + b))

    def test_controlador_sin_respuesta(self):
        self.activar(5)
        self.hasta(9, 14)
        a = self.pesar(2)
        self.servicio.vuelta()
        self.controlador_caido = True
        self.hasta(9, 15)
        m = self.smtp.mensajes[0]["mensaje"]
        self.assertEqual(asunto(m), "Balanza de Coches - PDL 19/10/2026 09:12 - 09:15 - Sin pesadas - Controlador sin respuesta")
        self.assertIn("no se pudo leer el controlador", m.get_body().get_content())
        self.controlador_caido = False
        self.hasta(9, 20)
        self.assertEqual(sorted(self.ids_enviados()), sorted(int(p.split("|")[0]) for p in a))

    def test_cambio_de_intervalo_respeta_el_envio_programado(self):
        self.activar(60)
        self.hasta(9, 20)
        salida = self.activar(5)
        self.assertIn("sale a las 10:00, como estaba programado", salida)
        self.hasta(9, 25)
        self.assertEqual(self.smtp.mensajes, [])
        self.hasta(10, 0)
        self.hasta(10, 5)
        asuntos = [asunto(r["mensaje"]) for r in self.smtp.mensajes]
        self.assertEqual(asuntos, ["Balanza de Coches - PDL 19/10/2026 09:12 - 10:00 - Sin pesadas",
                                   "Balanza de Coches - PDL 19/10/2026 10:00 - 10:05 - Sin pesadas"])

    def test_cambios_de_intervalo_como_los_pidio_el_cliente(self):
        self.reloj.t = datetime(2026, 10, 19, 15, 12, 40)
        self.activar(5)
        for minuto in (15, 20):
            self.hasta(15, minuto)
        self.hasta(15, 24)
        salida = self.activar(10)                               # a las 15:24 se cambia a cada 10
        self.assertIn("sale a las 15:25, como estaba programado", salida)
        self.assertIn("el siguiente a las 15:35", salida)
        for h, m in ((15, 25), (15, 30), (15, 35), (15, 45), (15, 55), (16, 3)):
            self.hasta(h, m)
        self.activar(5)                                         # a las 16:03 se cambia a cada 5
        for h, m in ((16, 5), (16, 10), (16, 14)):
            self.hasta(h, m)
        self.activar(15)                                        # a las 16:14 se cambia a cada 15
        for h, m in ((16, 15), (16, 20), (16, 30)):
            self.hasta(h, m)
        horas = [asunto(r["mensaje"]).split(" - ")[2] for r in self.smtp.mensajes]
        self.assertEqual(horas, ["15:15", "15:20", "15:25", "15:35", "15:45", "15:55", "16:05", "16:10", "16:15",
                                 "16:30"])

    def test_correo_lleva_el_ultimo_estado_de_comunicacion(self):
        from registro1280 import comunicacion
        self.activar(5)
        servicio_com = comunicacion.ServicioComunicacion(ping=lambda ip: False, reloj=self.reloj)
        self.hasta(9, 14, 50)
        servicio_com.vuelta()
        self.hasta(9, 14, 59)
        servicio_com.ping = lambda ip: True                      # a las 9:14:59 vuelve a responder
        servicio_com.vuelta()
        self.hasta(9, 15)
        self.assertIn("Estado de comunicación: Activo (09:14:59)", self.smtp.mensajes[0]["mensaje"].get_body().get_content())
        servicio_com.ping = lambda ip: False
        self.hasta(9, 19, 58)
        servicio_com.vuelta()
        self.hasta(9, 20)
        self.assertIn("Estado de comunicación: Falla de comunicación (09:19:58)",
                      self.smtp.mensajes[1]["mensaje"].get_body().get_content())
        basedatos.guardar_config(self.con, comunicacion_activo="0")
        self.hasta(9, 25)
        self.assertIn("Estado de comunicación: Desactivado", self.smtp.mensajes[2]["mensaje"].get_body().get_content())
        servicio_com._con.close()

    def test_raspberry_apagada_un_rato(self):
        self.activar(5)
        self.hasta(9, 14)
        a = self.pesar(4)
        self.hasta(11, 37, 20)                                  # vuelve la luz a las 11:37
        self.assertEqual(len(self.smtp.mensajes), 1)
        self.assertEqual(asunto(self.smtp.mensajes[0]["mensaje"]), "Balanza de Coches - PDL 19/10/2026 09:12 - 11:37 - 4 pesadas")
        self.hasta(11, 40)
        self.assertEqual(asunto(self.smtp.mensajes[1]["mensaje"]),
                         "Balanza de Coches - PDL 19/10/2026 11:37 - 11:40 - Sin pesadas")
        self.assertEqual(sorted(self.ids_enviados()), sorted(int(p.split("|")[0]) for p in a))

    def test_detenido_no_envia_y_al_reactivar_empieza_de_cero(self):
        self.activar(5)
        self.detener()
        self.hasta(9, 14)
        self.pesar(3)
        self.hasta(9, 15)
        self.hasta(9, 40)
        self.assertEqual(self.smtp.mensajes, [])
        salida = self.activar(5)
        self.assertIn("Primer envío a las 09:45 con las pesadas desde las 09:40", salida)
        self.hasta(9, 42)
        b = self.pesar(1)
        self.hasta(9, 45)
        self.assertEqual(self.ids_enviados(), [int(b[0].split("|")[0])])

    def test_al_reactivar_sale_lo_que_habia_quedado_pendiente(self):
        self.activar(5)
        self.hasta(9, 14)
        a = self.pesar(2)
        self.smtp.modo = "caido"
        self.hasta(9, 15)                                       # falla: quedan 2 pendientes
        self.hasta(9, 16)
        self.detener()
        self.hasta(9, 17)
        self.pesar(1)                                           # pesada mientras está detenido: no se envía
        self.smtp.modo = "normal"
        self.hasta(9, 18)
        salida = self.activar(5)
        self.hasta(9, 20)
        self.assertEqual(asunto(self.smtp.mensajes[0]["mensaje"]), "Balanza de Coches - PDL 19/10/2026 09:18 - 09:20 - 2 pesadas")
        self.assertEqual(sorted(self.ids_enviados()), sorted(int(p.split("|")[0]) for p in a))
        self.assertEqual(basedatos.contar_pendientes(self.con), 0)

    def test_si_falla_la_base_despues_de_enviar_no_se_repite(self):
        original = basedatos.registrar_correo
        fallas = [1]

        def registrar(*args, **kw):
            if fallas:
                fallas.pop()
                raise MySQLdb.OperationalError(2013, "Lost connection to server during query")
            return original(*args, **kw)
        self.activar(5)
        self.hasta(9, 14)
        a = self.pesar(2)
        with mock.patch.object(basedatos, "registrar_correo", registrar):
            with self.assertRaises(MySQLdb.OperationalError):
                self.hasta(9, 15)
            self.hasta(9, 15, 12)                               # solo anota, no vuelve a enviar
        self.assertEqual(len(self.smtp.mensajes), 1)
        self.assertEqual(basedatos.contar_pendientes(self.con), 0)
        self.assertEqual([c["resultado"] for c in basedatos.correos(self.con, 10)], ["OK"])
        self.hasta(9, 20)
        self.assertEqual(asunto(self.smtp.mensajes[1]["mensaje"]), "Balanza de Coches - PDL 19/10/2026 09:15 - 09:20 - Sin pesadas")
        self.assertEqual(sorted(self.ids_enviados()), sorted(int(p.split("|")[0]) for p in a))

    def test_espera_que_se_sincronice_la_hora_al_encender(self):
        self.activar(5)
        listo = [False]
        self.servicio.reloj_listo = lambda: listo[0]
        self.hasta(9, 15)
        self.assertEqual(self.smtp.mensajes, [])
        listo[0] = True
        self.hasta(9, 15, 30)
        self.assertEqual(len(self.smtp.mensajes), 1)

    def test_sin_destinatarios_o_sin_cuenta_queda_anotado(self):
        self.activar(5)
        basedatos.guardar_config(self.con, correo_para="")
        self.hasta(9, 15)
        self.assertIn("No hay destinatarios", basedatos.correos(self.con, 1)[0]["detalle"])
        basedatos.guardar_config(self.con, correo_para="ktomasl@alicorp.com.pe")

        def sin_cuenta():
            raise correo.ErrorCorreo("Falta la cuenta de Gmail que envía. Configurarla con:  sudo correo cuenta")
        self.servicio.leer_cuenta = sin_cuenta
        self.hasta(9, 20)
        self.assertIn("Falta la cuenta", basedatos.correos(self.con, 1)[0]["detalle"])
        self.assertEqual(self.smtp.mensajes, [])


class PedidosDeLaWeb(unittest.TestCase):
    """Lo que se cambia desde la web lo hace el servicio del correo, igual que los comandos."""

    def setUp(self):
        ayuda.preparar_base()
        self.reloj = ayuda.RelojFalso(datetime(2026, 10, 19, 9, 12, 40))
        self.sim = Simulador(iniciales=50, reloj=self.reloj)
        self.smtp = SMTPFalso()
        self.con = basedatos.conectar()
        ayuda.con_simulador(self.con, self.sim)
        self.probadas = []
        self.servicio = ServicioCorreo(reloj=self.reloj, leer_cuenta=self.smtp.cuenta, espera_reintento=0,
                                       dormir=lambda s: None, reloj_listo=lambda: True,
                                       probar_cuenta=self.probadas.append)

    def tearDown(self):
        self.servicio._cerrar()
        self.sim.cerrar()
        self.smtp.cerrar()
        self.con.close()
        if os.path.exists(correo.RUTA):
            os.remove(correo.RUTA)

    def pedir(self, tipo, datos="", hace=0):
        from datetime import timedelta
        nro = basedatos.crear_pedido(self.con, tipo, datos, self.reloj() - timedelta(seconds=hace), "10.0.0.5")
        self.servicio.vuelta()
        p = basedatos.pedido(self.con, nro)
        return p["estado"], p["resultado"]

    def test_bucle_y_controlador(self):
        estado, texto = self.pedir("bucle", "3")
        self.assertEqual((estado, basedatos.config(self.con)["bucle_activo"]), ("OK", "1"))
        self.assertIn("cada 3 minutos", texto)
        self.assertEqual(self.pedir("bucle", "detener")[0], "OK")
        self.assertEqual(basedatos.config(self.con)["bucle_activo"], "0")
        self.assertEqual(self.pedir("bucle", "cero")[0], "ERROR")
        estado, texto = self.pedir("controlador_probar")
        self.assertIn("V2.03.01", texto)
        estado, texto = self.pedir("guardar")
        self.assertIn("Total en la base: 50 pesadas", texto)
        self.assertEqual(basedatos.descargas(self.con, 1)[0]["origen"], "web")
        self.assertEqual(self.pedir("controlador", "999.1.1.1 10001")[0], "ERROR")
        estado, texto = self.pedir("controlador", "10.30.211.141 10001")
        self.assertEqual((estado, basedatos.config(self.con)["ip"]), ("OK", "10.30.211.141"))

    def test_correo_completo_desde_la_web(self):
        estado, texto = self.pedir("correo", "5")
        self.assertEqual(estado, "ERROR")
        self.assertIn("sudo correo cuenta", texto)
        estado, texto = self.pedir("correo_cuenta", json.dumps({"usuario": "prueba@gmail.com",
                                                                "clave": "abcd efgh ijkl mnop"}))
        self.assertEqual(estado, "OK", texto)
        self.assertEqual(correo.leer_cuenta()["clave"], "abcdefghijklmnop")
        self.assertEqual(basedatos.config(self.con)["correo_cuenta"], "prueba@gmail.com")
        self.assertEqual(self.probadas[0]["usuario"], "prueba@gmail.com")
        cur = self.con.cursor()
        cur.execute("SELECT datos FROM pedidos WHERE tipo = 'correo_cuenta'")
        self.assertEqual(cur.fetchall(), (("",),))                    # la contraseña no queda en la base
        self.assertEqual(self.pedir("correo_para", "no-es-correo")[0], "ERROR")
        self.assertEqual(self.pedir("correo_para", "ktomasl@alicorp.com.pe, subelrobotin@gmail.com")[0], "OK")
        self.assertEqual(self.pedir("correo_copia", "")[1], "Sin copia.")
        self.assertEqual(basedatos.config(self.con)["correo_clave"], "abcdefghijklmnop")   # para verla en la web
        estado, texto = self.pedir("correo_destinatarios", json.dumps({"para": ["a@x.com", "b@y.pe"], "copia": []}))
        self.assertEqual(estado, "OK", texto)
        self.assertEqual(basedatos.config(self.con)["correo_para"], "a@x.com, b@y.pe")
        self.assertEqual(self.pedir("correo_destinatarios", json.dumps({"para": [], "copia": ["c@z.com"]}))[0], "ERROR")
        muchos = ["persona%03d@alicorp.com.pe" % i for i in range(100)]
        estado, texto = self.pedir("correo_destinatarios", json.dumps({"para": ["ktomasl@alicorp.com.pe"],
                                                                       "copia": muchos}))
        self.assertEqual(estado, "OK", texto)
        self.assertEqual(len(basedatos.config(self.con)["correo_copia"].split(", ")), 100)
        estado, texto = self.pedir("correo", "5")
        self.assertEqual(estado, "OK", texto)
        self.assertIn("Primer envío a las 09:15", texto)
        estado, texto = self.pedir("correo", "10")
        self.assertIn("Intervalo cambiado a 10 minutos", texto)
        estado, texto = self.pedir("correo_probar", "otra@empresa.com")
        self.assertEqual(estado, "OK", texto)
        self.assertEqual(self.smtp.mensajes[0]["para"], ["otra@empresa.com"])
        self.assertEqual(self.pedir("correo", "detener")[0], "OK")
        self.assertEqual(basedatos.config(self.con)["correo_activo"], "0")

    def test_copia_la_clave_de_cuentas_anteriores(self):
        correo.guardar_cuenta("prueba@gmail.com", "abcdefghijklmnop")
        basedatos.guardar_config(self.con, correo_cuenta="prueba@gmail.com", correo_clave="")
        self.servicio.leer_cuenta = correo.leer_cuenta
        self.servicio.vuelta()
        self.assertEqual(basedatos.config(self.con)["correo_clave"], "abcdefghijklmnop")

    def test_cuenta_rechazada_no_se_guarda(self):
        def rechazar(cuenta):
            raise correo.ErrorCorreo("Gmail rechazó la cuenta", reintentar=False)
        self.servicio.probar_cuenta = rechazar
        estado, texto = self.pedir("correo_cuenta", json.dumps({"usuario": "prueba@gmail.com", "clave": "x" * 16}))
        self.assertEqual(estado, "ERROR")
        self.assertIn("No se guardó la cuenta", texto)
        self.assertFalse(os.path.exists(correo.RUTA))

    def test_estado_de_comunicacion_desde_la_web(self):
        estado, texto = self.pedir("comunicacion", "10")
        self.assertEqual(estado, "OK", texto)
        cfg = basedatos.config(self.con)
        self.assertEqual((cfg["comunicacion_activo"], cfg["comunicacion_segundos"]), ("1", "10"))
        self.assertEqual(self.pedir("comunicacion", "0")[0], "ERROR")
        self.assertEqual(self.pedir("comunicacion", "detener")[0], "OK")
        self.assertEqual(basedatos.config(self.con)["comunicacion_activo"], "0")

    def test_pedido_viejo_no_se_hace(self):
        estado, texto = self.pedir("bucle", "5", hace=1200)
        self.assertEqual(estado, "ERROR")
        self.assertIn("No se atendió a tiempo", texto)
        self.assertEqual(basedatos.config(self.con)["bucle_activo"], "0")


class ComandoCorreo(unittest.TestCase):
    def setUp(self):
        ayuda.preparar_base()
        self.sim = Simulador(iniciales=50)
        con = basedatos.conectar()
        ayuda.con_simulador(con, self.sim)
        con.close()

    def tearDown(self):
        self.sim.cerrar()
        if os.path.exists(correo.RUTA):
            os.remove(correo.RUTA)

    def test_pide_cuenta_y_destinatarios_antes_de_activar(self):
        codigo, _, error = correr(comandos.correo, "5")
        self.assertEqual(codigo, 1)
        self.assertIn("sudo correo cuenta", error)
        basedatos.guardar_config(basedatos.conectar(), correo_cuenta="industria@gmail.com")
        codigo, _, error = correr(comandos.correo, "5")
        self.assertIn("correo para", error)
        codigo, _, error = correr(comandos.correo, "para", "no-es-correo")
        self.assertEqual(codigo, 1)
        codigo, salida, _ = correr(comandos.correo, "para", "ktomasl@alicorp.com.pe")
        self.assertIn("Para: ktomasl@alicorp.com.pe", salida)
        codigo, salida, _ = correr(comandos.correo, "copia", "subelrobotin@gmail.com")
        codigo, _, error = correr(comandos.correo, "copia", *["persona%d@empresa.com.pe" % i for i in range(250)])
        self.assertEqual(codigo, 1)
        self.assertIn("Demasiadas direcciones", error)
        codigo, salida, _ = correr(comandos.correo, "copia", " ".join("p%d@alicorp.com.pe" % i for i in range(9)))
        self.assertEqual(codigo, 0)                               # más de 200 caracteres, entra
        codigo, salida, _ = correr(comandos.correo, "copia", "subelrobotin@gmail.com")
        codigo, salida, _ = correr(comandos.correo, "5")
        self.assertEqual(codigo, 0)
        self.assertIn("Correo activado: cada 5 minutos", salida)
        codigo, salida, _ = correr(comandos.correo)
        self.assertIn("Correo: activo, cada 5 minutos", salida)
        self.assertIn("Copia: subelrobotin@gmail.com", salida)
        self.assertIn("Pesadas pendientes de enviar: 0", salida)
        codigo, salida, _ = correr(comandos.correo, "copia", "ninguna")
        self.assertIn("Sin copia", salida)
        codigo, salida, _ = correr(comandos.correo, "detener")
        codigo, salida, _ = correr(comandos.correo)
        self.assertIn("Correo: detenido", salida)
        codigo, _, error = correr(comandos.correo, "0")
        self.assertEqual(codigo, 1)

    def test_no_se_activa_si_no_responde_el_controlador(self):
        basedatos.guardar_config(basedatos.conectar(), correo_cuenta="industria@gmail.com")
        correr(comandos.correo, "para", "ktomasl@alicorp.com.pe")
        with mock.patch.object(acciones, "guardar_ahora", side_effect=ErrorControlador("El controlador no respondió")):
            codigo, _, error = correr(comandos.correo, "5")
        self.assertEqual(codigo, 1)
        self.assertIn("No se activó el correo", error)
        self.assertEqual(basedatos.config(basedatos.conectar())["correo_activo"], "0")

    @unittest.skipUnless(os.geteuid() == 0, "necesita sudo")
    def test_cuenta_y_prueba(self):
        smtp = SMTPFalso(usuario="industria@gmail.com", clave="abcdefghijklmnop")
        try:
            con = basedatos.conectar()
            basedatos.guardar_config(con, correo_para="ktomasl@alicorp.com.pe", correo_copia="subelrobotin@gmail.com")
            with mock.patch("getpass.getpass", return_value="abcd efgh ijkl mnop"), \
                    mock.patch.object(correo, "probar_cuenta", lambda c: None):
                codigo, salida, error = correr(comandos.correo, "cuenta", "industria@gmail.com")
            self.assertEqual(codigo, 0, error)
            self.assertEqual(correo.leer_cuenta()["clave"], "abcdefghijklmnop")
            self.assertEqual(basedatos.config(con)["correo_cuenta"], "industria@gmail.com")
            # clave rechazada por Gmail: no se guarda
            with mock.patch("getpass.getpass", return_value="otraclave"), \
                    mock.patch.object(correo, "probar_cuenta",
                                      side_effect=correo.ErrorCorreo("Gmail rechazó", reintentar=False)):
                codigo, _, error = correr(comandos.correo, "cuenta", "industria@gmail.com")
            self.assertEqual(codigo, 1)
            self.assertEqual(correo.leer_cuenta()["clave"], "abcdefghijklmnop")
            # correo de prueba con 5 pesadas de muestra del controlador
            with mock.patch.object(correo, "leer_cuenta", smtp.cuenta):
                codigo, salida, error = correr(comandos.correo, "probar")
            self.assertEqual(codigo, 0, error)
            r = smtp.mensajes[0]
            self.assertEqual(r["para"], ["ktomasl@alicorp.com.pe", "subelrobotin@gmail.com"])
            self.assertEqual(asunto(r["mensaje"]), "Prueba - Registro de Balanza de Coches - PDL")
            _, filas = leer_csv(r["mensaje"])
            self.assertEqual(len(filas), 6)
            self.assertEqual(basedatos.total(con), 0)               # la prueba no guarda nada
            with mock.patch.object(correo, "leer_cuenta", smtp.cuenta):
                correr(comandos.correo, "probar", "otra@empresa.com")
            self.assertEqual(smtp.mensajes[1]["para"], ["otra@empresa.com"])
            self.assertEqual([c["tipo"] for c in basedatos.correos(con, 5)], ["prueba", "prueba"])
            prueba = basedatos.correos(con, 1)[0]
            self.assertTrue(prueba["desde"] and prueba["hasta"] and prueba["desde"] <= prueba["hasta"])
            con.close()
        finally:
            smtp.cerrar()

    @unittest.skipUnless(os.geteuid() == 0, "necesita sudo")
    def test_vaciar_base_detiene_el_correo(self):
        basedatos.guardar_config(basedatos.conectar(), correo_cuenta="industria@gmail.com")
        correr(comandos.correo, "para", "ktomasl@alicorp.com.pe")
        correr(comandos.correo, "5")
        with mock.patch("builtins.input", return_value="BORRAR"):
            correr(comandos.vaciar_base)
        con = basedatos.conectar()
        cfg = basedatos.config(con)
        self.assertEqual((cfg["correo_activo"], cfg["correo_para"]), ("0", "ktomasl@alicorp.com.pe"))
        self.assertEqual(basedatos.total(con), 0)
        con.close()


if __name__ == "__main__":
    unittest.main()
