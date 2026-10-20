"""Servicio del correo: cada N minutos envía por Gmail un XLSX solo con las pesadas nuevas.

Corre siempre (systemd). El comando "correo" solo cambia la configuración en la base:
  correo_activo   1 = enviar, 0 = detenido
  correo_minutos  cada cuántos minutos (los envíos caen en horas redondas: 09:10, 09:15...)
  correo_desde    inicio del tramo que falta enviar (fin del último correo que salió bien)
  correo_proximo  hora del próximo envío
En cada envío: primero descarga el controlador (para que entre lo último), toma las
pesadas que todavía no salieron en ningún correo, arma el XLSX y lo envía. Solo si Gmail
lo acepta, esas pesadas quedan marcadas con el número del correo: así ninguna se repite,
y si un envío falla, sus pesadas salen en el siguiente correo junto con las nuevas.
"""

import logging
import os
import socket
import threading
import time
import traceback
from datetime import timedelta

import MySQLdb

from . import acciones, basedatos, comunicacion, correo, excel
from .basedatos import ErrorBase
from .horario import leer_fecha, minutos_config, siguiente_envio, texto_fecha  # noqa: F401
from .controlador import ErrorControlador, leer_pesadas
from .guardado import guardar
from .reloj import ahora

registro = logging.getLogger("registro1280")

LATIDO_CADA = 30
PEDIDO_VENCE = 600   # segundos: un pedido de la web que espera más que esto ya no se atiende


def reloj_sincronizado():
    """La Raspberry no tiene reloj propio: al encender, la hora es la del último apagado hasta que
    se sincroniza por internet. En los primeros 10 minutos se espera esa sincronización para no
    poner horas equivocadas en el tramo; después se sigue igual."""
    try:
        with open("/proc/uptime") as f:
            encendida = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return True
    if encendida > 600 or not os.path.isdir("/run/systemd/timesync"):
        return True
    return os.path.exists("/run/systemd/timesync/synchronized")


def cuerpo_tramo(desde, hasta, n, archivo, aviso="", estado_comunicacion=""):
    lineas = []
    if n:
        lineas.append("%s del %s: %s." % (correo.EQUIPO, correo.rango(desde, hasta), correo.cantidad_texto(n)))
        lineas.append("Archivo adjunto: %s (XLSX; tabla %s; columnas %s)."
                      % (archivo, excel.NOMBRE_TABLA, ", ".join(excel.TITULOS)))
    else:
        lineas.append("%s del %s: sin pesadas." % (correo.EQUIPO, correo.rango(desde, hasta)))
    if estado_comunicacion:
        lineas.append("Estado de comunicación: %s" % estado_comunicacion)
    if aviso:
        lineas += ["", aviso]
    lineas += ["", "Correo automático del registro de %s del Rice Lake 1280 (Raspberry %s)."
               % (correo.EQUIPO, socket.gethostname())]
    return "\n".join(lineas) + "\n"


