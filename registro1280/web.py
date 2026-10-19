"""Página web de consulta (solo lectura) del registro de pesadas.

Corre como el usuario de Linux registro1280web, que en la base solo tiene permiso
de lectura: aunque alguien lo intentara, desde la web no se puede editar ni borrar.
"""

import hmac
import os
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import VERSION, basedatos, correo, formato
from .basedatos import ErrorBase
from .reloj import ahora

INTENTOS_MAXIMOS = 5
BLOQUEO_SEGUNDOS = 300
LATIDO_MAXIMO = 200
TAMANOS = (50, 100, 500, 1000)


class Intentos:
    """Bloquea el ingreso por 5 minutos después de 5 claves incorrectas seguidas."""

    def __init__(self):
        self._datos = {}
        self._candado = threading.Lock()

    def bloqueado(self, origen):
        with self._candado:
            fallos, hasta = self._datos.get(origen, (0, 0.0))
            return hasta > time.monotonic()

    def fallo(self, origen):
        with self._candado:
            fallos, hasta = self._datos.get(origen, (0, 0.0))
            if hasta and hasta <= time.monotonic():
                fallos = 0
            fallos += 1
            hasta = time.monotonic() + BLOQUEO_SEGUNDOS if fallos >= INTENTOS_MAXIMOS else 0.0
            self._datos[origen] = (fallos, hasta)

    def exito(self, origen):
        with self._candado:
            self._datos.pop(origen, None)


def _fecha_param(nombre):
    valor = (request.args.get(nombre) or "").strip()
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Fecha no válida: %s" % valor)


def _num(valor):
    return None if valor is None else float(valor)


