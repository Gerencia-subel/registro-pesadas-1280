"""Pruebas de los comandos de la terminal (necesitan MariaDB)."""

import contextlib
import io
import os
import unittest
from unittest import mock

import ayuda
from registro1280 import basedatos, comandos
from simulador_1280 import Simulador


def correr(funcion, *argumentos):
    salida, errores = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(salida), contextlib.redirect_stderr(errores):
        codigo = funcion(list(argumentos))
    return codigo, salida.getvalue(), errores.getvalue()


class Comandos(unittest.TestCase):
    def setUp(self):
        ayuda.preparar_base()
        self.sim = Simulador(iniciales=3000)
        con = basedatos.conectar()
        ayuda.con_simulador(con, self.sim)
        con.close()

    def tearDown(self):
        self.sim.cerrar()

    def test_controlador(self):
        codigo, salida, _ = correr(comandos.controlador)
        self.assertIn("127.0.0.1", salida)
        codigo, salida, _ = correr(comandos.controlador, "probar")
        self.assertIn("V2.03.01 1280", salida)
        codigo, _, error = correr(comandos.controlador, "999.1.1.1")
        self.assertEqual(codigo, 1)
        codigo, salida, _ = correr(comandos.controlador, "10.30.211.141", "10001")
        self.assertIn("10.30.211.141 puerto 10001", salida)

    def test_mostrar(self):
        codigo, salida, _ = correr(comandos.mostrar, "DB.DATA#2", "1000000")
        self.assertEqual(codigo, 0)
        self.assertEqual(salida.count("<CR><LF>"), 3000)
        self.assertIn("3,000 líneas", salida)
        codigo, _, error = correr(comandos.mostrar, "DB.CLEAR#2")
        self.assertEqual(codigo, 1)
        self.assertIn("no permitido", error)
        self.assertNotIn("DB.CLEAR#2", self.sim.comandos)

    def test_guardar_visualizar_y_bucle(self):
        codigo, salida, _ = correr(comandos.guardar)
        self.assertIn("Nuevas: 3,000", salida)
        self.assertIn("Total en la base: 3,000 pesadas", salida)
        for _ in range(5):
            self.sim.agregar()
        codigo, salida, _ = correr(comandos.guardar)
        self.assertIn("Nuevas: 5 | Actualizadas: 0 | Ya estaban: 2,995", salida)
        self.assertIn("Total en la base: 3,005 pesadas", salida)
        codigo, salida, _ = correr(comandos.visualizar)
        self.assertEqual(len([l for l in salida.splitlines() if " kg " in l]), 3005)
        self.assertIn("Total en la base: 3,005", salida)
        codigo, salida, _ = correr(comandos.visualizar, "3")
        self.assertIn("Mostradas: 3 | Total en la base: 3,005", salida)
        codigo, salida, _ = correr(comandos.bucle, "5")
        self.assertIn("cada 5 minutos", salida)
        self.assertIn("no ha dado señales", salida)          # aquí no corre el servicio
        codigo, salida, _ = correr(comandos.bucle)
        self.assertIn("activo, guarda cada 5", salida)
        self.assertIn("manual  OK", salida)
        codigo, salida, _ = correr(comandos.bucle, "detener")
        codigo, salida, _ = correr(comandos.bucle)
        self.assertIn("Bucle: detenido", salida)
        codigo, _, error = correr(comandos.bucle, "0")
        self.assertEqual(codigo, 1)

    @unittest.skipUnless(os.geteuid() == 0, "necesita sudo")
    def test_vaciar_base(self):
        correr(comandos.guardar)
        with mock.patch("builtins.input", return_value="no"):
            codigo, salida, _ = correr(comandos.vaciar_base)
        self.assertIn("No se borró nada", salida)
        con = basedatos.conectar()
        self.assertEqual(basedatos.total(con), 3000)
        with mock.patch("builtins.input", return_value="BORRAR"):
            codigo, salida, _ = correr(comandos.vaciar_base)
        self.assertEqual(basedatos.total(con), 0)
        self.assertEqual(basedatos.config(con)["ip"], "127.0.0.1")
        con.close()

    def test_controlador_apagado(self):
        self.sim.modo = "mudo"
        codigo, _, error = correr(comandos.guardar)
        self.assertEqual(codigo, 1)
        self.assertIn("No se pudo guardar", error)


if __name__ == "__main__":
    unittest.main()
