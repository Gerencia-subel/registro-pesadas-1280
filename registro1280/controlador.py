"""Comunicación con el indicador Rice Lake 1280 por TCP. Solo lectura.

El controlador recibe comandos de texto terminados en CR y responde en texto.
Este módulo solo permite comandos de consulta; ninguno cambia el controlador:
  VERSION        versión del firmware
  XG#n           peso actual de la balanza n
  DB.ALIAS#n     nombre de la tabla n
  DB.SCHEMA#n    máximo de registros, registros actuales y columnas de la tabla n
  DB.DATA#n      la tabla n completa (filas con CR/LF, columnas con |)

Tabla 2 (Pesadas), formato confirmado en planta el 05/10/2026:
  188755|77|Sapolio Floral|302.95|22559.85|10012026 0108|10012026 0109|2|0
  ID|cor|Producto|Peso|Acum|FechaI (MMDDYYYY HHMM)|FechaF|Turno|Estado
"""

import re
import socket
import time
from collections import namedtuple
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TABLA_PESADAS = 2
COLUMNAS = ["ID", "cor", "Producto", "Peso", "Acum", "FechaI", "FechaF", "Turno", "Estado"]

# Comandos de lectura permitidos. Cualquier otro se rechaza antes de enviarlo.
_PERMITIDOS = re.compile(r"^(VERSION|XG#\d{1,2}|DB\.ALIAS#\d{1,2}|DB\.SCHEMA#\d{1,2}|DB\.DATA#\d{1,2})$")

Pesada = namedtuple("Pesada", "ID cor Producto Peso Acum FechaI FechaF Turno Estado")
Rechazada = namedtuple("Rechazada", "linea motivo")
Lectura = namedtuple("Lectura", "pesadas rechazadas esperadas segundos")

_FIN_DE_LINEA = re.compile(r"\r\n|\r|\n")
CUATRO_DECIMALES = Decimal("0.0001")


class ErrorControlador(Exception):
    """Falla de conexión o respuesta inválida del controlador."""


def normalizar_comando(comando):
    """Devuelve el comando en mayúsculas si es de solo lectura; si no, lanza ValueError."""
    limpio = (comando or "").strip().upper()
    if "=" in limpio or not _PERMITIDOS.match(limpio):
        raise ValueError(
            "Comando no permitido: %r. Solo se permiten comandos de lectura: "
            "VERSION, XG#n, DB.ALIAS#n, DB.SCHEMA#n, DB.DATA#n." % (comando,)
        )
    return limpio


def enviar_comando(ip, puerto, comando, *, conexion=5.0, primera_respuesta=8.0,
                   silencio=2.0, maximo_segundos=120.0, maximo_bytes=None, completo=None):
    """Envía un comando de lectura y devuelve los bytes de la respuesta.

    El controlador no marca el final de la respuesta, así que se lee hasta que:
    - ``completo(buffer)`` diga que ya llegó todo (y luego 0,3 s sin datos),
    - o pasen ``silencio`` segundos sin recibir nada,
    - o se llegue a ``maximo_bytes``,
    - o el controlador cierre la conexión.
    """
    comando = normalizar_comando(comando)
    inicio = time.monotonic()
    try:
        sock = socket.create_connection((ip, int(puerto)), timeout=conexion)
    except socket.timeout:
        raise ErrorControlador("Sin respuesta del controlador en %s:%s" % (ip, puerto))
    except OSError as e:
        raise ErrorControlador("No se pudo conectar a %s:%s (%s)" % (ip, puerto, _motivo(e)))
    datos = bytearray()
    try:
        sock.sendall((comando + "\r").encode("ascii"))
        sock.settimeout(0.2)
        ultimo_dato = None
        while True:
            ahora = time.monotonic()
            if ahora - inicio > maximo_segundos:
                raise ErrorControlador("La respuesta tardó más de %d s" % maximo_segundos)
            if ultimo_dato is None:
                if ahora - inicio > primera_respuesta:
                    raise ErrorControlador("El controlador no respondió al comando %s" % comando)
            else:
                espera = 0.3 if (completo is not None and completo(datos)) else silencio
                if ahora - ultimo_dato >= espera:
                    break
            if maximo_bytes is not None and len(datos) >= maximo_bytes:
                break
            try:
                trozo = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as e:
                raise ErrorControlador("Se cortó la conexión (%s)" % _motivo(e))
            if not trozo:  # el controlador cerró la conexión
                if ultimo_dato is None:
                    raise ErrorControlador("El controlador cerró la conexión sin responder")
                break
            datos.extend(trozo)
            ultimo_dato = time.monotonic()
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if maximo_bytes is not None:
        return bytes(datos[:maximo_bytes])
    return bytes(datos)


