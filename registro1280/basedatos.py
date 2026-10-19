"""Base de datos MariaDB (compatible con MySQL) del registro de pesadas.

La conexión es por el socket local con autenticación unix_socket: cada usuario de
Linux entra con su propio nombre y sin contraseña. Permisos:
  registro1280     (el bucle)          leer, agregar y actualizar
  registro1280web  (la página web)     solo leer
  usuario de la Pi (los comandos)      leer, agregar y actualizar
Nadie tiene permiso para borrar pesadas.
"""

import os
import pwd
from datetime import timedelta

import MySQLdb
import MySQLdb.cursors

SOCKET = os.environ.get("REGISTRO1280_SOCKET", "/run/mysqld/mysqld.sock")
BASE = os.environ.get("REGISTRO1280_BASE", "registro1280")
# Los candados son de todo el servidor MariaDB: llevan el nombre de la base para que las
# pruebas (base registro1280_prueba) nunca esperen ni choquen con los servicios reales.
CANDADO = "%s_guardar" % BASE
CANDADO_CORREO = "%s_correo" % BASE
LOTE = 500

COLUMNAS_PESADA = ("ID", "cor", "Producto", "Peso", "Acum", "FechaI", "FechaF", "Turno", "Estado")


class ErrorBase(Exception):
    """Problema con la base de datos, explicado en español."""


def usuario_actual():
    return pwd.getpwuid(os.geteuid()).pw_name


def conectar(usuario=None):
    usuario = usuario or usuario_actual()
    try:
        return MySQLdb.connect(unix_socket=SOCKET, user=usuario, database=BASE,
                               charset="utf8mb4", autocommit=False, connect_timeout=10)
    except MySQLdb.OperationalError as e:
        codigo = e.args[0] if e.args else 0
        if codigo in (1045, 1698):
            raise ErrorBase("El usuario %s no tiene acceso a la base. Si se acaba de instalar, cerrar la "
                            "sesión SSH y volver a entrar; si no, usar sudo." % usuario)
        if codigo == 1044:
            raise ErrorBase("El usuario %s no tiene permiso sobre la base %s." % (usuario, BASE))
        if codigo == 1049:
            raise ErrorBase("La base %s no existe. Ejecutar: sudo bash instalar.sh" % BASE)
        if codigo in (2002, 2003, 2006, 2013):
            raise ErrorBase("La base de datos (MariaDB) no está funcionando. Ver: sudo systemctl status mariadb")
        raise ErrorBase("No se pudo conectar a la base: %s" % (e.args[1] if len(e.args) > 1 else e))


# ---------------------------------------------------------------- configuración
def config(con):
    cur = con.cursor()
    cur.execute("SELECT clave, valor FROM configuracion")
    datos = dict(cur.fetchall())
    con.commit()
    return datos


def guardar_config(con, **valores):
    cur = con.cursor()
    for clave, valor in valores.items():
        cur.execute("INSERT INTO configuracion (clave, valor) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE valor = VALUES(valor)", (clave, str(valor)))
    con.commit()


# ---------------------------------------------------------------- guardado
def _iguales(actual, nuevo):
    return tuple(actual) == tuple(nuevo)


