"""Formato real XLSX: tipos de celda, texto literal y libros con varias hojas."""

import unittest
from datetime import datetime
from decimal import Decimal
from unittest import mock
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import ayuda  # noqa: F401
from registro1280 import excel

N = {"s": excel.NS}


class Excel(unittest.TestCase):
    def test_numeros_fechas_y_productos_son_datos_no_formulas(self):
        fecha = datetime(2026, 10, 19, 9, 15, 32)
        fila = dict(nro=1, ID=203, cor=2, Producto='=HYPERLINK("x") & Limón\x00',
                    Peso=Decimal("302.9500"), Acum=Decimal("22559.8512"), FechaI=fecha, FechaF=fecha,
                    Turno=1, Estado=0, guardado_en=fecha, actualizado_en=None, correo=0)
        with excel.crear(iter([fila])) as archivo, ZipFile(archivo) as libro:
            self.assertIsNone(libro.testzip())
            for nombre in libro.namelist():
                ET.fromstring(libro.read(nombre))
            hoja = ET.fromstring(libro.read("xl/worksheets/sheet1.xml"))
            celdas = {c.get("r"): c for c in hoja.findall(".//s:row[@r='2']/s:c", N)}
            self.assertEqual(celdas["D2"].get("t"), "inlineStr")
            self.assertEqual(celdas["D2"].find("s:is/s:t", N).text, '=HYPERLINK("x") & Limón')
            self.assertEqual(hoja.findall(".//s:f", N), [])
            self.assertEqual(celdas["E2"].find("s:v", N).text, "302.9500")
            self.assertEqual(celdas["F2"].find("s:v", N).text, "22559.8512")
            self.assertEqual(celdas["G2"].get("s"), "3")
            esperado = (fecha - datetime(1899, 12, 30)).total_seconds() / 86400
            self.assertAlmostEqual(float(celdas["G2"].find("s:v", N).text), esperado, places=8)
            self.assertNotIn("L2", celdas)
            self.assertEqual(celdas["M2"].find("s:v", N).text, "0")

    def test_sin_coincidencias_conserva_los_encabezados(self):
        with excel.crear(iter([])) as archivo, ZipFile(archivo) as libro:
            hoja = ET.fromstring(libro.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(len(hoja.findall(".//s:row", N)), 1)
            self.assertEqual([e.text for e in hoja.findall(".//s:t", N)], list(excel.TITULOS))

    def test_continua_en_otra_hoja_sin_perder_registros(self):
        with mock.patch.object(excel, "MAX_FILAS", 3), excel.crear({"ID": i} for i in range(5)) as archivo:
            with ZipFile(archivo) as libro:
                hojas = ET.fromstring(libro.read("xl/workbook.xml")).findall("s:sheets/s:sheet", N)
                self.assertEqual(len(hojas), 3)
                ids = []
                for i in range(1, 4):
                    hoja = ET.fromstring(libro.read("xl/worksheets/sheet%d.xml" % i))
                    filas = hoja.findall("s:sheetData/s:row", N)
                    self.assertLessEqual(len(filas), 3)
                    ids += [int(f.find("s:c/s:v", N).text) for f in filas[1:]]
                self.assertEqual(ids, list(range(5)))

    def test_falla_de_lectura_no_deja_archivo_abierto(self):
        def filas():
            yield {"ID": 1}
            raise RuntimeError("Lectura interrumpida")
        temporal = excel.tempfile.SpooledTemporaryFile()
        with mock.patch.object(excel.tempfile, "SpooledTemporaryFile", return_value=temporal):
            with self.assertRaisesRegex(RuntimeError, "Lectura interrumpida"):
                excel.crear(filas())
        self.assertTrue(temporal.closed)


if __name__ == "__main__":
    unittest.main()