def _motivo(error):
    texto = getattr(error, "strerror", None) or str(error)
    return texto or error.__class__.__name__


def texto(datos):
    """El controlador responde en ASCII. Si un producto trae tildes o ñ se intenta
    UTF-8 y, si no corresponde, latin-1 (nunca falla)."""
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        return datos.decode("latin-1")


def una_linea(datos):
    return b"\r" in datos or b"\n" in datos


def contar_lineas(datos):
    """Cuenta finales de línea (CR/LF cuenta como uno)."""
    return max(datos.count(b"\r"), datos.count(b"\n"))


def leer_esquema(respuesta, tabla=TABLA_PESADAS, columnas=None):
    """Interpreta DB.SCHEMA#n. Devuelve (máximo, actuales, [columnas])."""
    linea = respuesta.strip().splitlines()[0].strip() if respuesta.strip() else ""
    if not linea:
        raise ErrorControlador("El controlador respondió vacío a DB.SCHEMA#%d" % tabla)
    if linea.startswith("??"):
        raise ErrorControlador("El controlador no reconoce la tabla %d (respondió ??)" % tabla)
    partes = [p.strip() for p in linea.split(",")]
    try:
        maximo = int(partes[0])
        actuales = int(partes[1])
    except (ValueError, IndexError):
        raise ErrorControlador("Respuesta inesperada a DB.SCHEMA#%d: %r" % (tabla, linea[:80]))
    nombres = partes[2::3]
    if columnas is not None and [n.lower() for n in nombres] != [c.lower() for c in columnas]:
        raise ErrorControlador(
            "La tabla %d del controlador no tiene las columnas esperadas (tiene: %s)"
            % (tabla, ", ".join(nombres))
        )
    return maximo, actuales, nombres


def _entero(valor, nombre):
    try:
        numero = int(valor.strip())
    except ValueError:
        raise ValueError("%s no es un número entero (%r)" % (nombre, valor))
    if abs(numero) >= 2 ** 31:
        raise ValueError("%s fuera de rango (%r)" % (nombre, valor))
    return numero


def _decimal(valor, nombre, limite):
    try:
        numero = Decimal(valor.strip())
    except InvalidOperation:
        raise ValueError("%s no es un número (%r)" % (nombre, valor))
    if not numero.is_finite() or abs(numero) >= limite:
        raise ValueError("%s fuera de rango (%r)" % (nombre, valor))
    return numero.quantize(CUATRO_DECIMALES, rounding=ROUND_HALF_UP)


def _fecha(valor, nombre):
    try:
        return datetime.strptime(valor.strip(), "%m%d%Y %H%M")
    except ValueError:
        raise ValueError("%s no es una fecha válida (%r)" % (nombre, valor))


def interpretar_fila(linea):
    """Convierte una fila de DB.DATA#2 en Pesada. Lanza ValueError si no es válida."""
    campos = linea.split("|")
    if len(campos) != 9:
        raise ValueError("tiene %d columnas en vez de 9" % len(campos))
    producto = campos[2].strip()
    if not producto:
        raise ValueError("Producto vacío")
    return Pesada(
        ID=_entero(campos[0], "ID"),
        cor=_entero(campos[1], "cor"),
        Producto=producto[:60],
        Peso=_decimal(campos[3], "Peso", Decimal(10) ** 9),
        Acum=_decimal(campos[4], "Acum", Decimal(10) ** 11),
        FechaI=_fecha(campos[5], "FechaI"),
        FechaF=_fecha(campos[6], "FechaF"),
        Turno=_entero(campos[7], "Turno"),
        Estado=_entero(campos[8], "Estado"),
    )


