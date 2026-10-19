"""Correo por Gmail (SMTP) con el CSV de las pesadas.

La cuenta que envía (correo de Gmail y contraseña de aplicación) está en
/etc/registro1280/correo.ini, que solo pueden leer root y el servicio del correo:
no se guarda en la base (la web puede leer la base) ni en GitHub.
"""

import configparser
import csv
import grp
import io
import os
import re
import smtplib
import socket
import ssl
from decimal import Decimal
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

RUTA = os.environ.get("REGISTRO1280_CORREO_INI", "/etc/registro1280/correo.ini")
SERVIDOR = "smtp.gmail.com"
PUERTO = 587
NOMBRE = "Registro de pesadas 1280"
COLUMNAS_CSV = ("ID", "cor", "Producto", "Peso", "Acum", "FechaI", "FechaF", "Turno", "Estado")
_DIRECCION = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")


class ErrorCorreo(Exception):
    """Problema al enviar el correo, explicado en español. reintentar=False si repetir no sirve."""

    def __init__(self, mensaje, reintentar=True):
        super().__init__(mensaje)
        self.reintentar = reintentar


# ------------------------------------------------------------------ direcciones
def direcciones(texto):
    """'a@x.com, b@y.com' o 'a@x.com b@y.com' -> ['a@x.com', 'b@y.com']. ValueError si alguna no sirve."""
    lista = [d.strip() for d in re.split(r"[\s,;]+", texto or "") if d.strip()]
    malas = [d for d in lista if len(d) > 254 or not _DIRECCION.match(d)]
    if malas:
        raise ValueError("Dirección de correo no válida: %s" % ", ".join(malas))
    vistas, unicas = set(), []
    for d in lista:
        if d.lower() not in vistas:
            vistas.add(d.lower())
            unicas.append(d)
    return unicas


# ------------------------------------------------------------------ cuenta
def hay_cuenta(ruta=None):
    return os.path.exists(ruta or RUTA)


def leer_cuenta(ruta=None):
    ruta = ruta or RUTA
    ini = configparser.ConfigParser(interpolation=None)
    try:
        leidos = ini.read(ruta, encoding="utf-8")
    except (PermissionError, configparser.Error) as e:
        raise ErrorCorreo("No se pudo leer la cuenta de correo (%s): %s" % (ruta, e))
    if not leidos or not ini.has_section("correo"):
        if os.path.exists(ruta) and not os.access(ruta, os.R_OK):
            raise ErrorCorreo("Sin permiso para leer la cuenta de correo. Usar sudo.")
        raise ErrorCorreo("Falta la cuenta de Gmail que envía. Configurarla con:  sudo correo cuenta")
    s = ini["correo"]
    cuenta = {"usuario": s.get("usuario", "").strip(), "clave": s.get("clave", "").replace(" ", ""),
              "servidor": s.get("servidor", SERVIDOR).strip(), "puerto": s.getint("puerto", PUERTO),
              "seguridad": s.get("seguridad", "starttls").strip().lower(), "nombre": s.get("nombre", NOMBRE)}
    if not cuenta["usuario"] or not cuenta["clave"]:
        raise ErrorCorreo("La cuenta de correo está incompleta. Configurarla con:  sudo correo cuenta")
    return cuenta


def guardar_cuenta(usuario, clave, ruta=None):
    """Escribe el archivo de la cuenta: dueño root, lo lee solo el grupo registro1280 (el servicio)."""
    ruta = ruta or RUTA
    ini = configparser.ConfigParser(interpolation=None)
    ini["correo"] = {"usuario": usuario, "clave": clave.replace(" ", ""), "servidor": SERVIDOR,
                     "puerto": str(PUERTO), "seguridad": "starttls", "nombre": NOMBRE}
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    temporal = ruta + ".tmp"
    descriptor = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        f.write("# Cuenta de Gmail que envía las pesadas (contraseña de aplicación).\n"
                "# Para cambiarla: sudo correo cuenta\n")
        ini.write(f)
    try:
        os.chown(temporal, 0, grp.getgrnam("registro1280").gr_gid)
        os.chmod(temporal, 0o640)
    except (KeyError, PermissionError):
        os.chmod(temporal, 0o600)
    os.replace(temporal, ruta)


