"""Pruebas del guardado en la base (necesitan MariaDB: sudo python3 -m unittest)."""

import threading
import unittest
from datetime import datetime, timedelta

import ayuda
from registro1280 import basedatos
from registro1280.controlador import ErrorControlador
from registro1280.guardado import guardar
from simulador_1280 import Simulador


def consulta(con, sql, *args):
    cur = con.cursor()
    cur.execute(sql, args)
    filas = cur.fetchall()
    con.commit()
    return filas


class Guardado(unittest.TestCase):
    def setUp(self):
        ayuda.preparar_base()
        self.sim = Simulador(iniciales=3000)
        self.con = basedatos.conectar()
        ayuda.con_simulador(self.con, self.sim)

    def tearDown(self):
        self.sim.cerrar()
        self.con.close()

    def sin_duplicados(self):
        n, distintos = consulta(self.con, "SELECT COUNT(*), COUNT(DISTINCT ID, FechaI) FROM pesadas")[0]
        self.assertEqual(n, distintos)
        return n

    def test_ejemplo_3000_y_luego_3005(self):
        r = guardar(self.con, "manual")
        self.assertEqual((r["leidas"], r["nuevas"], r["total"]), (3000, 3000, 3000))
        r = guardar(self.con, "manual")
        self.assertEqual((r["nuevas"], r["sin_cambio"], r["total"]), (0, 3000, 3000))
        for _ in range(5):
            self.sim.agregar()
        r = guardar(self.con, "manual")
        self.assertEqual((r["leidas"], r["nuevas"], r["actualizadas"], r["total"]), (3000, 5, 0, 3005))
        self.assertEqual(self.sin_duplicados(), 3005)

    def test_controlador_lleno_sobrescribe_y_no_se_pierde_nada(self):
        guardar(self.con, "manual")
        for vuelta in range(6):           # 6 x 500 = la tabla del controlador se renueva completa
            for _ in range(500):
                self.sim.agregar()
            r = guardar(self.con, "bucle")
            self.assertEqual(r["nuevas"], 500)
        self.assertEqual(self.sin_duplicados(), 6000)

    def test_id_reiniciado_no_pisa_pesadas_viejas(self):
        guardar(self.con, "manual")
        primer_id = consulta(self.con, "SELECT MIN(ID) FROM pesadas")[0][0]
        self.sim.reiniciar_ids(primer_id)   # el contador vuelve a un ID que ya existe en la base
        for _ in range(10):
            self.sim.agregar()
        r = guardar(self.con, "manual")
        self.assertEqual(r["nuevas"], 10)
        self.assertEqual(consulta(self.con, "SELECT COUNT(*) FROM pesadas WHERE ID = %s", primer_id)[0][0], 2)
        self.assertEqual(self.sin_duplicados(), 3010)

    def test_si_cambia_un_dato_se_actualiza_sin_duplicar(self):
        guardar(self.con, "manual")
        with self.sim._bloqueo:
            partes = self.sim.filas[10].split("|")
            partes[8] = "1"
            self.sim.filas[10] = "|".join(partes)
        r = guardar(self.con, "manual")
        self.assertEqual((r["nuevas"], r["actualizadas"], r["total"]), (0, 1, 3000))
        estado, actualizado = consulta(self.con, "SELECT Estado, actualizado_en FROM pesadas WHERE ID = %s",
                                       int(partes[0]))[0]
        self.assertEqual(estado, 1)
        self.assertIsNotNone(actualizado)
        r = guardar(self.con, "manual")
        self.assertEqual(r["actualizadas"], 0)

    def test_fila_invalida_se_aparta_una_sola_vez(self):
        with self.sim._bloqueo:
            self.sim.filas[5] = "188760|6|Sapolio|ERROR|1|10012026 0108|10012026 0109|1|0"
        r = guardar(self.con, "manual")
        self.assertEqual((r["nuevas"], r["rechazadas"], r["total"]), (2999, 1, 2999))
        guardar(self.con, "manual")
        filas = consulta(self.con, "SELECT linea, veces FROM rechazadas")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0][1], 2)

    def test_decimales_extra_no_cuentan_como_cambio(self):
        with self.sim._bloqueo:
            self.sim.filas[0] = "1|1|Granel|302.123456|302.123456|10012026 0108|10012026 0109|1|0"
        guardar(self.con, "manual")
        r = guardar(self.con, "manual")
        self.assertEqual(r["actualizadas"], 0)

    def test_dos_guardados_a_la_vez(self):
        otra = basedatos.conectar()
        resultados = []

        def correr(c):
            resultados.append(guardar(c, "manual"))

        hilos = [threading.Thread(target=correr, args=(c,)) for c in (self.con, otra)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        otra.close()
        self.assertEqual(sorted(r["nuevas"] for r in resultados), [0, 3000])
        self.assertEqual(self.sin_duplicados(), 3000)

    def test_controlador_caido_no_guarda_y_queda_anotado(self):
        self.sim.modo = "mudo"
        with self.assertRaises(ErrorControlador):
            guardar(self.con, "bucle", reintentos=1, dormir=lambda s: None)
        self.assertEqual(basedatos.total(self.con), 0)
        d = basedatos.descargas(self.con, 1)[0]
        self.assertEqual((d["resultado"], d["origen"]), ("ERROR", "bucle"))
        self.assertIn("no respondió", d["detalle"])

    def test_lectura_cortada_no_guarda_nada(self):
        self.sim.modo = "corta"
        with self.assertRaises(ErrorControlador):
            guardar(self.con, "manual")
        self.assertEqual(basedatos.total(self.con), 0)
        self.sim.modo = "normal"
        self.assertEqual(guardar(self.con, "manual")["total"], 3000)

    def test_listar_y_filtros(self):
        guardar(self.con, "manual")
        filas, n = basedatos.listar(self.con, por_pagina=50)
        self.assertEqual((len(filas), n), (50, 3000))
        self.assertGreaterEqual(filas[0]["FechaI"], filas[-1]["FechaI"])
        dia = filas[0]["FechaI"].replace(hour=0, minute=0)
        _, del_dia = basedatos.listar(self.con, desde=dia, hasta=dia)
        esperado = consulta(self.con, "SELECT COUNT(*) FROM pesadas WHERE DATE(FechaI) = %s", dia.date())[0][0]
        self.assertEqual(del_dia, esperado)
        _, n = basedatos.listar(self.con, producto="%")
        self.assertEqual(n, 0)
        _, n = basedatos.listar(self.con, id_ctrl=filas[0]["ID"])
        self.assertEqual(n, 1)
        self.assertEqual(len(list(basedatos.recorrer(self.con))), 3000)
        ultimas = list(basedatos.recorrer(self.con, 5))
        self.assertEqual(len(ultimas), 5)
        self.assertLessEqual(ultimas[0][6], ultimas[-1][6])


if __name__ == "__main__":
    unittest.main()
