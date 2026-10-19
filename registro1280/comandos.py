"""Comandos de la terminal: mostrar, guardar, visualizar, bucle, controlador, cambiar-clave."""

import getpass
import ipaddress
import os
import subprocess
import sys
import time
from datetime import datetime

from . import basedatos, formato
from .basedatos import ErrorBase
from .controlador import ErrorControlador, consultar, normalizar_comando
from .guardado import guardar as guardar_ahora
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
        r = guardar_ahora(con, "manual", reintentos=1)
    except (ErrorBase, ErrorControlador) as e:
        return _salir("No se pudo guardar: %s" % e)
    print("Descarga completa: %s pesadas en %.1f s" % (formato.numero(r["leidas"]), r["segundos"]))
    print("Nuevas: %s | Actualizadas: %s | Ya estaban: %s"
          % (formato.numero(r["nuevas"]), formato.numero(r["actualizadas"]), formato.numero(r["sin_cambio"])))
    if r["rechazadas"]:
        print("Filas con datos inválidos: %s (no se guardaron como pesadas; están en la tabla rechazadas)"
              % formato.numero(r["rechazadas"]))
    print("Total en la base: %s pesadas" % formato.numero(r["total"]))
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
def _estado_servicio(cfg):
    latido = cfg.get("latido") or ""
    try:
        edad = (ahora() - datetime.strptime(latido, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return False, "el servicio del bucle no ha dado señales"
    if edad > LATIDO_MAXIMO:
        return False, "el servicio del bucle no responde (última señal hace %s)" % formato.hace(edad)
    return True, "funcionando"


def bucle(argumentos):
    """bucle 5: guardar cada 5 minutos, siempre. bucle detener. bucle (o bucle estado)."""
    accion = argumentos[0].lower() if argumentos else "estado"
    try:
        con = _con()
        if accion in ("detener", "parar", "stop"):
            basedatos.guardar_config(con, bucle_activo="0", bucle_cambiado=ahora().strftime("%Y-%m-%d %H:%M:%S"))
            print("Bucle detenido. Ya no se guarda automáticamente. Para volver a iniciarlo: bucle 5")
            return 0
        if accion != "estado":
            try:
                minutos = int(accion)
                if minutos < 1:
                    raise ValueError
            except ValueError:
                return _salir("Uso: bucle 5          guardar cada 5 minutos (siempre, aunque se reinicie la Pi)\n"
                              "     bucle detener    detener el guardado automático\n"
                              "     bucle            ver el estado")
            basedatos.guardar_config(con, bucle_activo="1", bucle_minutos=str(minutos),
                                     bucle_cambiado=ahora().strftime("%Y-%m-%d %H:%M:%S"))
            print("Bucle activado: guarda cada %d minuto%s, empezando ahora. Sigue funcionando aunque se "
                  "cierre la ventana o se reinicie la Pi." % (minutos, "" if minutos == 1 else "s"))
            ok, texto = _estado_servicio(basedatos.config(con))
            if not ok:
                print("ATENCIÓN: %s. Revisar: sudo systemctl status registro1280-bucle" % texto)
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
            respuesta, segundos = consultar(cfg.get("ip"), int(cfg.get("puerto")), "VERSION")
            print("El controlador %s:%s respondió: %s (%.1f s)"
                  % (cfg.get("ip"), cfg.get("puerto"), " ".join(respuesta.split()), segundos))
            return 0
        try:
            ip = str(ipaddress.IPv4Address(argumentos[0]))
            puerto = int(argumentos[1]) if len(argumentos) > 1 else 10001
            if not 1 <= puerto <= 65535:
                raise ValueError
        except ValueError:
            return _salir("Uso: controlador 10.30.211.141 10001")
        basedatos.guardar_config(con, ip=ip, puerto=str(puerto))
        print("Controlador guardado: %s puerto %d" % (ip, puerto))
        return 0
    except (ErrorBase, ErrorControlador) as e:
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
    print("Se van a borrar las %s pesadas guardadas, el historial de descargas y las filas rechazadas."
          % formato.numero(n))
    print("La configuración (IP del controlador y bucle) se conserva. Esto no se puede deshacer.")
    if input('Para confirmar escribir BORRAR: ').strip() != "BORRAR":
        print("No se borró nada.")
        return 1
    cur = con.cursor()
    for tabla in ("pesadas", "descargas", "rechazadas"):
        cur.execute("TRUNCATE TABLE %s" % tabla)
    con.commit()
    print("Listo. La base quedó vacía.")
    return 0


COMANDOS = {
    "mostrar": mostrar,
    "guardar": guardar,
    "visualizar": visualizar,
    "bucle": bucle,
    "controlador": controlador,
    "cambiar-clave": cambiar_clave,
    "vaciar-base": vaciar_base,
}
