"""Pruebas de la lectura del controlador (con el simulador; no necesitan base de datos)."""

import unittest
from datetime import datetime
from decimal import Decimal

import ayuda  # noqa: F401  (prepara rutas)
from registro1280.controlador import (ErrorControlador, consultar, interpretar_fila, interpretar_tabla,
                                      leer_pesadas, normalizar_comando)
from simulador_1280 import Simulador

FILA = "188755|77|Sapolio Floral|302.95|22559.85|10012026 0108|10012026 0109|2|0"


class Comandos(unittest.TestCase):
    def test_solo_lectura(self):
        for c in ("VERSION", "version", " DB.DATA#2 ", "db.schema#2", "XG#1", "DB.ALIAS#5", "DB.DATA#12"):
            normalizar_comando(c)
        for c in ("DB.DATA#2=1", "DB.CLEAR#2", "DB.DELALL#2", "RS", "PCLR", "KZERO", "SX#1", "EX#1",
                  "RESETCONFIGURATION", "REMOVE.TSCAL", "DB.DATA#2\rRS", "", "VERSION=1", "DB.DATA#123"):
            with self.assertRaises(ValueError, msg=c):
                normalizar_comando(c)


class Filas(unittest.TestCase):
    def test_fila_real_de_planta(self):
        p = interpretar_fila(FILA)
        self.assertEqual(p.ID, 188755)
        self.assertEqual(p.cor, 77)
        self.assertEqual(p.Producto, "Sapolio Floral")
        self.assertEqual(p.Peso, Decimal("302.95"))
        self.assertEqual(p.Acum, Decimal("22559.85"))
        self.assertEqual(p.FechaI, datetime(2026, 10, 1, 1, 8))
        self.assertEqual(p.FechaF, datetime(2026, 10, 1, 1, 9))
        self.assertEqual((p.Turno, p.Estado), (2, 0))

    def test_decimales_y_largos(self):
        p = interpretar_fila("1|1|%s|1.234567|  -0.5 |01012026 0000|01012026 0001|1|0" % ("X" * 80))
        self.assertEqual(p.Peso, Decimal("1.2346"))
        self.assertEqual(p.Acum, Decimal("-0.5000"))
        self.assertEqual(len(p.Producto), 60)

    def test_filas_invalidas(self):
        malas = [
            "188755|77|Sapolio|302.95|22559.85|10012026 0108|10012026 0109|2",          # 8 columnas
            "188755|77||302.95|22559.85|10012026 0108|10012026 0109|2|0",               # sin producto
            "18x755|77|Sapolio|302.95|22559.85|10012026 0108|10012026 0109|2|0",        # ID
            "188755|77|Sapolio|3o2.95|22559.85|10012026 0108|10012026 0109|2|0",        # peso
            "188755|77|Sapolio|302.95|22559.85|13012026 0108|10012026 0109|2|0",        # mes 13
            "188755|77|Sapolio|302.95|22559.85|10012026|10012026 0109|2|0",             # sin hora
            "188755|77|Sapolio|NaN|22559.85|10012026 0108|10012026 0109|2|0",
            "188755|77|Sapolio|1e20|22559.85|10012026 0108|10012026 0109|2|0",
            "9999999999|77|Sapolio|302.95|22559.85|10012026 0108|10012026 0109|2|0",
        ]
        for m in malas:
            with self.assertRaises(ValueError, msg=m):
                interpretar_fila(m)


class Tabla(unittest.TestCase):
    def test_completa(self):
        pesadas, rechazadas = interpretar_tabla(FILA + "\r\n" + FILA.replace("188755", "188756") + "\r\n", 2)
        self.assertEqual(len(pesadas), 2)
        self.assertEqual(rechazadas, [])

    def test_cortada_no_se_guarda(self):
        with self.assertRaises(ErrorControlador):
            interpretar_tabla(FILA + "\r\n" + FILA[:30], 2)

    def test_incompleta_no_se_guarda(self):
        with self.assertRaises(ErrorControlador):
            interpretar_tabla(FILA + "\r\n", 2)

    def test_desconocido(self):
        with self.assertRaises(ErrorControlador):
            interpretar_tabla("??\r\n", 1)

    def test_una_fila_mala_no_detiene_el_resto(self):
        pesadas, rechazadas = interpretar_tabla(FILA + "\r\nbasura|1\r\n", 2)
        self.assertEqual(len(pesadas), 1)
        self.assertEqual(rechazadas[0].linea, "basura|1")

    def test_todas_malas_es_error(self):
        with self.assertRaises(ErrorControlador):
            interpretar_tabla("basura\r\notra\r\n", 2)