# ------------------------------------------------------------------ CSV
_EN_TEXTO = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def sin_direcciones(texto):
    """Para la web: reemplaza las direcciones de correo de un texto (no se muestran en la web)."""
    return _EN_TEXTO.sub("(dirección)", texto or "")


def numero_csv(valor):
    """Decimal('302.9500') -> '302.95'; Decimal('-81.6000') -> '-81.6'; Decimal('1000.0000') -> '1000'."""
    if valor is None:
        return ""
    d = Decimal(valor)
    texto = format(d.normalize(), "f") if d != 0 else "0"
    return texto


def fecha_csv(momento):
    return momento.strftime("%d/%m/%Y %H:%M") if momento else ""


def armar_csv(filas):
    """Filas (dict con las columnas del controlador) -> bytes del CSV (UTF-8 con BOM para Excel)."""
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\r\n")
    escritor.writerow(COLUMNAS_CSV)
    for f in filas:
        escritor.writerow([f["ID"], f["cor"], f["Producto"], numero_csv(f["Peso"]), numero_csv(f["Acum"]),
                           fecha_csv(f["FechaI"]), fecha_csv(f["FechaF"]), f["Turno"], f["Estado"]])
    return salida.getvalue().encode("utf-8-sig")


# ------------------------------------------------------------------ textos
def rango(desde, hasta):
    """'19/10/2026 09:10 - 09:15' (mismo día) o '19/10/2026 23:55 - 20/10/2026 00:00'."""
    if desde.date() == hasta.date():
        return "%s - %s" % (desde.strftime("%d/%m/%Y %H:%M"), hasta.strftime("%H:%M"))
    return "%s - %s" % (desde.strftime("%d/%m/%Y %H:%M"), hasta.strftime("%d/%m/%Y %H:%M"))


def nombre_archivo(desde, hasta):
    if desde.date() == hasta.date():
        return "pesadas_%s_%s-%s.csv" % (desde.strftime("%Y%m%d"), desde.strftime("%H%M"), hasta.strftime("%H%M"))
    return "pesadas_%s-%s.csv" % (desde.strftime("%Y%m%d_%H%M"), hasta.strftime("%Y%m%d_%H%M"))


def cantidad_texto(n):
    if n == 0:
        return "Sin pesadas"
    return "1 pesada" if n == 1 else "{:,} pesadas".format(n)


def asunto_tramo(desde, hasta, n, sin_controlador=False):
    asunto = "Pesadas %s - %s" % (rango(desde, hasta), cantidad_texto(n))
    if sin_controlador:
        asunto += " - Controlador sin respuesta"
    return asunto


# ------------------------------------------------------------------ mensaje y envío
def armar_mensaje(cuenta, para, copia, asunto, cuerpo, adjunto=None, nombre_adjunto=None):
    m = EmailMessage()
    m["From"] = formataddr((cuenta.get("nombre") or NOMBRE, cuenta["usuario"]))
    m["To"] = ", ".join(para)
    if copia:
        m["Cc"] = ", ".join(copia)
    m["Subject"] = asunto
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=cuenta["usuario"].split("@")[-1] or None)
    m.set_content(cuerpo)
    if adjunto is not None:
        m.add_attachment(adjunto, maintype="text", subtype="csv", filename=nombre_adjunto,
                         params={"charset": "utf-8"})
    return m


