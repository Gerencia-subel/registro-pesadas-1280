"""Comandos de la terminal: mostrar, guardar, visualizar, bucle, correo, controlador, cambiar-clave."""

import getpass
import os
import subprocess
import sys

import MySQLdb

from . import acciones, basedatos, correo as correo_gmail, formato
from .basedatos import ErrorBase
from .controlador import ErrorControlador, consultar, normalizar_comando
from .horario import leer_fecha, minutos_config
from .reloj import ahora

LATIDO_MAXIMO = 200   # segundos sin latido para decir que el servicio del bucle no responde


def _salir(mensaje, codigo=1):
    print(mensaje, file=sys.stderr)
    return codigo


def _con():
    return basedatos.conectar()


# ------------------------------------------------------------------ mostrar
def mostrar(argumentos):
    """mostrar "DB.DATA#2" [máximo de caracteres]: lo que responde el controlador, tal cual."""
    if not argumentos:
        return _salir('Uso: mostrar "COMANDO" [máximo de caracteres]\n'
                      'Ejemplos: mostrar "VERSION"   mostrar "DB.SCHEMA#2"   mostrar "DB.DATA#2" 1000000')
    try:
        comando = normalizar_comando(argumentos[0])
        maximo = int(argumentos[1]) if len(argumentos) > 1 else 1000000
        if maximo <= 0:
            raise ValueError("El máximo de caracteres debe ser mayor que 0.")
    except ValueError as e:
        return _salir(str(e))
    try:
        cfg = basedatos.config(_con())
    except ErrorBase as e:
        return _salir(str(e))
    ip, puerto = cfg.get("ip"), cfg.get("puerto")
    try:
        respuesta, segundos = consultar(ip, int(puerto), comando, maximo)
    except ErrorControlador as e:
        return _salir("Controlador %s:%s - %s" % (ip, puerto, e))
    # Igual que la función Mostrar de PowerShell: se ven los finales de línea del controlador.
    marcado = respuesta.replace("\r\n", "<CR><LF>\n").replace("\r", "<CR>\n")
    sys.stdout.write(marcado)
    if not marcado.endswith("\n"):
        sys.stdout.write("\n")
    lineas = max(respuesta.count("\r"), respuesta.count("\n"))
    print("--- %s: %s caracteres, %s líneas, %.1f s (controlador %s:%s)"
          % (comando, formato.numero(len(respuesta)), formato.numero(lineas), segundos, ip, puerto))
    if len(respuesta) >= maximo:
        print("--- Se llegó al máximo de %s caracteres: puede faltar parte de la respuesta." % formato.numero(maximo))
    return 0


# ------------------------------------------------------------------ guardar
def guardar(argumentos):
    """guardar: descarga la tabla de pesadas y guarda en la base solo lo nuevo."""
    try:
        con = _con()
        cfg = basedatos.config(con)
        print("Controlador %s:%s: descargando la tabla de pesadas..." % (cfg.get("ip"), cfg.get("puerto")))
        print(acciones.guardar(con, "manual"))
    except ErrorBase as e:
        return _salir("No se pudo guardar: %s" % e)
    except acciones.ErrorAccion as e:
        return _salir(str(e))
    return 0


# ------------------------------------------------------------------ visualizar
_ANCHOS = [("N°", 7), ("ID", 7), ("cor", 4), ("Producto", 25), ("Peso", 15), ("Acum", 17),
           ("FechaI", 16), ("FechaF", 16), ("Turno", 5), ("Estado", 6), ("Guardado", 19)]
_DERECHA = {"N°", "ID", "cor", "Peso", "Acum", "Turno", "Estado"}


def _fila(valores):
    partes = []
    for (nombre, ancho), valor in zip(_ANCHOS, valores):
        valor = str(valor)
        if len(valor) > ancho:
            valor = valor[:ancho - 1] + "…"
        partes.append(valor.rjust(ancho) if nombre in _DERECHA else valor.ljust(ancho))
    return " ".join(partes).rstrip()


