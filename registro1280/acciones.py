"""Acciones que cambian la configuración o hacen algo en el momento.

Las usan los comandos de la terminal y el servicio del correo cuando atiende los pedidos
de la web, así las dos formas hacen exactamente lo mismo. Cada acción devuelve el texto
del resultado o lanza ErrorAccion con la explicación.
"""

import ipaddress
import json
import math
import socket
from datetime import datetime, timedelta

from . import basedatos, correo, formato
from .basedatos import ErrorBase
from .controlador import ErrorControlador, consultar, leer_pesadas
from .guardado import guardar as guardar_ahora
from .horario import leer_fecha, minutos_config, siguiente_envio, texto_fecha
from .reloj import ahora

LATIDO_MAXIMO = 200   # segundos sin latido para decir que un servicio no responde
MAXIMO_DIRECCIONES = 5000   # caracteres de cada lista (para o copia)


class ErrorAccion(Exception):
    """La acción no se pudo hacer; el texto explica por qué."""


def estado_servicio(cfg, clave="latido", nombre="del bucle", reloj=ahora):
    latido = cfg.get(clave) or ""
    try:
        edad = (reloj() - datetime.strptime(latido, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return False, "el servicio %s no ha dado señales" % nombre
    if edad > LATIDO_MAXIMO:
        return False, "el servicio %s no responde (última señal hace %s)" % (nombre, formato.hace(edad))
    return True, "funcionando"


def _minutos(valor, ejemplo):
    try:
        minutos = int(str(valor).strip())
        if minutos < 1:
            raise ValueError
    except ValueError:
        raise ErrorAccion("Los minutos deben ser un número entero mayor que 0 (por ejemplo %s)." % ejemplo)
    return minutos


def _plural(n):
    return "" if n == 1 else "s"


# ------------------------------------------------------------------ bucle
def bucle_activar(con, minutos, reloj=ahora):
    minutos = _minutos(minutos, "bucle 5")
    basedatos.guardar_config(con, bucle_activo="1", bucle_minutos=str(minutos), bucle_cambiado=texto_fecha(reloj()))
    lineas = ["Bucle activado: guarda cada %d minuto%s, empezando ahora. Sigue funcionando aunque se "
              "cierre la ventana o se reinicie la Pi." % (minutos, _plural(minutos))]
    ok, texto = estado_servicio(basedatos.config(con), reloj=reloj)
    if not ok:
        lineas.append("ATENCIÓN: %s. Revisar: sudo systemctl status registro1280-bucle" % texto)
    return "\n".join(lineas)


def bucle_detener(con, reloj=ahora):
    basedatos.guardar_config(con, bucle_activo="0", bucle_cambiado=texto_fecha(reloj()))
    return "Bucle detenido. Ya no se guarda automáticamente. Para volver a iniciarlo: bucle 5"


# ------------------------------------------------------------------ controlador
def controlador_cambiar(con, ip, puerto=10001):
    try:
        ip = str(ipaddress.IPv4Address(str(ip).strip()))
        puerto = int(str(puerto).strip() or 10001)
        if not 1 <= puerto <= 65535:
            raise ValueError
    except ValueError:
        raise ErrorAccion("IP o puerto no válidos. Ejemplo: 10.30.211.141 y 10001")
    basedatos.guardar_config(con, ip=ip, puerto=str(puerto))
    return "Controlador guardado: %s puerto %d" % (ip, puerto)


def controlador_probar(con):
    cfg = basedatos.config(con)
    try:
        respuesta, segundos = consultar(cfg.get("ip"), int(cfg.get("puerto")), "VERSION")
    except (ErrorControlador, ValueError) as e:
        raise ErrorAccion("Controlador %s:%s - %s" % (cfg.get("ip"), cfg.get("puerto"), e))
    return "El controlador %s:%s respondió: %s (%.1f s)" % (cfg.get("ip"), cfg.get("puerto"),
                                                            " ".join(respuesta.split()), segundos)


# ------------------------------------------------------------------ guardar
def guardar(con, origen="manual", leer=None, reloj=ahora, dormir=None):
    leer = leer or leer_pesadas
    extra = {"dormir": dormir} if dormir else {}
    try:
        r = guardar_ahora(con, origen, reintentos=1, leer=leer, reloj=reloj, **extra)
    except (ErrorBase, ErrorControlador) as e:
        raise ErrorAccion("No se pudo guardar: %s" % e)
    lineas = ["Descarga completa: %s pesadas en %.1f s" % (formato.numero(r["leidas"]), r["segundos"]),
              "Nuevas: %s | Actualizadas: %s | Ya estaban: %s"
              % (formato.numero(r["nuevas"]), formato.numero(r["actualizadas"]), formato.numero(r["sin_cambio"]))]
    if r["rechazadas"]:
        lineas.append("Filas con datos inválidos: %s (no se guardaron como pesadas; están en la tabla rechazadas)"
                      % formato.numero(r["rechazadas"]))
    lineas.append("Total en la base: %s pesadas" % formato.numero(r["total"]))
    return "\n".join(lineas)


# ------------------------------------------------------------------ correo
def correo_detener(con, reloj=ahora):
    if basedatos.config(con).get("correo_activo") != "1":
        return "El envío por correo ya estaba detenido. Para activarlo: correo 5"
    basedatos.guardar_config(con, correo_activo="0", correo_detenido=texto_fecha(reloj()))
    return ("Envío por correo detenido. Para volver a activarlo: correo 5\n"
            "(Al activarlo de nuevo, lo que se guarde mientras esté detenido no se envía.)")


def aviso_limite_gmail(cfg, minutos):
    """Una cuenta de Gmail gratuita acepta alrededor de 500 envíos al día (cada destinatario cuenta)."""
    try:
        destinatarios = len(correo.direcciones(cfg.get("correo_para"))) + \
            len(correo.direcciones(cfg.get("correo_copia")))
    except ValueError:
        return ""
    correos = math.ceil(24 * 60 / minutos)
    if correos * max(1, destinatarios) <= 450:
        return ""
    return ("Aviso: así son %s correos al día (%s envíos contando para y copia). Una cuenta de Gmail "
            "gratuita acepta alrededor de 500 al día; si se pasa, Gmail deja de aceptar envíos por unas "
            "horas. Las pesadas no se pierden: van en el primer correo que Gmail vuelva a aceptar."
            % (formato.numero(correos), formato.numero(correos * max(1, destinatarios))))


def correo_activar(con, minutos, reloj=ahora, leer=None, dormir=None):
    """Activa el envío cada N minutos, o cambia el intervalo si ya está activo."""
    leer = leer or leer_pesadas
    minutos = _minutos(minutos, "correo 5")
    cfg = basedatos.config(con)
    if not correo.hay_cuenta(cfg):
        raise ErrorAccion("Primero configurar la cuenta de Gmail que envía:  sudo correo cuenta")
    if not (cfg.get("correo_para") or "").strip():
        raise ErrorAccion("Primero indicar a quién se envía:  correo para nombre@empresa.com")
    plural = _plural(minutos)
    if cfg.get("correo_activo") == "1":
        anterior = minutos_config(cfg)
        proximo = leer_fecha(cfg.get("correo_proximo"))
        basedatos.guardar_config(con, correo_minutos=str(minutos))
        if minutos == anterior:
            return "El correo ya está activo cada %d minuto%s. Próximo envío: %s." % (minutos, plural, _hora(proximo))
        despues = proximo + timedelta(minutes=minutos) if proximo else None
        lineas = ["Intervalo cambiado a %d minuto%s. El próximo correo sale a las %s, como estaba programado; "
                  "desde ahí se cuentan los %d minuto%s: el siguiente a las %s."
                  % (minutos, plural, _hora(proximo), minutos, plural, _hora(despues))]
        aviso = aviso_limite_gmail(cfg, minutos)
        return "\n".join(lineas + ([aviso] if aviso else []))
    extra = {"dormir": dormir} if dormir else {}
    try:
        guardar_ahora(con, "manual", reintentos=1, reloj=reloj, leer=leer, **extra)
    except (ErrorControlador, ErrorBase) as e:
        raise ErrorAccion("No se activó el correo: no se pudo leer el controlador (%s).\n"
                          "Revisar con: controlador probar  y volver a intentar." % e)
    # Lo guardado antes de activar (o mientras estuvo detenido) no se envía. Lo que había quedado
    # pendiente mientras estaba activo (por ejemplo, un envío que falló) sí se envía.
    anteriores = basedatos.marcar_sin_enviar(con, leer_fecha(cfg.get("correo_detenido")))
    pendientes = basedatos.contar_pendientes(con)
    momento = reloj()
    proximo = siguiente_envio(momento, minutos)
    basedatos.guardar_config(con, correo_minutos=str(minutos), correo_desde=texto_fecha(momento),
                             correo_proximo=texto_fecha(proximo), correo_detenido="", correo_activo="1")
    lineas = ["Correo activado: cada %d minuto%s. Primer envío a las %s con las pesadas desde las %s."
              % (minutos, plural, _hora(proximo), _hora(momento)),
              "Cada correo lleva solo las pesadas nuevas de su tramo; ninguna se envía dos veces.",
              "Lo que ya estaba guardado (%s pesadas) no se envía." % formato.numero(anteriores)]
    if pendientes:
        lineas.append("Además van en el primer correo %s pesadas que habían quedado sin enviar antes de "
                      "detenerlo." % formato.numero(pendientes))
    aviso = aviso_limite_gmail(cfg, minutos)
    if aviso:
        lineas.append(aviso)
    ok, texto = estado_servicio(basedatos.config(con), "correo_latido", "del correo", reloj=reloj)
    if not ok:
        lineas.append("ATENCIÓN: %s. Revisar: sudo systemctl status registro1280-correo" % texto)
    return "\n".join(lineas)


def _hora(momento):
    return momento.strftime("%H:%M") if momento else ""


def correo_direcciones(con, cual, texto):
    """cual = "para" o "copia". En copia, texto vacío o "ninguna" quita la copia."""
    clave = "correo_para" if cual == "para" else "correo_copia"
    nombre = "Para" if cual == "para" else "Copia"
    if cual == "copia" and (texto or "").strip().lower() in ("", "ninguna", "ninguno", "no", "-"):
        basedatos.guardar_config(con, correo_copia="")
        return "Sin copia."
    try:
        lista = correo.direcciones(texto)
    except ValueError as e:
        raise ErrorAccion(str(e))
    if not lista:
        raise ErrorAccion("Indicar al menos una dirección de correo.")
    if len(", ".join(lista)) > MAXIMO_DIRECCIONES:
        raise ErrorAccion("Demasiadas direcciones (máximo %d caracteres en total)." % MAXIMO_DIRECCIONES)
    basedatos.guardar_config(con, **{clave: ", ".join(lista)})
    return "%s: %s" % (nombre, ", ".join(lista))


def correo_destinatarios(con, para, copia):
    """Para y copia juntos (listas de direcciones, una por casilla en la web)."""
    try:
        lista_para = correo.direcciones(" ".join(para))
        lista_copia = correo.direcciones(" ".join(copia))
    except ValueError as e:
        raise ErrorAccion(str(e))
    if not lista_para:
        raise ErrorAccion("Falta al menos un destinatario en Para.")
    for lista in (lista_para, lista_copia):
        if len(", ".join(lista)) > MAXIMO_DIRECCIONES:
            raise ErrorAccion("Demasiadas direcciones (máximo %d caracteres en total)." % MAXIMO_DIRECCIONES)
    basedatos.guardar_config(con, correo_para=", ".join(lista_para), correo_copia=", ".join(lista_copia))
    return "Para (%d): %s\nCopia (%d): %s" % (len(lista_para), ", ".join(lista_para), len(lista_copia),
                                               ", ".join(lista_copia) or "sin copia")


def correo_cuenta(con, usuario, clave, probar_cuenta=None):
    """Prueba la cuenta en Gmail y la guarda. Si Gmail la rechaza, no se guarda nada."""
    probar_cuenta = probar_cuenta or correo.probar_cuenta
    try:
        lista = correo.direcciones(usuario)
        if len(lista) != 1:
            raise ValueError("Indicar una sola dirección de Gmail.")
    except ValueError as e:
        raise ErrorAccion(str(e))
    usuario = lista[0]
    clave = "".join((clave or "").split())
    if not clave:
        raise ErrorAccion("Falta la contraseña de aplicación. No se cambió nada.")
    lineas = []
    if len(clave) != 16:
        lineas.append("Atención: la contraseña de aplicación de Gmail tiene 16 letras y esta tiene %d." % len(clave))
    cuenta = {"usuario": usuario, "clave": clave, "servidor": correo.SERVIDOR, "puerto": correo.PUERTO,
              "seguridad": "starttls", "nombre": correo.NOMBRE}
    try:
        probar_cuenta(cuenta)
        lineas.append("Gmail aceptó la cuenta.")
    except correo.ErrorCorreo as e:
        if not e.reintentar:
            raise ErrorAccion("\n".join(lineas + ["No se guardó la cuenta. %s" % e]))
        lineas.append("No se pudo comprobar ahora: %s Se guarda igual; probar luego con: sudo correo probar" % e)
    try:
        correo.guardar_cuenta(usuario, clave)
    except OSError as e:
        raise ErrorAccion("No se pudo guardar la cuenta (%s). Usar sudo." % e)
    # Copia para mostrarla en la página Configuración (la pidió el cliente); se envía con el archivo.
    basedatos.guardar_config(con, correo_cuenta=usuario, correo_clave=clave)
    lineas.append("Cuenta guardada: %s." % usuario)
    return "\n".join(lineas)


def correo_probar(con, destino="", leer=None, enviar=None, leer_cuenta=None, reloj=ahora):
    """Envía ahora un correo de prueba con las últimas 5 pesadas del controlador (no guarda nada)."""
    leer, enviar = leer or leer_pesadas, enviar or correo.enviar
    leer_cuenta = leer_cuenta or correo.leer_cuenta
    cfg = basedatos.config(con)
    try:
        cuenta = leer_cuenta()
        if (destino or "").strip():
            para, copia = correo.direcciones(destino), []
        else:
            para = correo.direcciones(cfg.get("correo_para"))
            copia = correo.direcciones(cfg.get("correo_copia"))
    except (correo.ErrorCorreo, ValueError) as e:
        raise ErrorAccion(str(e))
    if not para:
        raise ErrorAccion("No hay destinatarios. Indicarlos con:  correo para nombre@empresa.com")
    momento = reloj()
    muestra, archivo = [], None
    try:
        lectura = leer(cfg.get("ip"), int(cfg.get("puerto")))
        muestra = [p._asdict() for p in sorted(lectura.pesadas, key=lambda p: (p.FechaI, p.ID))[-5:]]
        nota = "El controlador no tiene pesadas, por eso no va el archivo de muestra."
        resumen = "sin archivo: el controlador no tiene pesadas"
    except (ErrorControlador, ValueError, OSError) as e:
        nota = "No se pudo leer el controlador (%s), por eso no va el archivo de muestra." % e
        resumen = "sin archivo: no se pudo leer el controlador (%s)" % e
    if muestra:
        archivo = "pesadas_prueba_%s.csv" % momento.strftime("%Y%m%d_%H%M")
        nota = ("Archivo adjunto de muestra: las últimas %d pesadas del controlador (no se guardan ni cuentan "
                "como enviadas)." % len(muestra))
        resumen = "muestra de %d pesadas del controlador" % len(muestra)
    cuerpo = ("Correo de prueba del registro de pesadas del Rice Lake 1280 (Raspberry %s), enviado el %s.\n\n"
              "Los envíos automáticos llegan con el asunto, por ejemplo:\n"
              "Pesadas 19/10/2026 09:10 - 09:15 - 12 pesadas\n"
              "y un archivo CSV solo con las pesadas nuevas de ese tramo.\n\n%s\n"
              % (socket.gethostname(), formato.fecha(momento), nota))
    mensaje = correo.armar_mensaje(cuenta, para, copia, "Prueba - Registro de pesadas 1280", cuerpo,
                                   correo.armar_csv(muestra) if muestra else None, archivo)
    destinos = "a %s%s" % (", ".join(para), "; copia %s" % ", ".join(copia) if copia else "")
    error, no_aceptados = None, ""
    try:
        no_aceptados = enviar(cuenta, mensaje) or ""
    except correo.ErrorCorreo as e:
        error = str(e)
    detalle = error or "Prueba %s; %s. %s" % (destinos, resumen, no_aceptados)
    try:
        # El tramo de una prueba es el de las pesadas de muestra que lleva (de la primera a la última).
        desde = min(p["FechaI"] for p in muestra) if muestra else None
        hasta = max(p["FechaF"] for p in muestra) if muestra else None
        basedatos.registrar_correo(con, enviado_en=reloj(), tipo="prueba", pesadas=len(muestra),
                                   desde=desde, hasta=hasta,
                                   resultado="ERROR" if error else "OK", para=", ".join(para),
                                   copia=", ".join(copia), archivo=archivo, detalle=detalle.strip())
    except ErrorBase:
        pass
    if error:
        raise ErrorAccion("No se pudo enviar: %s" % error)
    lineas = ["Correo de prueba enviado %s.%s" % (destinos, " Lleva de muestra %d pesadas del controlador."
                                                  % len(muestra) if muestra else " " + nota)]
    if no_aceptados:
        lineas.append(no_aceptados)
    return "\n".join(lineas)


# ------------------------------------------------------------------ pedidos de la web
TIPOS = {
    "bucle": "Guardado automático",
    "controlador": "Controlador",
    "controlador_probar": "Probar controlador",
    "guardar": "Guardar ahora",
    "correo": "Envío por correo",
    "correo_para": "Destinatarios (para)",
    "correo_copia": "Copia",
    "correo_destinatarios": "Destinatarios",
    "correo_cuenta": "Cuenta de Gmail",
    "correo_probar": "Correo de prueba",
}


def ejecutar(con, tipo, datos, reloj=ahora, leer=None, enviar=None, leer_cuenta=None, probar_cuenta=None,
             dormir=None):
    """Hace lo que pidió la web (mismo efecto que el comando de la terminal)."""
    datos = (datos or "").strip()
    if tipo == "bucle":
        return bucle_detener(con, reloj) if datos == "detener" else bucle_activar(con, datos, reloj)
    if tipo == "controlador":
        partes = datos.split()
        if not partes:
            raise ErrorAccion("Indicar la IP del controlador.")
        return controlador_cambiar(con, partes[0], partes[1] if len(partes) > 1 else 10001)
    if tipo == "controlador_probar":
        return controlador_probar(con)
    if tipo == "guardar":
        return guardar(con, "web", leer=leer, reloj=reloj, dormir=dormir)
    if tipo == "correo":
        if datos == "detener":
            return correo_detener(con, reloj)
        return correo_activar(con, datos, reloj=reloj, leer=leer, dormir=dormir)
    if tipo == "correo_para":
        return correo_direcciones(con, "para", datos)
    if tipo == "correo_copia":
        return correo_direcciones(con, "copia", datos)
    if tipo == "correo_destinatarios":
        try:
            valores = json.loads(datos)
            para, copia = list(valores.get("para") or []), list(valores.get("copia") or [])
        except (ValueError, AttributeError, TypeError):
            raise ErrorAccion("Datos de los destinatarios no válidos.")
        return correo_destinatarios(con, [str(x) for x in para], [str(x) for x in copia])
    if tipo == "correo_cuenta":
        try:
            valores = json.loads(datos)
        except ValueError:
            raise ErrorAccion("Datos de la cuenta no válidos.")
        return correo_cuenta(con, valores.get("usuario", ""), valores.get("clave", ""), probar_cuenta=probar_cuenta)
    if tipo == "correo_probar":
        return correo_probar(con, datos, leer=leer, enviar=enviar, leer_cuenta=leer_cuenta, reloj=reloj)
    raise ErrorAccion("Pedido desconocido: %s" % tipo)