def _vivo(latido):
    try:
        edad = (ahora() - datetime.strptime(latido or "", "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return False
    return edad <= LATIDO_MAXIMO


def _hora_config(texto):
    try:
        return formato.fecha(datetime.strptime(texto or "", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return ""


def _correo_json(c):
    tramo = correo.rango(c["desde"], c["hasta"]) if c["desde"] and c["hasta"] else ""
    return {"nro": c["nro"], "enviado_en": formato.fecha_segundos(c["enviado_en"]), "tipo": c["tipo"],
            "tramo": tramo, "pesadas": c["pesadas"], "resultado": c["resultado"],
            "archivo": c["archivo"] or "", "detalle": c["detalle"] or ""}


def crear_app(ajustes, conectar=basedatos.conectar):
    """ajustes: dict con usuario, clave_hash, secreto (de config_web.leer())."""
    carpeta = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__, template_folder=os.path.join(carpeta, "templates"),
                static_folder=os.path.join(carpeta, "static"))
    app.secret_key = ajustes["secreto"]
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    try:
        app.json.ensure_ascii = False
    except AttributeError:
        pass
    intentos = Intentos()

    def origen_del_pedido():
        # Con Tailscale Funnel los pedidos llegan desde la misma Pi (127.0.0.1); en ese caso
        # se usa la IP real que informa el proxy, para no bloquear a todos por un solo intento.
        remoto = request.remote_addr or "?"
        if remoto in ("127.0.0.1", "::1"):
            reenviado = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if reenviado:
                return "funnel:" + reenviado[:60]
        return remoto

    def autorizado():
        return session.get("usuario") == ajustes["usuario"]

    @app.before_request
    def proteger():
        if request.endpoint in ("ingresar", "static") or autorizado():
            return None
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "La sesión terminó. Volver a ingresar."}), 401
        return redirect(url_for("ingresar"))

    @app.after_request
    def cabeceras(respuesta):
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["X-Frame-Options"] = "DENY"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        if request.endpoint != "static":
            respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @app.route("/ingresar", methods=["GET", "POST"])
    def ingresar():
        error = None
        if request.method == "POST":
            origen = origen_del_pedido()
            usuario = (request.form.get("usuario") or "").strip()
            clave = request.form.get("clave") or ""
            if intentos.bloqueado(origen):
                error = "Demasiados intentos. Esperar 5 minutos y volver a probar."
            elif (hmac.compare_digest(usuario.encode("utf-8"), ajustes["usuario"].encode("utf-8"))
                  and check_password_hash(ajustes["clave_hash"], clave)):
                intentos.exito(origen)
                session.clear()
                session.permanent = True
                session["usuario"] = usuario
                return redirect(url_for("inicio"))
            else:
                intentos.fallo(origen)
                error = ("Demasiados intentos. Esperar 5 minutos y volver a probar."
                         if intentos.bloqueado(origen) else "Usuario o clave incorrectos.")
        return render_template("ingresar.html", error=error, version=VERSION), (401 if error else 200)

    @app.get("/salir")
    def salir():
        session.clear()
        return redirect(url_for("ingresar"))

    @app.get("/")
    def inicio():
        return render_template("inicio.html", usuario=session.get("usuario"), version=VERSION,
                               tamanos=TAMANOS)

    def con_base(funcion):
        try:
            con = conectar()
        except ErrorBase as e:
            return jsonify({"ok": False, "error": str(e)}), 503
        try:
            return funcion(con)
        except ErrorBase as e:
            return jsonify({"ok": False, "error": str(e)}), 503
        finally:
            try:
                con.close()
            except Exception:
                pass

    @app.get("/api/resumen")
    def api_resumen():
        def hacer(con):
            r = basedatos.resumen(con)
            cfg = r["config"]
            d = r["ultima_descarga"]
            c = r["ultimo_correo"]
            servicio_ok = _vivo(cfg.get("latido"))
            return jsonify({
                "ok": True,
                "ahora": formato.fecha(ahora()),
                "total": r["total"],
                "total_texto": formato.numero(r["total"]),
                "primera": formato.fecha(r["primera"]),
                "ultima": formato.fecha(r["ultima"]),
                "rechazadas": r["rechazadas"],
                "controlador": "%s:%s" % (cfg.get("ip", ""), cfg.get("puerto", "")),
                "bucle": {"activo": cfg.get("bucle_activo") == "1", "minutos": cfg.get("bucle_minutos"),
                          "servicio_ok": servicio_ok},
                "ultima_descarga": None if not d else {
                    "inicio": formato.fecha_segundos(d["inicio"]), "origen": d["origen"],
                    "resultado": d["resultado"], "nuevas": d["nuevas"], "leidas": d["leidas"],
                    "segundos": _num(d["segundos"]), "detalle": d["detalle"] or "",
                },
                "correo": {
                    "activo": cfg.get("correo_activo") == "1", "minutos": cfg.get("correo_minutos"),
                    "servicio_ok": _vivo(cfg.get("correo_latido")),
                    "proximo": _hora_config(cfg.get("correo_proximo")),
                    "ultimo": None if not c else _correo_json(c),
                },
            })
        return con_base(hacer)

    @app.get("/api/pesadas")
    def api_pesadas():
        try:
            desde, hasta = _fecha_param("desde"), _fecha_param("hasta")
            producto = (request.args.get("producto") or "").strip()[:60] or None
            id_texto = (request.args.get("id") or "").strip()
            id_ctrl = int(id_texto) if id_texto else None
            pagina = max(1, int(request.args.get("pagina") or 1))
            por_pagina = int(request.args.get("por_pagina") or 100)
            if por_pagina not in TAMANOS:
                por_pagina = 100
        except ValueError as e:
            mensaje = str(e) if str(e).startswith("Fecha") else "El ID debe ser un número."
            return jsonify({"ok": False, "error": mensaje}), 400

        def hacer(con):
            filas, cantidad = basedatos.listar(con, desde, hasta, producto, id_ctrl, pagina, por_pagina)
            paginas = max(1, (cantidad + por_pagina - 1) // por_pagina)
            return jsonify({
                "ok": True, "total": cantidad, "total_texto": formato.numero(cantidad),
                "pagina": pagina, "paginas": paginas, "por_pagina": por_pagina,
                "filas": [{
                    "nro": f["nro"], "ID": f["ID"], "cor": f["cor"], "Producto": f["Producto"],
                    "Peso": formato.kg(f["Peso"]), "Acum": formato.kg(f["Acum"]),
                    "FechaI": formato.fecha(f["FechaI"]), "FechaF": formato.fecha(f["FechaF"]),
                    "Turno": f["Turno"], "Estado": f["Estado"],
                    "guardado_en": formato.fecha_segundos(f["guardado_en"]),
                    "actualizado_en": formato.fecha_segundos(f["actualizado_en"]),
                    "correo": f["correo"] or None,
                } for f in filas],
            })
        return con_base(hacer)

    @app.get("/api/descargas")
    def api_descargas():
        def hacer(con):
            return jsonify({"ok": True, "descargas": [{
                "inicio": formato.fecha_segundos(d["inicio"]), "origen": d["origen"],
                "resultado": d["resultado"], "segundos": _num(d["segundos"]), "leidas": d["leidas"],
                "nuevas": d["nuevas"], "actualizadas": d["actualizadas"], "total": d["total"],
                "detalle": d["detalle"] or "",
            } for d in basedatos.descargas(con, 20)]})
        return con_base(hacer)

    @app.get("/api/correos")
    def api_correos():
        def hacer(con):
            return jsonify({"ok": True, "correos": [_correo_json(c) for c in basedatos.correos(con, 50)]})
        return con_base(hacer)

    return app


def servir():
    """Servicio registro1280-web (lo arranca systemd)."""
    import logging
    import sys
    from waitress import serve
    from . import config_web
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    ajustes = config_web.leer()
    app = crear_app(ajustes)
    logging.getLogger("registro1280").info("Web del registro de pesadas en el puerto %d", ajustes["puerto"])
    serve(app, host="0.0.0.0", port=ajustes["puerto"], threads=6, ident="registro1280")
