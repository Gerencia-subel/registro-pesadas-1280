"""Página web del registro de pesadas: consulta de pesadas y configuración.

Corre como el usuario de Linux registro1280web, que en la base solo puede leer y agregar
pedidos: las pesadas no se pueden editar ni borrar desde la web. Los cambios de
configuración (correo, bucle, controlador) y "guardar ahora" se piden con un pedido que
hace el servicio del correo, igual que los comandos de la terminal.
"""

import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import VERSION, acciones, basedatos, correo, formato
from .basedatos import ErrorBase
from .horario import leer_fecha, minutos_config
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


def _lista(texto):
    return [d.strip() for d in (texto or "").split(",") if d.strip()]


def _hora_config(texto):
    try:
        return formato.fecha(datetime.strptime(texto or "", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return ""


def _correo_json(c):
    tramo = correo.rango(c["desde"], c["hasta"]) if c["desde"] and c["hasta"] else "Prueba (sin tramo)"
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
        if request.endpoint in ("ingresar", "static"):
            return None
        if autorizado():
            if "csrf" not in session:
                session["csrf"] = secrets.token_hex(16)
            if request.method == "POST" and not hmac.compare_digest(
                    request.headers.get("X-CSRF-Token", ""), session["csrf"]):
                return jsonify({"ok": False, "error": "La página está desactualizada. Recargarla."}), 403
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
                session["csrf"] = secrets.token_hex(16)
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

    @app.get("/configuracion")
    def configuracion():
        return render_template("configuracion.html", usuario=session.get("usuario"), version=VERSION,
                               csrf=session.get("csrf", ""))

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

    @app.get("/api/configuracion")
    def api_configuracion():
        def hacer(con):
            cfg = basedatos.config(con)
            return jsonify({
                "ok": True,
                "ahora": formato.fecha(ahora()),
                "controlador": {"ip": cfg.get("ip", ""), "puerto": cfg.get("puerto", "")},
                "bucle": {"activo": cfg.get("bucle_activo") == "1", "minutos": cfg.get("bucle_minutos") or "5",
                          "servicio_ok": _vivo(cfg.get("latido"))},
                "correo": {
                    "activo": cfg.get("correo_activo") == "1", "minutos": str(minutos_config(cfg)),
                    "servicio_ok": _vivo(cfg.get("correo_latido")),
                    "cuenta": cfg.get("correo_cuenta", ""), "para": _lista(cfg.get("correo_para")),
                    "copia": _lista(cfg.get("correo_copia")),
                    "proximo": formato.fecha(leer_fecha(cfg.get("correo_proximo"))),
                    "desde": formato.fecha(leer_fecha(cfg.get("correo_desde"))),
                    "pendientes": basedatos.contar_pendientes(con) if cfg.get("correo_activo") == "1" else 0,
                },
                "pedidos": [{
                    "nro": p["nro"], "creado": formato.fecha_segundos(p["creado"]), "origen": p["origen"],
                    "tipo": acciones.TIPOS.get(p["tipo"], p["tipo"]), "estado": p["estado"],
                    "resultado": p["resultado"] or "",
                } for p in basedatos.ultimos_pedidos(con, 15)],
            })
        return con_base(hacer)

    @app.post("/api/pedidos")
    def api_crear_pedido():
        cuerpo = request.get_json(silent=True) or {}
        tipo = str(cuerpo.get("tipo") or "")
        if tipo not in acciones.TIPOS:
            return jsonify({"ok": False, "error": "Pedido desconocido."}), 400
        if tipo == "correo_cuenta":
            datos = json.dumps({"usuario": str(cuerpo.get("usuario") or "").strip(),
                                "clave": str(cuerpo.get("clave") or "")})
        elif tipo == "correo_destinatarios":
            datos = json.dumps({c: [str(x).strip() for x in (cuerpo.get(c) or []) if str(x).strip()]
                                for c in ("para", "copia")})
        else:
            datos = str(cuerpo.get("datos") or "").strip()
        if len(datos) > 6000:
            return jsonify({"ok": False, "error": "Demasiado texto (máximo 6000 caracteres)."}), 400

        def hacer(con):
            nro = basedatos.crear_pedido(con, tipo, datos, ahora(), origen_del_pedido())
            return jsonify({"ok": True, "nro": nro})
        return con_base(hacer)

    @app.get("/api/configuracion/clave")
    def api_clave():
        def hacer(con):
            return jsonify({"ok": True, "clave": basedatos.config(con).get("correo_clave", "")})
        return con_base(hacer)

    @app.get("/api/pedidos/<int:nro>")
    def api_pedido(nro):
        def hacer(con):
            p = basedatos.pedido(con, nro)
            if p is None:
                return jsonify({"ok": False, "error": "No existe ese pedido."}), 404
            return jsonify({"ok": True, "estado": p["estado"], "resultado": p["resultado"] or ""})
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