class ServicioCorreo:
    def __init__(self, conectar=basedatos.conectar, leer=leer_pesadas, reloj=ahora, enviar=correo.enviar,
                 leer_cuenta=correo.leer_cuenta, espera_reintento=30, dormir=None,
                 reloj_listo=reloj_sincronizado, probar_cuenta=correo.probar_cuenta):
        self.conectar = conectar
        self.leer = leer
        self.reloj = reloj
        self.enviar = enviar
        self.leer_cuenta = leer_cuenta
        self.probar_cuenta = probar_cuenta
        self.reloj_listo = reloj_listo
        self.espera_reintento = espera_reintento
        self.detener = threading.Event()
        self.dormir = dormir or self.detener.wait
        self._con = None
        self._ultimo_latido = 0.0
        self._activo = None
        self._por_anotar = None   # correo ya enviado (o fallido) que falta anotar en la base
        self._clave_revisada = False

    # ------------------------------------------------------------------
    def correr(self):
        registro.info("Servicio de correo iniciado")
        while not self.detener.is_set():
            try:
                espera = self.vuelta()
            except (ErrorBase, MySQLdb.Error) as e:
                registro.error("Base de datos: %s", e)
                self._cerrar()
                espera = 10
            except Exception:
                registro.error("Error inesperado: %s", traceback.format_exc())
                self._cerrar()
                espera = 10
            self.detener.wait(max(0.2, min(espera, 2.0)))
        self._cerrar()
        registro.info("Servicio de correo detenido")

    def _cerrar(self):
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
        self._con = None

    def vuelta(self):
        """Una revisión. Devuelve cuántos segundos esperar hasta la siguiente."""
        if self._con is None:
            self._con = self.conectar()
        con = self._con
        if time.monotonic() - self._ultimo_latido >= LATIDO_CADA:
            basedatos.guardar_config(con, correo_latido=texto_fecha(self.reloj()))
            self._ultimo_latido = time.monotonic()
        if self._por_anotar is not None:
            # Un correo ya salió pero no se pudo anotar en la base (se cayó la conexión, por
            # ejemplo): se reintenta solo la anotación. Ese tramo nunca se vuelve a enviar.
            self._anotar(con)
            return 0.5
        if not self._clave_revisada:
            self._copiar_clave(con)
        self.atender_pedidos(con)
        cfg = basedatos.config(con)
        activo = cfg.get("correo_activo") == "1"
        minutos = minutos_config(cfg)
        if activo != self._activo:
            self._activo = activo
            registro.info("Correo %s", "activo: cada %d min" % minutos if activo else "detenido")
        if not activo:
            return 2
        momento = self.reloj()
        proximo = leer_fecha(cfg.get("correo_proximo"))
        if proximo is None:
            basedatos.guardar_config(con, correo_proximo=texto_fecha(siguiente_envio(momento, minutos)))
            return 0.5
        if momento < proximo:
            return (proximo - momento).total_seconds()
        if not self.reloj_listo():
            return 5
        if not basedatos.tomar_candado_correo(con):
            return 5
        try:
            resultado = self.enviar_tramo(con, cfg, proximo, momento)
        finally:
            basedatos.soltar_candado_correo(con)
        resultado.update(cfg=cfg, proximo=proximo)
        self._por_anotar = resultado
        self._anotar(con)
        return 0.5

    def _anotar(self, con):
        """Anota el correo, marca sus pesadas y programa el siguiente, todo en una sola operación."""
        r = self._por_anotar
        cfg, actual = r["cfg"], basedatos.config(con)
        cambios = {}
        # Si mientras se enviaba alguien detuvo o volvió a activar el correo, se respeta lo nuevo.
        if actual.get("correo_activo") == "1" and actual.get("correo_desde") == cfg.get("correo_desde") \
                and actual.get("correo_proximo") == cfg.get("correo_proximo"):
            cambios["correo_proximo"] = texto_fecha(siguiente_envio(self.reloj(), minutos_config(actual),
                                                                    base=r["proximo"]))
            if r["ok"]:
                cambios["correo_desde"] = texto_fecha(r["hasta"])
        basedatos.registrar_correo(con, nros_enviados=r["enviadas"] or (), config_nueva=cambios,
                                   enviado_en=r["enviado_en"], resultado="OK" if r["ok"] else "ERROR",
                                   detalle=r["detalle"] or "", **r["datos"])
        self._por_anotar = None

    def _copiar_clave(self, con):
        """Cuentas guardadas antes de la versión 1.3.1: copia la contraseña para verla en la web."""
        self._clave_revisada = True
        cfg = basedatos.config(con)
        if cfg.get("correo_cuenta") and not cfg.get("correo_clave"):
            try:
                basedatos.guardar_config(con, correo_clave=self.leer_cuenta()["clave"])
            except correo.ErrorCorreo:
                pass

    # ------------------------------------------------------------------
    def atender_pedidos(self, con):
        """Hace lo que se pidió desde la web (cambios de configuración, guardar ahora, probar)."""
        for p in basedatos.pedidos_pendientes(con):
            if p["tipo"] == "correo_cuenta":
                basedatos.borrar_datos_pedido(con, p["nro"])   # la contraseña no queda en la base
            if (self.reloj() - p["creado"]).total_seconds() > PEDIDO_VENCE:
                basedatos.terminar_pedido(con, p["nro"], "ERROR", "No se atendió a tiempo (el servicio del correo "
                                          "no estaba funcionando). Volver a intentar.", self.reloj())
                continue
            try:
                texto = acciones.ejecutar(con, p["tipo"], p["datos"], reloj=self.reloj, leer=self.leer,
                                          enviar=self.enviar, leer_cuenta=self.leer_cuenta,
                                          probar_cuenta=self.probar_cuenta, dormir=self.dormir)
                estado = "OK"
            except acciones.ErrorAccion as e:
                texto, estado = str(e), "ERROR"
            except (ErrorBase, MySQLdb.Error) as e:
                texto, estado = "Error de la base de datos: %s" % e, "ERROR"
            except Exception as e:
                registro.error("Pedido %s: %s", p["tipo"], traceback.format_exc())
                texto, estado = "Error inesperado: %s" % e, "ERROR"
            registro.info("Pedido de la web %s: %s", p["tipo"], estado)
            basedatos.terminar_pedido(con, p["nro"], estado, texto, self.reloj())

    # ------------------------------------------------------------------
    def enviar_tramo(self, con, cfg, proximo, momento):
        """Descarga, arma el XLSX de lo pendiente y lo envía. Devuelve lo que hay que anotar."""
        minutos = minutos_config(cfg)
        # Normalmente el tramo termina en la hora programada. Si la Pi estuvo apagada o sin
        # servicio, termina ahora (cubre todo lo que faltaba).
        hasta = proximo if (momento - proximo).total_seconds() < 60 else momento.replace(second=0)
        desde = leer_fecha(cfg.get("correo_desde")) or hasta - timedelta(minutes=minutos)
        desde = min(desde, hasta)

        aviso, sin_controlador = "", False
        try:
            guardar(con, "correo", reintentos=1, leer=self.leer, reloj=self.reloj, dormir=self.dormir)
        except ErrorControlador:
            sin_controlador = True
            aviso = ("Aviso: a las %s no se pudo leer el controlador %s. Las pesadas que falten se "
                     "enviarán en el siguiente correo." % (self.reloj().strftime("%H:%M"), cfg.get("ip", "")))
        except ErrorBase as e:
            registro.warning("No se pudo guardar lo último del controlador: %s", e)
            aviso = ("Aviso: no se pudo guardar lo último del controlador %s. Las pesadas que falten se "
                     "enviarán en el siguiente correo." % cfg.get("ip", ""))

        filas = basedatos.pendientes_correo(con)
        archivo = correo.nombre_archivo(desde, hasta) if filas else None
        datos = dict(tipo="tramo", desde=desde, hasta=hasta, pesadas=len(filas), archivo=archivo,
                     para=cfg.get("correo_para", ""), copia=cfg.get("correo_copia", ""))
        try:
            para = correo.direcciones(cfg.get("correo_para"))
            copia = correo.direcciones(cfg.get("correo_copia"))
            if not para:
                raise correo.ErrorCorreo("No hay destinatarios. Indicarlos con:  correo para nombre@empresa.com")
            cuenta = self.leer_cuenta()
        except (ValueError, correo.ErrorCorreo) as e:
            return self._resultado(con, datos, hasta, None, str(e))

        asunto = correo.asunto_tramo(desde, hasta, len(filas), sin_controlador)
        cuerpo = cuerpo_tramo(desde, hasta, len(filas), archivo, aviso,
                              comunicacion.texto_estado(comunicacion.leer_estado(cfg, self.reloj)))
        mensaje = correo.armar_mensaje(cuenta, para, copia, asunto, cuerpo,
                                       correo.armar_excel(filas) if filas else None, archivo)
        error, no_aceptados = None, ""
        for intento in range(2):
            try:
                no_aceptados = self.enviar(cuenta, mensaje) or ""
                error = None
                break
            except correo.ErrorCorreo as e:
                error = str(e)
                registro.warning("No se pudo enviar el correo (%s): %s", asunto, e)
                if intento == 0 and e.reintentar and not self.detener.is_set():
                    self.dormir(self.espera_reintento)
                else:
                    break
        if error is None:
            registro.info("Correo enviado: %s", asunto)
            if no_aceptados:
                registro.warning("%s", no_aceptados)
            detalle = correo.detalle_enviado(", ".join(para), ", ".join(copia))
            return self._resultado(con, datos, hasta, [f["nro"] for f in filas], detalle)
        return self._resultado(con, datos, hasta, None, error)

    def _resultado(self, con, datos, hasta, enviadas, detalle):
        return {"datos": datos, "hasta": hasta, "ok": enviadas is not None, "enviadas": enviadas,
                "detalle": detalle, "enviado_en": self.reloj()}
