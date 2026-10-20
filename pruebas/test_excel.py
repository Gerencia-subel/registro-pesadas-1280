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
    def test_tabla_estructurada_con_nombre_y_relaciones_validas(self):
        with excel.crear(iter([{"ID": 1}, {"ID": 2}])) as archivo, ZipFile(archivo) as libro:
            hoja = ET.fromstring(libro.read("xl/worksheets/sheet1.xml"))
            parte = hoja.find("s:tableParts/s:tablePart", N)
            self.assertIsNotNone(parte)
            relacion = parte.get("{%s}id" % excel.NS_REL)
            ns_rel = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
            relaciones = ET.fromstring(libro.read("xl/worksheets/_rels/sheet1.xml.rels"))
            destino = relaciones.find("r:Relationship[@Id='%s']" % relacion, ns_rel)
            self.assertEqual(destino.get("Type"), excel.NS_REL + "/table")
            self.assertEqual(destino.get("Target"), "../tables/table1.xml")
            tabla = ET.fromstring(libro.read("xl/tables/table1.xml"))
            self.assertEqual(tabla.get("name"), "TablaBalanzacoches")
            self.assertEqual(tabla.get("displayName"), "TablaBalanzacoches")
            self.assertEqual(tabla.get("ref"), "A1:M3")
            self.assertEqual(tabla.find("s:autoFilter", N).get("ref"), "A1:M3")
            self.assertEqual([c.get("name") for c in tabla.findall("s:tableColumns/s:tableColumn", N)],
                             list(excel.TITULOS))
            tipos = ET.fromstring(libro.read("[Content_Types].xml"))
            tipo = next(t for t in tipos if t.get("PartName") == "/xl/tables/table1.xml")
            self.assertEqual(tipo.get("ContentType"),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml")

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
            tabla = ET.fromstring(libro.read("xl/tables/table1.xml"))
            self.assertEqual((tabla.get("displayName"), tabla.get("ref")), ("TablaBalanzacoches", "A1:M2"))

    def test_continua_en_otra_hoja_sin_perder_registros(self):
        with mock.patch.object(excel, "MAX_FILAS", 3), excel.crear({"ID": i} for i in range(5)) as archivo:
            with ZipFile(archivo) as libro:
                hojas = ET.fromstring(libro.read("xl/workbook.xml")).findall("s:sheets/s:sheet", N)
                self.assertEqual(len(hojas), 3)
                ids = []
                nombres = []
                for i in range(1, 4):
                    hoja = ET.fromstring(libro.read("xl/worksheets/sheet%d.xml" % i))
                    filas = hoja.findall("s:sheetData/s:row", N)
                    self.assertLessEqual(len(filas), 3)
                    ids += [int(f.find("s:c/s:v", N).text) for f in filas[1:]]
                    tabla = ET.fromstring(libro.read("xl/tables/table%d.xml" % i))
                    nombres.append(tabla.get("displayName"))
                    self.assertEqual(tabla.get("ref"), "A1:M%d" % len(filas))
                self.assertEqual(ids, list(range(5)))
                self.assertEqual(nombres, ["TablaBalanzacoches", "TablaBalanzacoches_2", "TablaBalanzacoches_3"])

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
