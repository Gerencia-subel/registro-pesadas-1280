"""Comandos de la terminal: mostrar, guardar, visualizar, bucle, correo, controlador, cambiar-clave."""

import getpass
import ipaddress
import os
import socket
import subprocess
import sys
from datetime import datetime

from . import basedatos, correo as correo_gmail, formato
from .basedatos import ErrorBase
from .controlador import ErrorControlador, consultar, leer_pesadas, normalizar_comando
from .envio import leer_fecha, minutos_config, siguiente_envio, texto_fecha
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
def _estado_servicio(cfg, clave="latido", nombre="del bucle"):
    latido = cfg.get(clave) or ""
    try:
        edad = (ahora() - datetime.strptime(latido, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return False, "el servicio %s no ha dado señales" % nombre
    if edad > LATIDO_MAXIMO:
        return False, "el servicio %s no responde (última señal hace %s)" % (nombre, formato.hace(edad))
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
    if accion == "cuenta":
        return _correo_cuenta(resto)
    if accion == "probar":
        return _correo_probar(resto)
    try:
        con = _con()
        if accion in ("detener", "parar", "stop"):
            basedatos.guardar_config(con, correo_activo="0")
            print("Envío por correo detenido. Para volver a activarlo: correo 5")
            print("(Al activarlo de nuevo, lo que se guarde mientras esté detenido no se envía.)")
            return 0
        if accion in ("para", "copia"):
            return _correo_direcciones(con, accion, resto)
        if accion == "estado":
            return _correo_estado(con)
        try:
            minutos = int(accion)
            if minutos < 1:
                raise ValueError
        except ValueError:
            return _salir(_USO_CORREO)
        return _correo_activar(con, minutos)
    except ErrorBase as e:
        return _salir(str(e))


def _correo_direcciones(con, accion, resto):
    clave = "correo_para" if accion == "para" else "correo_copia"
    if not resto:
        actual = basedatos.config(con).get(clave) or "(ninguno)"
        print("%s: %s" % ("Para" if accion == "para" else "Copia", actual))
        return 0
    texto = " ".join(resto)
    if accion == "copia" and texto.strip().lower() in ("ninguna", "ninguno", "no", "-"):
        basedatos.guardar_config(con, correo_copia="")
        print("Sin copia.")
        return 0
    try:
        lista = correo_gmail.direcciones(texto)
    except ValueError as e:
        return _salir(str(e))
    if not lista:
        return _salir(_USO_CORREO)
    basedatos.guardar_config(con, **{clave: ", ".join(lista)})
    print("%s: %s" % ("Para" if accion == "para" else "Copia", ", ".join(lista)))
    return 0


def _correo_activar(con, minutos):
    cfg = basedatos.config(con)
    if not correo_gmail.hay_cuenta():
        return _salir("Primero configurar la cuenta de Gmail que envía:  sudo correo cuenta")
    if not (cfg.get("correo_para") or "").strip():
        return _salir("Primero indicar a quién se envía:  correo para nombre@empresa.com")
    plural = "" if minutos == 1 else "s"
    if cfg.get("correo_activo") == "1":
        anterior = minutos_config(cfg)
        proximo = leer_fecha(cfg.get("correo_proximo"))
        basedatos.guardar_config(con, correo_minutos=str(minutos))
        if minutos == anterior:
            print("El correo ya está activo cada %d minuto%s. Próximo envío: %s." % (minutos, plural, _hora(proximo)))
        else:
            print("Intervalo cambiado a %d minuto%s. El próximo correo sale a las %s, como estaba programado; "
                  "desde ahí, cada %d minuto%s." % (minutos, plural, _hora(proximo), minutos, plural))
        return 0
    print("Descargando el controlador para que lo que ya está registrado no se envíe...")
    try:
        guardar_ahora(con, "manual", reintentos=1)
    except (ErrorControlador, ErrorBase) as e:
        return _salir("No se activó el correo: no se pudo leer el controlador (%s).\n"
                      "Revisar con: controlador probar  y volver a intentar." % e)
    anteriores = basedatos.marcar_sin_enviar(con)
    momento = ahora()
    proximo = siguiente_envio(momento, minutos)
    basedatos.guardar_config(con, correo_minutos=str(minutos), correo_desde=texto_fecha(momento),
                             correo_proximo=texto_fecha(proximo), correo_activo="1")
    print("Correo activado: cada %d minuto%s. Primer envío a las %s con las pesadas desde las %s."
          % (minutos, plural, _hora(proximo), _hora(momento)))
    print("Cada correo lleva solo las pesadas nuevas de su tramo; ninguna se envía dos veces.")
    print("Lo que ya estaba guardado (%s pesadas) no se envía." % formato.numero(anteriores))
    ok, texto = _estado_servicio(basedatos.config(con), "correo_latido", "del correo")
    if not ok:
        print("ATENCIÓN: %s. Revisar: sudo systemctl status registro1280-correo" % texto)
    return 0


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
    cuenta = cfg.get("correo_cuenta") or ""
    if not correo_gmail.hay_cuenta():
        cuenta = "falta (sudo correo cuenta)"
    print("Cuenta que envía: %s" % (cuenta or "configurada"))
    print("Para: %s" % (cfg.get("correo_para") or "(falta: correo para nombre@empresa.com)"))
    print("Copia: %s" % (cfg.get("correo_copia") or "(ninguna)"))
    if not historial:
        print("Todavía no se envió ningún correo.")
        return 0
    print("")
    print("Últimos correos:")
    print("%-6s %-19s %-6s %-29s %8s %-6s  %s" % ("N°", "Enviado", "Tipo", "Tramo", "Pesadas", "Result", "Detalle"))
    for c in historial:
        tramo = correo_gmail.rango(c["desde"], c["hasta"]) if c["desde"] and c["hasta"] else ""
        print("%-6s %-19s %-6s %-29s %8s %-6s  %s" % (
            c["nro"], formato.fecha_segundos(c["enviado_en"]), c["tipo"], tramo, c["pesadas"], c["resultado"],
            c["detalle"] or ""))
    return 0


def _correo_cuenta(argumentos):
    if os.geteuid() != 0:
        return _salir("Usar con sudo:  sudo correo cuenta")
    actual = ""
    try:
        actual = correo_gmail.leer_cuenta()["usuario"]
    except correo_gmail.ErrorCorreo:
        pass
    if argumentos:
        usuario = argumentos[0].strip()
    else:
        usuario = input("Correo de Gmail que envía%s: " % (" [%s]" % actual if actual else "")).strip() or actual
    try:
        lista = correo_gmail.direcciones(usuario)
        if len(lista) != 1:
            raise ValueError("Indicar una sola dirección de Gmail.")
    except ValueError as e:
        return _salir(str(e))
    usuario = lista[0]
    clave = getpass.getpass("Contraseña de aplicación de Gmail (no se ve al escribir; Enter al terminar): ")
    clave = "".join(clave.split())
    if not clave:
        return _salir("No se escribió la contraseña. No se cambió nada.")
    if len(clave) != 16:
        print("Atención: la contraseña de aplicación de Gmail tiene 16 letras y esta tiene %d." % len(clave))
    cuenta = {"usuario": usuario, "clave": clave, "servidor": correo_gmail.SERVIDOR, "puerto": correo_gmail.PUERTO,
              "seguridad": "starttls", "nombre": correo_gmail.NOMBRE}
    print("Probando la cuenta en Gmail...")
    try:
        correo_gmail.probar_cuenta(cuenta)
        print("Gmail aceptó la cuenta.")
    except correo_gmail.ErrorCorreo as e:
        if not e.reintentar:
            return _salir("No se guardó la cuenta. %s" % e)
        print("No se pudo comprobar ahora: %s Se guarda igual; probar luego con: sudo correo probar" % e)
    correo_gmail.guardar_cuenta(usuario, clave)
    try:
        basedatos.guardar_config(_con(), correo_cuenta=usuario)
    except ErrorBase as e:
        print("Aviso: %s" % e)
    print("Cuenta guardada: %s. La contraseña queda solo en esta Raspberry (%s)." % (usuario, correo_gmail.RUTA))
    return 0


def _correo_probar(argumentos):
    if os.geteuid() != 0:
        return _salir("Usar con sudo:  sudo correo probar")
    try:
        con = _con()
        cfg = basedatos.config(con)
        cuenta = correo_gmail.leer_cuenta()
        if argumentos:
            para, copia = correo_gmail.direcciones(" ".join(argumentos)), []
        else:
            para = correo_gmail.direcciones(cfg.get("correo_para"))
            copia = correo_gmail.direcciones(cfg.get("correo_copia"))
        if not para:
            return _salir("No hay destinatarios. Indicarlos con:  correo para nombre@empresa.com")
    except (ErrorBase, correo_gmail.ErrorCorreo, ValueError) as e:
        return _salir(str(e))
    momento = ahora()
    muestra, archivo = [], None
    print("Leyendo el controlador para adjuntar una muestra del archivo...")
    try:
        lectura = leer_pesadas(cfg.get("ip"), int(cfg.get("puerto")))
        muestra = [p._asdict() for p in sorted(lectura.pesadas, key=lambda p: (p.FechaI, p.ID))[-5:]]
        nota = "El controlador no tiene pesadas, por eso no va el archivo de muestra."
    except (ErrorControlador, ValueError, OSError) as e:
        nota = "No se pudo leer el controlador (%s), por eso no va el archivo de muestra." % e
    if muestra:
        archivo = "pesadas_prueba_%s.csv" % momento.strftime("%Y%m%d_%H%M")
        nota = ("Archivo adjunto de muestra: las últimas %d pesadas del controlador (no se guardan ni cuentan "
                "como enviadas)." % len(muestra))
    cuerpo = ("Correo de prueba del registro de pesadas del Rice Lake 1280 (Raspberry %s), enviado el %s.\n\n"
              "Los envíos automáticos llegan con el asunto, por ejemplo:\n"
              "Pesadas 19/10/2026 09:10 - 09:15 - 12 pesadas\n"
              "y un archivo CSV solo con las pesadas nuevas de ese tramo.\n\n%s\n"
              % (socket.gethostname(), formato.fecha(momento), nota))
    mensaje = correo_gmail.armar_mensaje(cuenta, para, copia, "Prueba - Registro de pesadas 1280", cuerpo,
                                         correo_gmail.armar_csv(muestra) if muestra else None, archivo)
    print("Enviando a: %s%s ..." % (", ".join(para), " (copia: %s)" % ", ".join(copia) if copia else ""))
    error, no_aceptados = None, ""
    try:
        no_aceptados = correo_gmail.enviar(cuenta, mensaje) or ""
    except correo_gmail.ErrorCorreo as e:
        error = str(e)
    try:
        basedatos.registrar_correo(con, enviado_en=ahora(), tipo="prueba", pesadas=len(muestra),
                                   resultado="ERROR" if error else "OK", para=", ".join(para),
                                   copia=", ".join(copia), archivo=archivo, detalle=error or no_aceptados)
    except ErrorBase:
        pass
    if error:
        return _salir("No se pudo enviar: %s" % error)
    print("Correo de prueba enviado.%s" % (" Lleva de muestra %d pesadas del controlador." % len(muestra)
                                          if muestra else ""))
    if no_aceptados:
        print(no_aceptados)
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
    print("Se van a borrar las %s pesadas guardadas, el historial de descargas y de correos, y las filas "
          "rechazadas." % formato.numero(n))
    print("La configuración (IP del controlador, bucle, destinatarios y cuenta de correo) se conserva. "
          "El envío por correo queda detenido. Esto no se puede deshacer.")
    if input('Para confirmar escribir BORRAR: ').strip() != "BORRAR":
        print("No se borró nada.")
        return 1
    basedatos.guardar_config(con, correo_activo="0", correo_desde="", correo_proximo="")
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