class ConSimulador(unittest.TestCase):
    def setUp(self):
        self.sim = Simulador(iniciales=3000)

    def tearDown(self):
        self.sim.cerrar()

    def test_descarga_completa(self):
        l = leer_pesadas("127.0.0.1", self.sim.puerto)
        self.assertEqual(len(l.pesadas), 3000)
        self.assertEqual(l.esperadas, 3000)
        self.assertEqual(self.sim.comandos, ["DB.SCHEMA#2", "DB.DATA#2"])

    def test_corte_a_mitad(self):
        self.sim.modo = "corta"
        with self.assertRaises(ErrorControlador):
            leer_pesadas("127.0.0.1", self.sim.puerto)

    def test_pausa_larga_a_mitad(self):
        self.sim.modo = "pausa"
        self.assertEqual(len(leer_pesadas("127.0.0.1", self.sim.puerto).pesadas), 3000)

    def test_no_responde(self):
        self.sim.modo = "mudo"
        with self.assertRaises(ErrorControlador):
            leer_pesadas("127.0.0.1", self.sim.puerto)

    def test_ocupado_un_momento_se_vuelve_a_pedir(self):
        self.sim.data_sin_respuesta = 1          # el primer pedido de la tabla no recibe respuesta
        l = leer_pesadas("127.0.0.1", self.sim.puerto, primera_respuesta=1.0, dormir=lambda s: None)
        self.assertEqual(len(l.pesadas), 3000)
        self.assertEqual(self.sim.comandos, ["DB.SCHEMA#2", "DB.DATA#2", "DB.DATA#2"])

    def test_tabla_sin_respuesta(self):
        self.sim.data_sin_respuesta = 10
        with self.assertRaises(ErrorControlador) as e:
            leer_pesadas("127.0.0.1", self.sim.puerto, primera_respuesta=1.0, dormir=lambda s: None)
        self.assertIn("no respondió al comando DB.DATA#2", str(e.exception))
        self.assertEqual(self.sim.comandos.count("DB.DATA#2"), 3)

    def test_sin_conexion(self):
        puerto = self.sim.puerto
        self.sim.cerrar()
        with self.assertRaises(ErrorControlador):
            leer_pesadas("127.0.0.1", puerto)

    def test_tabla_vacia(self):
        vacio = Simulador(iniciales=0)
        try:
            l = leer_pesadas("127.0.0.1", vacio.puerto)
            self.assertEqual(l.pesadas, [])
            self.assertEqual(vacio.comandos, ["DB.SCHEMA#2"])
        finally:
            vacio.cerrar()

    def test_producto_con_enie(self):
        self.sim.agregar(producto="Piña")
        nombres = {p.Producto for p in leer_pesadas("127.0.0.1", self.sim.puerto).pesadas}
        self.assertIn("Piña", nombres)

    def test_mostrar(self):
        texto, _ = consultar("127.0.0.1", self.sim.puerto, "version")
        self.assertEqual(texto, "V2.03.01  1280\r\n")
        texto, _ = consultar("127.0.0.1", self.sim.puerto, "DB.DATA#2")
        self.assertEqual(texto.count("\r\n"), 3000)
        texto, _ = consultar("127.0.0.1", self.sim.puerto, "DB.DATA#2", maximo_bytes=500)
        self.assertEqual(len(texto), 500)
        with self.assertRaises(ValueError):
            consultar("127.0.0.1", self.sim.puerto, "DB.CLEAR#2")
        self.assertNotIn("DB.CLEAR#2", self.sim.comandos)


if __name__ == "__main__":
    unittest.main()