def separar_filas(respuesta):
    """Devuelve (filas completas, resto sin fin de línea)."""
    partes = _FIN_DE_LINEA.split(respuesta)
    resto = partes.pop() if partes else ""
    return [p for p in partes if p.strip()], resto


def interpretar_tabla(respuesta, esperadas):
    """Convierte la respuesta de DB.DATA#2 en (pesadas, rechazadas).

    Si la respuesta llegó cortada o incompleta lanza ErrorControlador y no se guarda
    nada (se vuelve a intentar en la siguiente descarga). Una fila suelta con datos
    inválidos no detiene el guardado: se aparta en "rechazadas".
    """
    filas, resto = separar_filas(respuesta)
    if resto.strip():
        raise ErrorControlador(
            "Lectura incompleta: la última fila llegó cortada (%d filas completas de %d)"
            % (len(filas), esperadas)
        )
    if filas and filas[0].lstrip().startswith("??"):
        raise ErrorControlador("El controlador no reconoce la tabla 2 (respondió ??)")
    if len(filas) < esperadas:
        raise ErrorControlador("Lectura incompleta: llegaron %d de %d filas" % (len(filas), esperadas))
    pesadas, rechazadas = [], []
    for linea in filas:
        try:
            pesadas.append(interpretar_fila(linea))
        except ValueError as e:
            rechazadas.append(Rechazada(linea.strip(), str(e)))
    if filas and not pesadas:
        raise ErrorControlador("Ninguna fila de la tabla 2 es válida. Primera: %r (%s)"
                               % (rechazadas[0].linea[:80], rechazadas[0].motivo))
    return pesadas, rechazadas


def leer_pesadas(ip, puerto):
    """Descarga la tabla Pesadas completa. Lanza ErrorControlador si algo falla."""
    inicio = time.monotonic()
    _, actuales, _ = leer_esquema(
        texto(enviar_comando(ip, puerto, "DB.SCHEMA#2", silencio=1.0, completo=una_linea)),
        columnas=COLUMNAS,
    )
    if actuales == 0:
        return Lectura([], [], 0, time.monotonic() - inicio)
    # Mientras falten filas se toleran pausas de hasta 5 s.
    datos = enviar_comando(
        ip, puerto, "DB.DATA#2", silencio=5.0,
        completo=lambda buf: contar_lineas(buf) >= actuales,
    )
    pesadas, rechazadas = interpretar_tabla(texto(datos), actuales)
    return Lectura(pesadas, rechazadas, actuales, time.monotonic() - inicio)


def consultar(ip, puerto, comando, maximo_bytes=1000000):
    """Envía un comando de lectura y devuelve (texto, segundos). Para el comando mostrar.

    Para DB.DATA#n pide antes DB.SCHEMA#n para saber cuántas filas esperar.
    """
    comando = normalizar_comando(comando)
    inicio = time.monotonic()
    completo = una_linea
    silencio = 1.0
    if comando.startswith("DB.DATA#"):
        tabla = int(comando.split("#")[1])
        try:
            _, actuales, _ = leer_esquema(
                texto(enviar_comando(ip, puerto, "DB.SCHEMA#%d" % tabla, silencio=1.0, completo=una_linea)),
                tabla=tabla,
            )
            completo = lambda buf: contar_lineas(buf) >= max(actuales, 1)
        except ErrorControlador:
            completo = None
        silencio = 5.0
    datos = enviar_comando(ip, puerto, comando, silencio=silencio, completo=completo,
                           maximo_bytes=maximo_bytes)
    return texto(datos), time.monotonic() - inicio