def guardar_pesadas(con, pesadas, rechazadas, momento):
    """Guarda solo lo nuevo. Devuelve un dict con nuevas, actualizadas, sin_cambio, total.

    Cada pesada se identifica por (ID, FechaI). Si ya existe y algún dato cambió,
    se actualiza; si es igual, no se toca. Nunca se duplica ni se borra nada.
    Un candado en la base evita que dos guardados (bucle y manual) se crucen.
    """
    por_llave = {}
    for p in pesadas:
        por_llave[(p.ID, p.FechaI)] = p
    cur = con.cursor()
    cur.execute("SELECT GET_LOCK(%s, 120)", (CANDADO,))
    if cur.fetchone()[0] != 1:
        raise ErrorBase("Otro guardado sigue en curso. Intentar de nuevo en un momento.")
    try:
        existentes = {}
        if por_llave:
            fechas = [llave[1] for llave in por_llave]
            cur.execute("SELECT ID, FechaI, cor, Producto, Peso, Acum, FechaF, Turno, Estado "
                        "FROM pesadas WHERE FechaI BETWEEN %s AND %s", (min(fechas), max(fechas)))
            for fila in cur.fetchall():
                existentes[(fila[0], fila[1])] = fila[2:]
        nuevas, cambiadas, sin_cambio = [], [], 0
        for llave, p in por_llave.items():
            valores = (p.cor, p.Producto, p.Peso, p.Acum, p.FechaF, p.Turno, p.Estado)
            actual = existentes.get(llave)
            if actual is None:
                nuevas.append(p)
            elif _iguales(actual, valores):
                sin_cambio += 1
            else:
                cambiadas.append(p)
        for i in range(0, len(nuevas), LOTE):
            cur.executemany(
                "INSERT INTO pesadas (ID, cor, Producto, Peso, Acum, FechaI, FechaF, Turno, Estado, guardado_en) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE ID = ID",
                [tuple(p) + (momento,) for p in nuevas[i:i + LOTE]],
            )
        for p in cambiadas:
            cur.execute(
                "UPDATE pesadas SET cor = %s, Producto = %s, Peso = %s, Acum = %s, FechaF = %s, "
                "Turno = %s, Estado = %s, actualizado_en = %s WHERE ID = %s AND FechaI = %s",
                (p.cor, p.Producto, p.Peso, p.Acum, p.FechaF, p.Turno, p.Estado, momento, p.ID, p.FechaI),
            )
        for r in rechazadas:
            cur.execute(
                "INSERT INTO rechazadas (linea, motivo, primera_vez, ultima_vez) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE ultima_vez = VALUES(ultima_vez), veces = veces + 1, "
                "motivo = VALUES(motivo)",
                (r.linea[:500], r.motivo[:200], momento, momento),
            )
        con.commit()
    except Exception:
        try:
            con.rollback()
        except MySQLdb.Error:
            pass
        raise
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK(%s)", (CANDADO,))
            cur.fetchall()
        except MySQLdb.Error:
            pass
    return {"nuevas": len(nuevas), "actualizadas": len(cambiadas), "sin_cambio": sin_cambio,
            "rechazadas": len(rechazadas), "total": total(con)}


def registrar_descarga(con, **campos):
    columnas = ("inicio", "origen", "resultado", "segundos", "leidas", "nuevas", "actualizadas",
                "sin_cambio", "rechazadas", "total", "detalle")
    valores = [campos.get(c) for c in columnas]
    if valores[-1]:
        valores[-1] = str(valores[-1])[:500]
    cur = con.cursor()
    cur.execute("INSERT INTO descargas (%s) VALUES (%s)" % (", ".join(columnas), ", ".join(["%s"] * len(columnas))),
                valores)
    con.commit()


def borrar_descargas_antiguas(con, antes_de):
    """Solo el historial de descargas (no las pesadas). Lo usa el bucle una vez al día."""
    cur = con.cursor()
    cur.execute("DELETE FROM descargas WHERE inicio < %s", (antes_de,))
    con.commit()
    return cur.rowcount


# ---------------------------------------------------------------- correo
def pendientes_correo(con):
    """Pesadas que todavía no salieron en ningún correo, en orden de FechaI."""
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT nro, %s FROM pesadas WHERE correo IS NULL ORDER BY FechaI, nro"
                % ", ".join(COLUMNAS_PESADA))
    filas = list(cur.fetchall())
    con.commit()
    return filas


def contar_pendientes(con):
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM pesadas WHERE correo IS NULL")
    n = cur.fetchone()[0]
    con.commit()
    return int(n)


def marcar_sin_enviar(con, guardadas_desde=None):
    """Al activar el correo: lo ya guardado queda marcado con 0 y no se envía.

    Con guardadas_desde (cuando se detuvo el correo) solo se marcan las guardadas desde esa
    hora: las que quedaron pendientes mientras el correo estaba activo se siguen enviando.
    """
    cur = con.cursor()
    if guardadas_desde:
        cur.execute("UPDATE pesadas SET correo = 0 WHERE correo IS NULL AND guardado_en >= %s", (guardadas_desde,))
    else:
        cur.execute("UPDATE pesadas SET correo = 0 WHERE correo IS NULL")
    n = cur.rowcount
    con.commit()
    return n