def _explicar(e, cuenta):
    servidor = "%s:%s" % (cuenta["servidor"], cuenta["puerto"])
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return ("Gmail rechazó la cuenta %s o la contraseña de aplicación (%s). Revisar con:  sudo correo cuenta"
                % (cuenta["usuario"], _texto_smtp(e)))
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        malas = ", ".join("%s (%s)" % (d, _texto_smtp(r)) for d, r in e.recipients.items())
        return "Gmail no aceptó estos destinatarios: %s" % malas
    if isinstance(e, smtplib.SMTPResponseException):
        texto = _texto_smtp(e)
        if "5.4.5" in texto or "sending limit" in texto.lower():
            return ("Gmail llegó al límite diario de envíos de la cuenta (%s). Las pesadas quedan pendientes "
                    "y salen en el siguiente correo que Gmail acepte." % texto)
        return "Gmail respondió con error: %s" % texto
    if isinstance(e, ssl.SSLError):
        return "Error de seguridad (TLS) con %s: %s. Revisar la fecha y hora de la Raspberry." % (servidor, e)
    if isinstance(e, socket.gaierror):
        return "Sin internet: no se encuentra %s (%s)." % (cuenta["servidor"], e)
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "Sin respuesta de %s (internet lento o bloqueado)." % servidor
    if isinstance(e, OSError):
        return "No se pudo conectar con %s: %s" % (servidor, e)
    if isinstance(e, smtplib.SMTPException):
        return "Error de correo: %s" % e
    return "Error inesperado al enviar: %s" % e


def _vale_reintentar(e):
    if isinstance(e, (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused)):
        return False
    if isinstance(e, smtplib.SMTPResponseException) and 500 <= (e.smtp_code or 0) < 600:
        return False
    return True


def _texto_smtp(e):
    codigo = getattr(e, "smtp_code", None)
    mensaje = getattr(e, "smtp_error", b"")
    if isinstance(e, tuple):
        codigo, mensaje = e[0], e[1]
    if isinstance(mensaje, bytes):
        mensaje = mensaje.decode("utf-8", "replace")
    mensaje = " ".join(str(mensaje).split())[:200]
    return ("%s %s" % (codigo, mensaje)).strip() if codigo else mensaje


def _abrir(cuenta, smtp, smtp_ssl, espera):
    contexto = ssl.create_default_context()
    if cuenta["seguridad"] == "ssl":
        servidor = smtp_ssl(cuenta["servidor"], cuenta["puerto"], timeout=espera, context=contexto)
    else:
        servidor = smtp(cuenta["servidor"], cuenta["puerto"], timeout=espera)
        servidor.ehlo()
        if cuenta["seguridad"] == "starttls":
            servidor.starttls(context=contexto)
            servidor.ehlo()
    return servidor


def enviar(cuenta, mensaje, smtp=smtplib.SMTP, smtp_ssl=smtplib.SMTP_SSL, espera=30):
    """Envía el mensaje. Lanza ErrorCorreo con la explicación si falla.

    Devuelve un texto con los destinatarios que no se aceptaron (si a los demás sí les llegó).
    """
    servidor = None
    datos_enviados = []
    try:
        servidor = _abrir(cuenta, smtp, smtp_ssl, espera)
        servidor.login(cuenta["usuario"], cuenta["clave"])
        # Si la falla ocurre después de mandar el contenido (por ejemplo, no llega la respuesta
        # de Gmail), no se sabe si el correo llegó: no se reintenta en el mismo tramo, para no
        # mandarlo dos veces. Sus pesadas quedan pendientes y van en el siguiente correo.
        data_original = servidor.data

        def data(contenido):
            datos_enviados.append(True)
            return data_original(contenido)
        servidor.data = data
        rechazados = servidor.send_message(mensaje) or {}
        if rechazados:
            return "Gmail no aceptó: " + ", ".join("%s (%s)" % (d, _texto_smtp(r)) for d, r in rechazados.items())
        return ""
    except Exception as e:
        raise ErrorCorreo(_explicar(e, cuenta), reintentar=_vale_reintentar(e) and not datos_enviados)
    finally:
        if servidor is not None:
            try:
                servidor.quit()
            except Exception:
                try:
                    servidor.close()
                except Exception:
                    pass


def probar_cuenta(cuenta, smtp=smtplib.SMTP, smtp_ssl=smtplib.SMTP_SSL, espera=20):
    """Solo entra a Gmail con la cuenta (no envía nada). Lanza ErrorCorreo si falla."""
    servidor = None
    try:
        servidor = _abrir(cuenta, smtp, smtp_ssl, espera)
        servidor.login(cuenta["usuario"], cuenta["clave"])
    except Exception as e:
        raise ErrorCorreo(_explicar(e, cuenta), reintentar=_vale_reintentar(e))
    finally:
        if servidor is not None:
            try:
                servidor.quit()
            except Exception:
                pass