def visualizar(argumentos):
    """visualizar: todas las pesadas guardadas. visualizar 20: solo las últimas 20."""
    ultimas = None
    if argumentos:
        try:
            ultimas = int(argumentos[0])
            if ultimas <= 0:
                raise ValueError
        except ValueError:
            return _salir("Uso: visualizar        (todas)\n     visualizar 20     (las últimas 20)")
    try:
        con = _con()
        cabecera = _fila([n for n, _ in _ANCHOS])
        print(cabecera)
        print("-" * len(cabecera))
        mostradas = 0
        for f in basedatos.recorrer(con, ultimas):
            nro, id_, cor, prod, peso, acum, fi, ff, turno, estado, guardado = f
            print(_fila([nro, id_, cor, prod, formato.kg(peso), formato.kg(acum), formato.fecha(fi),
                         formato.fecha(ff), turno, estado, formato.fecha_segundos(guardado)]))
            mostradas += 1
        r = basedatos.resumen(con)
    except ErrorBase as e:
        return _salir(str(e))
    except BrokenPipeError:
        return 0
    print("-" * len(cabecera))
    if r["total"]:
        print("Mostradas: %s | Total en la base: %s pesadas (FechaI del %s al %s)"
              % (formato.numero(mostradas), formato.numero(r["total"]), formato.fecha(r["primera"]),
                 formato.fecha(r["ultima"])))
    else:
        print("La base todavía no tiene pesadas. Usar: guardar")
    return 0


# ------------------------------------------------------------------ bucle
def _estado_servicio(cfg, clave="latido", nombre="del bucle"):
    return acciones.estado_servicio(cfg, clave, nombre, reloj=ahora)


def bucle(argumentos):
    """bucle 5: guardar cada 5 minutos, siempre. bucle detener. bucle (o bucle estado)."""
    accion = argumentos[0].lower() if argumentos else "estado"
    try:
        con = _con()
        if accion in ("detener", "parar", "stop"):
            print(acciones.bucle_detener(con, ahora))
            return 0
        if accion != "estado":
            try:
                print(acciones.bucle_activar(con, accion, ahora))
            except acciones.ErrorAccion:
                return _salir("Uso: bucle 5          guardar cada 5 minutos (siempre, aunque se reinicie la Pi)\n"
                              "     bucle detener    detener el guardado automático\n"
                              "     bucle            ver el estado")
            return 0
        cfg = basedatos.config(con)
        historial = basedatos.descargas(con, 10)
    except ErrorBase as e:
        return _salir(str(e))
    activo = cfg.get("bucle_activo") == "1"
    minutos = cfg.get("bucle_minutos") or "5"
    ok, texto = _estado_servicio(cfg)
    print("Bucle: %s" % ("activo, guarda cada %s minuto(s)" % minutos if activo else "detenido"))
    print("Servicio: %s" % texto)
    print("Controlador: %s:%s" % (cfg.get("ip"), cfg.get("puerto")))
    if not historial:
        print("Todavía no hay descargas.")
        return 0
    print("")
    print("Últimas descargas:")
    print("%-19s %-7s %-6s %7s %7s %7s %8s  %s" % ("Inicio", "Origen", "Result", "Seg", "Leídas", "Nuevas",
                                                    "Total", "Detalle"))
    for d in historial:
        print("%-19s %-7s %-6s %7s %7s %7s %8s  %s" % (
            formato.fecha_segundos(d["inicio"]), d["origen"], d["resultado"],
            "" if d["segundos"] is None else "%.1f" % d["segundos"],
            "" if d["leidas"] is None else d["leidas"], "" if d["nuevas"] is None else d["nuevas"],
            "" if d["total"] is None else d["total"], d["detalle"] or ""))
    return 0


# ------------------------------------------------------------------ correo
_USO_CORREO = """Uso: correo 5                    enviar cada 5 minutos el CSV con las pesadas nuevas de cada tramo
     correo detener              detener el envío
     correo                      ver el estado y los últimos correos
     correo para a@x.com b@y.com destinatarios
     correo copia a@x.com        copia (correo copia ninguna = sin copia)
     sudo correo cuenta          cuenta de Gmail que envía (contraseña de aplicación)
     sudo correo probar          enviar ahora un correo de prueba (sudo correo probar a@x.com: solo a esa dirección)"""


