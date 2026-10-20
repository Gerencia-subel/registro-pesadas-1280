"""Punto de entrada: python3 -m registro1280 <comando> [argumentos]

Comandos de la terminal: mostrar, guardar, visualizar, bucle, correo, controlador.
Servicios: servicio-bucle, servicio-correo y servicio-web (los arranca systemd).
"""

import logging
import signal
import sys

from . import VERSION


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "ayuda"):
        print("Registro de pesadas 1280 - versión %s\n" % VERSION)
        print('  mostrar "DB.DATA#2"     ver lo que responde el controlador (solo comandos de lectura)')
        print("  guardar                 guardar en la base solo las pesadas nuevas")
        print("  visualizar [N]          ver todas las pesadas guardadas (o las últimas N)")
        print("  bucle 5                 guardar cada 5 minutos, siempre")
        print("  bucle detener           detener el guardado automático")
        print("  bucle                   ver el estado del bucle y las últimas descargas")
        print("  correo 5                enviar por correo cada 5 minutos el CSV con las pesadas nuevas")
        print("  correo detener          detener el envío por correo")
        print("  correo                  ver el estado del correo y los últimos envíos")
        print("  correo para / copia     destinatarios (correo para nombre@empresa.com)")
        print("  sudo correo cuenta      cuenta de Gmail que envía (contraseña de aplicación)")
        print("  sudo correo probar      enviar ahora un correo de prueba")
        print("  comunicacion 5          revisar con ping cada 5 segundos si el controlador responde")
        print("  comunicacion detener    desactivar esa revisión; comunicacion: ver el estado")
        print("  controlador [IP PUERTO] ver o cambiar la IP del controlador; controlador probar")
        print("  sudo cambiar-clave      recuperar el acceso de un administrador de la web")
        print("  sudo vaciar-base        borrar todas las pesadas (después de probar con el simulador)")
        return 0
    comando, argumentos = argv[0], argv[1:]
    if comando == "servicio-bucle":
        return _servicio_bucle()
    if comando == "servicio-correo":
        return _servicio_correo()
    if comando == "servicio-comunicacion":
        return _servicio_comunicacion()
    if comando == "servicio-web":
        from .web import servir
        servir()
        return 0
    if comando == "preparar-web":   # lo usa instalar.sh
        from . import accesos, basedatos, config_web
        creado = config_web.preparar(int(argumentos[0]) if argumentos else 8081)
        con = basedatos.conectar()
        try:
            accesos.preparar_iniciales(con)
        finally:
            con.close()
        print("Configuración de la web %s." % ("creada" if creado else "conservada"))
        return 0
    from .comandos import COMANDOS
    funcion = COMANDOS.get(comando)
    if funcion is None:
        print("Comando desconocido: %s. Ver: python3 -m registro1280 ayuda" % comando, file=sys.stderr)
        return 2
    try:
        return funcion(argumentos)
    except KeyboardInterrupt:
        print("")
        return 130
    except BrokenPipeError:  # por ejemplo: visualizar | head
        try:
            import os
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0


def _servicio_bucle():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    from .bucle import Bucle
    servicio = Bucle()
    signal.signal(signal.SIGTERM, lambda *_: servicio.detener.set())
    signal.signal(signal.SIGINT, lambda *_: servicio.detener.set())
    servicio.correr()
    return 0


def _servicio_correo():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    from .envio import ServicioCorreo
    servicio = ServicioCorreo()
    signal.signal(signal.SIGTERM, lambda *_: servicio.detener.set())
    signal.signal(signal.SIGINT, lambda *_: servicio.detener.set())
    servicio.correr()
    return 0


def _servicio_comunicacion():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    from .comunicacion import ServicioComunicacion
    servicio = ServicioComunicacion()
    signal.signal(signal.SIGTERM, lambda *_: servicio.detener.set())
    signal.signal(signal.SIGINT, lambda *_: servicio.detener.set())
    servicio.correr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