def registrar_correo(con, nros_enviados=(), config_nueva=None, **campos):
    """Anota un correo en la tabla correos, marca sus pesadas y guarda config_nueva, todo junto."""
    columnas = ("enviado_en", "tipo", "desde", "hasta", "pesadas", "resultado", "para", "copia",
                "archivo", "detalle")
    valores = [campos.get(c) for c in columnas]
    for i, largo in ((6, 5000), (7, 5000), (9, 500)):
        if valores[i]:
            valores[i] = str(valores[i])[:largo]
    cur = con.cursor()
    try:
        cur.execute("INSERT INTO correos (%s) VALUES (%s)" % (", ".join(columnas), ", ".join(["%s"] * len(columnas))),
                    valores)
        nro = cur.lastrowid
        nros = list(nros_enviados)
        for i in range(0, len(nros), LOTE):
            parte = nros[i:i + LOTE]
            cur.execute("UPDATE pesadas SET correo = %%s WHERE correo IS NULL AND nro IN (%s)"
                        % ", ".join(["%s"] * len(parte)), [nro] + parte)
        for clave, valor in (config_nueva or {}).items():
            cur.execute("INSERT INTO configuracion (clave, valor) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE valor = VALUES(valor)", (clave, str(valor)))
        con.commit()
    except Exception:
        try:
            con.rollback()
        except MySQLdb.Error:
            pass
        raise
    return nro


def correos(con, limite=10):
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT * FROM correos ORDER BY nro DESC LIMIT %s", (int(limite),))
    filas = list(cur.fetchall())
    con.commit()
    return filas


def listar_correos(con, pagina=1, por_pagina=5):
    """Historial completo de correos, del más reciente al más antiguo, por páginas."""
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT COUNT(*) AS n FROM correos")
    cantidad = int(cur.fetchone()["n"])
    paginas = max(1, (cantidad + por_pagina - 1) // por_pagina)
    pagina = min(max(1, int(pagina)), paginas)
    cur.execute("SELECT * FROM correos ORDER BY nro DESC LIMIT %s OFFSET %s",
                (int(por_pagina), (pagina - 1) * int(por_pagina)))
    filas = list(cur.fetchall())
    con.commit()
    return filas, cantidad, pagina


def tomar_candado_correo(con):
    """Evita que dos procesos envíen el mismo tramo a la vez. True si se obtuvo."""
    cur = con.cursor()
    cur.execute("SELECT GET_LOCK(%s, 0)", (CANDADO_CORREO,))
    ok = cur.fetchone()[0] == 1
    con.commit()
    return ok


def soltar_candado_correo(con):
    try:
        cur = con.cursor()
        cur.execute("SELECT RELEASE_LOCK(%s)", (CANDADO_CORREO,))
        cur.fetchall()
        con.commit()
    except MySQLdb.Error:
        pass


# ---------------------------------------------------------------- pedidos de la web
def crear_pedido(con, tipo, datos, creado, origen=""):
    cur = con.cursor()
    cur.execute("INSERT INTO pedidos (creado, origen, tipo, datos) VALUES (%s, %s, %s, %s)",
                (creado, (origen or "")[:80], tipo[:30], (datos or "")[:6000]))
    nro = cur.lastrowid
    con.commit()
    return nro


def pedidos_pendientes(con):
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT nro, creado, tipo, datos FROM pedidos WHERE estado = 'PENDIENTE' ORDER BY nro")
    filas = list(cur.fetchall())
    con.commit()
    return filas


def borrar_datos_pedido(con, nro):
    cur = con.cursor()
    cur.execute("UPDATE pedidos SET datos = '' WHERE nro = %s", (nro,))
    con.commit()


def terminar_pedido(con, nro, estado, resultado, momento):
    cur = con.cursor()
    cur.execute("UPDATE pedidos SET estado = %s, resultado = %s, atendido = %s WHERE nro = %s",
                (estado, (resultado or "")[:2000], momento, nro))
    con.commit()


def pedido(con, nro):
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT nro, creado, tipo, estado, resultado, atendido FROM pedidos WHERE nro = %s", (nro,))
    fila = cur.fetchone()
    con.commit()
    return fila


def ultimos_pedidos(con, limite=15):
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT nro, creado, origen, tipo, estado, resultado, atendido FROM pedidos "
                "ORDER BY nro DESC LIMIT %s", (int(limite),))
    filas = list(cur.fetchall())
    con.commit()
    return filas