def _hora(momento):
    return momento.strftime("%H:%M") if momento else ""


def correo(argumentos):
    """correo 5 | correo detener | correo | correo para ... | correo copia ... | sudo correo cuenta | sudo correo probar"""
    accion = argumentos[0].lower() if argumentos else "estado"
    resto = argumentos[1:]
    try:
        if accion == "cuenta":
            return _correo_cuenta(resto)
        if accion == "probar":
            if os.geteuid() != 0:
                return _salir("Usar con sudo:  sudo correo probar")
            con = _con()
            print("Leyendo el controlador y enviando el correo de prueba...")
            print(acciones.correo_probar(con, " ".join(resto), reloj=ahora))
            return 0
        con = _con()
        if accion in ("detener", "parar", "stop"):
            print(acciones.correo_detener(con, ahora))
            return 0
        if accion in ("para", "copia"):
            if not resto:
                actual = basedatos.config(con).get("correo_" + accion) or "(ninguno)"
                print("%s: %s" % ("Para" if accion == "para" else "Copia", actual))
                return 0
            print(acciones.correo_direcciones(con, accion, " ".join(resto)))
            return 0
        if accion == "estado":
            return _correo_estado(con)
        try:
            int(accion)
        except ValueError:
            return _salir(_USO_CORREO)
        if basedatos.config(con).get("correo_activo") != "1":
            print("Descargando el controlador para que lo que ya está registrado no se envíe...")
        print(acciones.correo_activar(con, accion, reloj=ahora))
        return 0
    except acciones.ErrorAccion as e:
        return _salir(str(e))
    except ErrorBase as e:
        return _salir(str(e))
    except MySQLdb.Error as e:
        return _salir("Error de la base de datos: %s" % (e.args[1] if len(e.args) > 1 else e))


def _correo_estado(con):
    cfg = basedatos.config(con)
    historial = basedatos.correos(con, 10)
    activo = cfg.get("correo_activo") == "1"
    minutos = minutos_config(cfg)
    if activo:
        print("Correo: activo, cada %d minuto%s" % (minutos, "" if minutos == 1 else "s"))
        proximo, desde = leer_fecha(cfg.get("correo_proximo")), leer_fecha(cfg.get("correo_desde"))
        if proximo:
            print("Próximo envío: %s (pesadas desde %s)" % (formato.fecha(proximo), formato.fecha(desde)))
        print("Pesadas pendientes de enviar: %s" % formato.numero(basedatos.contar_pendientes(con)))
    else:
        print("Correo: detenido. Para activarlo: correo 5")
    ok, texto = _estado_servicio(cfg, "correo_latido", "del correo")
    print("Servicio: %s" % texto)
    print("Cuenta que envía: %s" % (cfg.get("correo_cuenta") or "falta (sudo correo cuenta)"))
    print("Para: %s" % (cfg.get("correo_para") or "(falta: correo para nombre@empresa.com)"))
    print("Copia: %s" % (cfg.get("correo_copia") or "(ninguna)"))
    if not historial:
        print("Todavía no se envió ningún correo.")
        return 0
    print("")
    print("Últimos correos:")
    print("%-6s %-19s %-6s %-29s %8s %-6s  %s" % ("N°", "Enviado", "Tipo", "Tramo", "Pesadas", "Result", "Detalle"))
    for c in historial:
        tramo = correo_gmail.rango(c["desde"], c["hasta"]) if c["desde"] and c["hasta"] else "-"
        print("%-6s %-19s %-6s %-29s %8s %-6s  %s" % (
            c["nro"], formato.fecha_segundos(c["enviado_en"]), c["tipo"], tramo, c["pesadas"], c["resultado"],
            c["detalle"] or ""))
    return 0


