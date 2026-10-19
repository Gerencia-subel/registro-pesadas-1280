"""Exportación XLSX sin dependencias adicionales, con números y fechas de Excel."""

import re
import tempfile
from datetime import datetime
from decimal import Decimal
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

MAX_FILAS = 1048576  # Límite de Excel; se continúa en otra hoja si hace falta.
COLUMNAS = ("nro", "ID", "cor", "Producto", "Peso", "Acum", "FechaI", "FechaF", "Turno",
            "Estado", "guardado_en", "actualizado_en", "correo")
TITULOS = ("N°", "ID", "cor", "Producto", "Peso (kg)", "Acumulado (kg)", "Fecha de inicio",
           "Fecha de fin", "Turno", "Estado", "Guardado", "Actualizado", "Correo")
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_CONTROLES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _fila(numero, valores, encabezado=False):
    celdas = []
    for indice, valor in enumerate(valores):
        if valor is None:
            continue
        referencia = "%s%d" % (chr(65 + indice), numero)
        if isinstance(valor, datetime):
            serial = (valor - datetime(1899, 12, 30)).total_seconds() / 86400
            celda = '<c r="%s" s="3"><v>%.10f</v></c>' % (referencia, serial)
        elif isinstance(valor, (int, Decimal)):
            estilo = 2 if isinstance(valor, Decimal) else 0
            celda = '<c r="%s" s="%d"><v>%s</v></c>' % (referencia, estilo, valor)
        else:
            # inlineStr conserva literalmente productos que empiezan por =, +, - o @:
            # Excel no los interpreta como fórmulas.
            texto = escape(_CONTROLES.sub("", str(valor)))
            celda = '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' \
                    % (referencia, 1 if encabezado else 0, texto)
        celdas.append(celda)
    return ('<row r="%d">%s</row>' % (numero, "".join(celdas))).encode("utf-8")


def crear(filas):
    """Consume filas sin cargarlas todas en memoria. Devuelve un archivo temporal abierto."""
    archivo = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    try:
        with ZipFile(archivo, "w", ZIP_DEFLATED) as libro:
            hoja = None
            numero_hoja, numero_fila = 0, MAX_FILAS
            try:
                # Siempre se crea al menos una hoja, incluso sin resultados.
                for fila in _con_encabezado(filas):
                    if numero_fila == MAX_FILAS:
                        if hoja is not None:
                            hoja.write(('</sheetData><autoFilter ref="A1:M%d"/></worksheet>' % numero_fila).encode())
                            hoja.close()
                        numero_hoja += 1
                        hoja = libro.open("xl/worksheets/sheet%d.xml" % numero_hoja, "w")
                        hoja.write(('<worksheet xmlns="%s"><sheetViews><sheetView workbookViewId="0">'
                                    '<pane ySplit="1" topLeftCell="A2" state="frozen"/>'
                                    '</sheetView></sheetViews><cols><col min="1" max="3" width="12" customWidth="1"/>'
                                    '<col min="4" max="4" width="30" customWidth="1"/>'
                                    '<col min="5" max="6" width="18" customWidth="1"/>'
                                    '<col min="7" max="8" width="22" customWidth="1"/>'
                                    '<col min="9" max="10" width="10" customWidth="1"/>'
                                    '<col min="11" max="12" width="22" customWidth="1"/>'
                                    '<col min="13" max="13" width="12" customWidth="1"/></cols><sheetData>' % NS).encode())
                        hoja.write(_fila(1, TITULOS, encabezado=True))
                        numero_fila = 1
                    if fila is not None:
                        numero_fila += 1
                        hoja.write(_fila(numero_fila, [fila.get(c) for c in COLUMNAS]))
                hoja.write(('</sheetData><autoFilter ref="A1:M%d"/></worksheet>' % numero_fila).encode())
            finally:
                if hoja is not None:
                    hoja.close()
            _estructura(libro, numero_hoja)
        archivo.seek(0)
        return archivo
    except Exception:
        archivo.close()
        raise


def _con_encabezado(filas):
    yield None
    yield from filas


def _estructura(libro, hojas):
    relaciones = "http://schemas.openxmlformats.org/package/2006/relationships"
    tipo = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    libro.writestr("_rels/.rels", '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%sofficeDocument" '
                   'Target="xl/workbook.xml"/></Relationships>' % (relaciones, tipo))
    libro.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'
                   % (NS, tipo.rstrip("/"), "".join('<sheet name="Pesadas %d" sheetId="%d" r:id="rId%d"/>'
                      % (i, i, i) for i in range(1, hojas + 1))))
    libro.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="%s">%s<Relationship Id="rIdStyles" '
                   'Type="%sstyles" Target="styles.xml"/></Relationships>' % (relaciones,
                   "".join('<Relationship Id="rId%d" Type="%sworksheet" Target="worksheets/sheet%d.xml"/>'
                           % (i, tipo, i) for i in range(1, hojas + 1)), tipo))
    contenido = "application/vnd.openxmlformats-officedocument.spreadsheetml."
    libro.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="%ssheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="%sstyles+xml"/>%s</Types>'
                   % (contenido, contenido, "".join('<Override PartName="/xl/worksheets/sheet%d.xml" '
                      'ContentType="%sworksheet+xml"/>' % (i, contenido) for i in range(1, hojas + 1))))
    libro.writestr("xl/styles.xml", '<styleSheet xmlns="%s">' % NS +
                   '<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00##"/>'
                   '<numFmt numFmtId="165" formatCode="dd/mm/yyyy hh:mm:ss"/></numFmts>'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                   '<fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                   '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
                   '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