# ---------------------------------------------------------------- consultas
def total(con):
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM pesadas")
    n = cur.fetchone()[0]
    con.commit()
    return int(n)


def descargas(con, limite=10):
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT * FROM descargas ORDER BY nro DESC LIMIT %s", (int(limite),))
    filas = list(cur.fetchall())
    con.commit()
    return filas


def resumen(con):
    cur = con.cursor()
    cur.execute("SELECT COUNT(*), MIN(FechaI), MAX(FechaI) FROM pesadas")
    cantidad, primera, ultima = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM rechazadas")
    rechazadas = cur.fetchone()[0]
    con.commit()
    ultima_descarga = descargas(con, 1)
    ultimo_correo = correos(con, 1)
    return {"total": int(cantidad), "primera": primera, "ultima": ultima, "rechazadas": int(rechazadas),
            "ultima_descarga": ultima_descarga[0] if ultima_descarga else None,
            "ultimo_correo": ultimo_correo[0] if ultimo_correo else None, "config": config(con)}


def _filtros(desde=None, hasta=None, producto=None, id_ctrl=None, hasta_exclusivo=False):
    condiciones, argumentos = [], []
    if desde:
        condiciones.append("FechaI >= %s")
        argumentos.append(desde)
    if hasta:
        condiciones.append("FechaI < %s")
        argumentos.append(hasta if hasta_exclusivo else hasta + timedelta(days=1))
    if producto:
        texto = producto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        condiciones.append("Producto LIKE %s")
        argumentos.append("%" + texto + "%")
    if id_ctrl is not None:
        condiciones.append("ID = %s")
        argumentos.append(id_ctrl)
    return (" WHERE " + " AND ".join(condiciones) if condiciones else ""), argumentos


def listar(con, desde=None, hasta=None, producto=None, id_ctrl=None, pagina=1, por_pagina=100,
           hasta_exclusivo=False):
    """Pesadas de la más reciente a la más antigua. Hasta incluye el día salvo hasta_exclusivo."""
    donde, argumentos = _filtros(desde, hasta, producto, id_ctrl, hasta_exclusivo)
    cur = con.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT COUNT(*) AS n FROM pesadas" + donde, argumentos)
    cantidad = int(cur.fetchone()["n"])
    pagina = max(1, int(pagina))
    cur.execute("SELECT nro, %s, guardado_en, actualizado_en, correo FROM pesadas%s "
                "ORDER BY FechaI DESC, nro DESC LIMIT %%s OFFSET %%s" % (", ".join(COLUMNAS_PESADA), donde),
                argumentos + [int(por_pagina), (pagina - 1) * int(por_pagina)])
    filas = list(cur.fetchall())
    con.commit()
    return filas, cantidad


def recorrer(con, ultimas=None):
    """Para el comando visualizar: todas (o las últimas N) en orden de FechaI, sin cargarlas en memoria."""
    cur = con.cursor(MySQLdb.cursors.SSCursor)
    columnas = "nro, %s, guardado_en" % ", ".join(COLUMNAS_PESADA)
    if ultimas:
        cur.execute("SELECT * FROM (SELECT %s FROM pesadas ORDER BY FechaI DESC, nro DESC LIMIT %%s) t "
                    "ORDER BY FechaI, nro" % columnas, (int(ultimas),))
    else:
        cur.execute("SELECT %s FROM pesadas ORDER BY FechaI, nro" % columnas)
    try:
        for fila in cur:
            yield fila
    finally:
        cur.close()
        con.commit()


def exportar_pesadas(con, desde=None, hasta=None, producto=None, id_ctrl=None, hasta_exclusivo=False):
    """Todas las coincidencias, en el mismo orden que la web, sin límite de página."""
    donde, argumentos = _filtros(desde, hasta, producto, id_ctrl, hasta_exclusivo)
    cur = con.cursor(MySQLdb.cursors.SSDictCursor)
    cur.execute("SELECT nro, %s, guardado_en, actualizado_en, correo FROM pesadas%s "
                "ORDER BY FechaI DESC, nro DESC" % (", ".join(COLUMNAS_PESADA), donde), argumentos)
    try:
        yield from cur
    finally:
        cur.close()
        con.commit()