def _correo_cuenta(argumentos):
    if os.geteuid() != 0:
        return _salir("Usar con sudo:  sudo correo cuenta")
    con = _con()
    actual = basedatos.config(con).get("correo_cuenta") or ""
    if argumentos:
        usuario = argumentos[0].strip()
    else:
        usuario = input("Correo de Gmail que envía%s: " % (" [%s]" % actual if actual else "")).strip() or actual
    clave = getpass.getpass("Contraseña de aplicación de Gmail (no se ve al escribir; Enter al terminar): ")
    print("Probando la cuenta en Gmail...")
    print(acciones.correo_cuenta(con, usuario, clave))
    return 0


# ------------------------------------------------------------------ controlador
def controlador(argumentos):
    """controlador: ver la IP. controlador IP PUERTO: cambiarla. controlador probar."""
    try:
        con = _con()
        cfg = basedatos.config(con)
        if not argumentos:
            print("Controlador: %s puerto %s" % (cfg.get("ip"), cfg.get("puerto")))
            print("Para cambiarlo: controlador 10.30.211.141 10001   Para probarlo: controlador probar")
            return 0
        if argumentos[0].lower() == "probar":
            print(acciones.controlador_probar(con))
            return 0
        try:
            print(acciones.controlador_cambiar(con, argumentos[0], argumentos[1] if len(argumentos) > 1 else 10001))
        except acciones.ErrorAccion:
            return _salir("Uso: controlador 10.30.211.141 10001")
        return 0
    except (ErrorBase, acciones.ErrorAccion) as e:
        return _salir(str(e))


# ------------------------------------------------------------------ cambiar-clave
def cambiar_clave(argumentos):
    """sudo cambiar-clave: cambia el usuario y la clave de la página web."""
    from . import config_web
    if os.geteuid() != 0:
        return _salir("Usar con sudo:  sudo cambiar-clave")
    ini = config_web.leer()
    actual = ini.get("usuario", "intradevco")
    usuario = input("Usuario de la web [%s]: " % actual).strip() or actual
    clave = getpass.getpass("Clave nueva: ")
    if len(clave) < 4:
        return _salir("La clave debe tener al menos 4 caracteres.")
    if getpass.getpass("Repetir la clave: ") != clave:
        return _salir("Las claves no coinciden. No se cambió nada.")
    config_web.cambiar_clave(usuario, clave)
    subprocess.run(["systemctl", "restart", "registro1280-web"], check=False)
    print("Listo. Usuario: %s. La web ya pide la clave nueva." % usuario)
    return 0


# ------------------------------------------------------------------ vaciar-base
def vaciar_base(argumentos):
    """sudo vaciar-base: borra todas las pesadas (por ejemplo, después de probar con el simulador)."""
    if os.geteuid() != 0:
        return _salir("Usar con sudo:  sudo vaciar-base")
    try:
        con = _con()
        n = basedatos.total(con)
    except ErrorBase as e:
        return _salir(str(e))
    print("Se van a borrar las %s pesadas guardadas, el historial de descargas y de correos, y las filas "
          "rechazadas." % formato.numero(n))
    print("La configuración (IP del controlador, bucle, destinatarios y cuenta de correo) se conserva. "
          "El envío por correo queda detenido. Esto no se puede deshacer.")
    if input('Para confirmar escribir BORRAR: ').strip() != "BORRAR":
        print("No se borró nada.")
        return 1
    basedatos.guardar_config(con, correo_activo="0", correo_desde="", correo_proximo="", correo_detenido="")
    cur = con.cursor()
    for tabla in ("pesadas", "descargas", "rechazadas", "correos"):
        cur.execute("TRUNCATE TABLE %s" % tabla)
    con.commit()
    print("Listo. La base quedó vacía. Para volver a enviar por correo: correo 5")
    return 0


COMANDOS = {
    "mostrar": mostrar,
    "guardar": guardar,
    "visualizar": visualizar,
    "bucle": bucle,
    "correo": correo,
    "controlador": controlador,
    "cambiar-clave": cambiar_clave,
    "vaciar-base": vaciar_base,
}
